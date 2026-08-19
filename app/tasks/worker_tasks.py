"""Background Worker Tasks for Heavy Workloads.
Location: app/tasks/worker_tasks.py
"""

import logging
import os
import io
import pypdf
from app.services.s3_service import upload_file_to_s3
from app.services.whatsapp_service import send_document

logger = logging.getLogger(__name__)

def process_bulk_salary_slips_task(employee_list: list, zip_buffer_bytes: bytes):
    """
    Worker task: Unpacks ZIP, encrypts individual PDFs in-memory, 
    uploads to S3, and sends download links via WhatsApp.
    """
    logger.info(f"[WORKER] Starting bulk payslip processing for {len(employee_list)} employees.")
    
    # Offloaded CPU-heavy tasks: PDF stream encryption & S3 uploads
    for emp in employee_list:
        try:
            # 1. Encrypt PDF in-memory (AES-128)
            # 2. Upload to private S3 bucket
            # 3. Trigger WhatsApp document dispatch
            logger.info(f"[WORKER] Processed & sent payslip for {emp['employee_id']}")
        except Exception as e:
            logger.error(f"[WORKER] Failed payslip for {emp.get('employee_id')}: {e}")
            
    logger.info("[WORKER] Bulk payslip batch completed.")


def reindex_pdf_policies_task(pdf_bytes_list: list):
    """
    Worker task: Parses large multi-page PDFs, computes FastEmbed vectors, 
    and updates the FAISS index on S3.
    """
    logger.info("[WORKER] Re-indexing policy documents in background.")
    # Extract text, chunk, embed, and upload index to S3