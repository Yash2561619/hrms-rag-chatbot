"""step1_extract.py.

Production-Ready PDF Text Extraction for HR RAG
-----------------------------------------------

Features:
* Fast digital PDF extraction with pdfplumber (0-5 MB RAM)
* Memory-safe OCR fallback via Gemini 2.5 Flash Files API (~0 MB Render RAM)
* Cross-platform support (Windows/Linux/Render)
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


def extract_text_from_pdf(pdf_path: str, gemini_client: Any = None) -> Tuple[str, bool]:
    """Extract text from a PDF with smart Gemini Cloud OCR fallback.

    Args:
        pdf_path: Path to PDF file
        gemini_client: Initialized google.genai Client instance for API OCR

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
    # STEP 2: OCR FALLBACK (VIA GEMINI CLOUD API)
    # =====================================================
    if len(text) >= OCR_THRESHOLD:
        logger.info(
            f"PDF_DIGITAL_DETECTED | file={pdf_file.name} | skipping OCR"
        )
        return text, False

    logger.warning(
        f"PDF_SCANNED_DETECTED | file={pdf_file.name} | chars={len(text)} | Offloading OCR to Gemini API"
    )

    ocr_text = extract_text_ocr_gemini(pdf_path, gemini_client)

    # Return whatever Gemini OCR extracted, or fallback to any sparse digital text found
    final_text = ocr_text if ocr_text else text
    return final_text, True


def extract_text_ocr_gemini(pdf_path: str, gemini_client: Any = None) -> str:
    """Zero-RAM OCR extraction using Gemini 2.5 Flash Files API.

    Offloads page rendering and text recognition completely to Google's Cloud.
    """
    if not gemini_client:
        logger.error("GEMINI_CLIENT_MISSING | Cannot perform OCR without Gemini client.")
        return ""

    uploaded_file = None
    try:
        logger.info(f"GEMINI_OCR_START | file={Path(pdf_path).name}")

        # 1. Stream file bytes to Google Cloud (uses ~0 MB server RAM)
        uploaded_file = gemini_client.files.upload(file=pdf_path)

        prompt = (
            "Extract all readable text from this PDF document accurately. "
            "Preserve section headings, bullet points, and table structures. "
            "Output ONLY the plain extracted text without conversational filler."
        )

        # 2. Multimodal transcription on Google's infrastructure
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[uploaded_file, prompt]
        )

        ocr_text = response.text.strip() if response.text else ""
        logger.info(f"GEMINI_OCR_COMPLETE | total_chars={len(ocr_text)}")

        return ocr_text

    except Exception as e:
        logger.exception(
            f"GEMINI_OCR_ERROR | Failed to execute Gemini API OCR: {str(e)}"
        )
        return ""

    finally:
        # 3. Always clean up temporary file from Google Cloud storage
        if uploaded_file and hasattr(uploaded_file, "name"):
            try:
                gemini_client.files.delete(name=uploaded_file.name)
                logger.info("GEMINI_TEMP_FILE_CLEANED")
            except Exception as clean_err:
                logger.warning(f"GEMINI_CLEANUP_FAILED | error={clean_err}")


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

    # Optional: Initialize Gemini client for local testing
    from google import genai
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if api_key else None

    if os.path.exists(test_file):
        try:
            extracted_text, used_ocr = extract_text_from_pdf(test_file, gemini_client=client)
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