"""step1_extract.py.

Production-Ready PDF Text Extraction for HR RAG
-----------------------------------------------

Features:
* Fast digital PDF extraction with pdfplumber
* OCR fallback only for scanned PDFs
* Cross-platform support (Windows/Linux/Render)
* Safe error handling without hardcoded system paths
* Clean text return for chunking
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# OCR threshold: if extracted text length is below this, assume scanned PDF
OCR_THRESHOLD = 100


def extract_text_from_pdf(pdf_path: str) -> Tuple[str, bool]:
    """Extract text from a PDF with smart OCR fallback.

    Args:
        pdf_path: Path to PDF file

    Returns:
        (text, used_ocr)
    """
    pdf_file = Path(pdf_path)

    # Validation
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
        raise e

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

    # Return whatever OCR extracted, or fallback to any sparse digital text found
    final_text = ocr_text if ocr_text else text
    return final_text, True


def extract_text_ocr(pdf_path: str) -> str:
    """OCR extraction for scanned PDFs with cross-platform Poppler handling."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        logger.error(
            "OCR_DEPENDENCIES_MISSING | Missing pdf2image or pytesseract"
        )
        return ""

    try:
        logger.info("OCR_START")

        # Determine Poppler Path dynamically (Windows vs Linux/Render)
        poppler_dir = None
        win_poppler = r"C:\Program Files\poppler\Library\bin"
        if os.name == "nt" and os.path.exists(win_poppler):
            poppler_dir = win_poppler

        images = convert_from_path(
            pdf_path,
            dpi=200,
            first_page=1,
            last_page=5,  # Process first 5 pages for performance
            poppler_path=poppler_dir,
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
        logger.info(f"OCR_COMPLETE | total_chars={len(ocr_text)}")

        return ocr_text

    except Exception as e:
        logger.exception(
            f"OCR_ERROR | Failed to execute OCR (Check Poppler/Tesseract system installation): {str(e)}"
        )
        return ""


def extract_pdf_metadata(pdf_path: str) -> Dict[str, Any]:
    """Return useful PDF metadata."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return {
                "pages": len(pdf.pages),
                "metadata": pdf.metadata or {},
                "filename": Path(pdf_path).name,
            }
    except Exception as e:
        logger.error(f"METADATA_ERROR | error={str(e)}")
        return {}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )

    test_file = "uploads/policies/travel.pdf"

    if os.path.exists(test_file):
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
    else:
        print(f"Test file '{test_file}' not found.")