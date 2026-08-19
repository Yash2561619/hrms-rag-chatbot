import io
import logging
import os
import zipfile
from datetime import datetime
from app.services.s3_service import upload_salary_to_s3
from app.utils.pdf_security import (
    generate_salary_pdf_password,
    protect_pdf_with_password,
)
from database import get_employee, log_activity, save_salary_slip

logger = logging.getLogger(__name__)


def process_bulk_salary_slips_job(zip_bytes: bytes):
  """Background task: Unpacks ZIP, encrypts each PDF, and uploads to S3."""
  success_count = 0
  skipped_count = 0

  try:
    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as z:
      for file_info in z.infolist():
        filename = os.path.basename(file_info.filename)
        if (
            not filename.lower().endswith('.pdf')
            or filename.startswith('.')
            or file_info.is_dir()
        ):
          continue

        parts = filename[:-4].split('_')
        if len(parts) != 3:
          skipped_count += 1
          continue

        emp_id, month_str, year_str = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
        )

        try:
          year = int(year_str)
          month = (
              int(month_str)
              if month_str.isdigit()
              else datetime.strptime(month_str[:3].title(), '%b').month
          )
        except (ValueError, IndexError):
          skipped_count += 1
          continue

        employee = get_employee(emp_id)
        if not employee:
          skipped_count += 1
          continue

        phone_num = (
            employee.get('whatsapp') or employee.get('phone')
            if isinstance(employee, dict)
            else (employee[5] if len(employee) > 5 else employee[2])
        )

        pdf_password = generate_salary_pdf_password(
            emp_id, phone_num or '0000'
        )
        raw_bytes = z.read(file_info.filename)
        encrypted_stream = protect_pdf_with_password(raw_bytes, pdf_password)

        s3_key = upload_salary_to_s3(encrypted_stream, filename)
        save_salary_slip(emp_id, month, year, s3_key)
        success_count += 1

    log_activity(
        f'📦 Bulk salary job finished: {success_count} uploaded,'
        f' {skipped_count} skipped'
    )
    logger.info(
        f'[RQ_WORKER] Bulk salary complete: {success_count} success,'
        f' {skipped_count} skipped'
    )

  except Exception as e:
    logger.exception(f'[RQ_WORKER] Bulk salary task failed: {e}')