"""
step1_extract.py
Production-Ready PDF Text Extraction for HR RAG
-----------------------------------------------

Features:
* Fast digital PDF extraction with pdfplumber
* OCR fallback only for scanned PDFs
* Proper logging and error handling
* No hardcoded machine-specific paths
* Returns clean text for chunking
"""

import logging
from pathlib import Path
from typing import Tuple, Dict, Any

import pdfplumber
POPPLER_PATH = r"C:\Program Files\poppler\Library\bin"

logger = logging.getLogger(__name__)

# OCR threshold: if extracted text is below this, assume scanned PDF
OCR_THRESHOLD = 100


def extract_text_from_pdf(pdf_path: str) -> Tuple[str, bool]:
    """
    Extract text from a PDF with smart OCR fallback.

    Args:
        pdf_path: Path to PDF file

    Returns:
        (text, used_ocr)

    Raises:
        FileNotFoundError: If PDF does not exist
        ValueError: If file is not a PDF
        Exception: If extraction fails
    """
    pdf_file = Path(pdf_path)

    # =====================================================
    # VALIDATION
    # =====================================================

    if not pdf_file.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if pdf_file.suffix.lower() != ".pdf":
        raise ValueError(f"File is not a PDF: {pdf_path}")

    # =====================================================
    # STEP 1: FAST DIGITAL EXTRACTION
    # =====================================================

    text = ""

    try:
        logger.info(f"PDF_EXTRACT_START | file={pdf_file.name}")

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
                except Exception as e:
                    logger.warning(
                        f"PDF_PAGE_FAILED | page={page_num} | error={str(e)}"
                    )
                    continue

        text = text.strip()

        logger.info(
            f"PDF_EXTRACT_DONE | file={pdf_file.name} | chars={len(text)}"
        )

    except Exception as e:
        logger.exception(
            f"PDF_EXTRACT_ERROR | file={pdf_file.name} | error={str(e)}"
        )
        raise

    # =====================================================
    # STEP 2: OCR FALLBACK ONLY IF NEEDED
    # =====================================================

    if len(text) >= OCR_THRESHOLD:
        logger.info(
            f"PDF_DIGITAL_DETECTED | file={pdf_file.name} | skipping OCR"
        )
        return text, False

    logger.warning(
        f"PDF_SCANNED_DETECTED | file={pdf_file.name} | chars={len(text)} | running OCR"
    )

    ocr_text = extract_text_ocr(pdf_path)

    return ocr_text, True


def extract_text_ocr(pdf_path: str) -> str:
    """
    OCR extraction for scanned PDFs.

    Requirements:
        pip install pdf2image pytesseract
        Install Tesseract OCR on the system.
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract

    except ImportError as e:
        logger.error(
            "OCR_DEPENDENCIES_MISSING | install: pdf2image pytesseract"
        )
        raise e

    try:
        logger.info("OCR_START")

        # Faster settings for production
        images = convert_from_path(
            pdf_path,
            dpi=200,          # Good quality + faster than 300
            first_page=1,
            last_page=5 
                # OCR first 5 pages for performance
        )

        ocr_text = ""

        for i, image in enumerate(images, 1):
            try:
                page_text = pytesseract.image_to_string(image)

                if page_text:
                    ocr_text += page_text + "\n"

                logger.info(
                    f"OCR_PAGE_DONE | page={i} | chars={len(page_text)}"
                )

            except Exception as e:
                logger.warning(
                    f"OCR_PAGE_FAILED | page={i} | error={str(e)}"
                )
                continue

        ocr_text = ocr_text.strip()

        logger.info(
            f"OCR_COMPLETE | total_chars={len(ocr_text)}"
        )

        return ocr_text

    except Exception as e:
        logger.exception(f"OCR_ERROR | error={str(e)}")
        return ""


# =====================================================
# PDF METADATA EXTRACTION
# =====================================================

def extract_pdf_metadata(pdf_path: str) -> Dict[str, Any]:
    """Return useful PDF metadata."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return {
                "pages": len(pdf.pages),
                "metadata": pdf.metadata or {},
                "filename": Path(pdf_path).name
            }

    except Exception as e:
        logger.error(f"METADATA_ERROR | error={str(e)}")
        return {}


# =====================================================
# MODULE TEST BLOCK
# =====================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    test_file = "uploads/policies/travel.pdf"

    try:
        extracted_text, used_ocr = extract_text_from_pdf(test_file)

        print("\n" + "=" * 60)
        print("EXTRACTION RESULT")
        print("=" * 60)
        print(f"Used OCR: {used_ocr}")
        print(f"Characters: {len(extracted_text)}")
        print(f"Preview:\n{extracted_text[:300]}")

    except Exception as err:
        print(f"ERROR: {err}")