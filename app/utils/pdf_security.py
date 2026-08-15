import io
import re
from pypdf import PdfReader, PdfWriter


def generate_salary_pdf_password(employee_id: str, phone_number: str) -> str:
  """Generates password: Employee ID + Last 4 digits of Phone (e.g.

  EMP001@5888).
  """
  clean_id = (employee_id or "").strip().upper()
  digits_only = re.sub(r"\D", "", str(phone_number or ""))
  last_4_phone = digits_only[-4:] if len(digits_only) >= 4 else "1234"
  return f"{clean_id}@{last_4_phone}"


def protect_pdf_with_password(
    input_pdf_bytes: bytes, password: str
) -> io.BytesIO:
  """Encrypts raw PDF bytes using AES user password encryption."""
  reader = PdfReader(io.BytesIO(input_pdf_bytes))
  writer = PdfWriter()

  for page in reader.pages:
    writer.add_page(page)

  writer.encrypt(user_password=password)

  output_stream = io.BytesIO()
  writer.write(output_stream)
  output_stream.seek(0)
  return output_stream