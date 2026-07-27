"""HR Policy Knowledge Base Builder with Incremental Indexing & Gemini API

Embeddings.

Location: scripts/update_db.py (or update_db.py)

Uses Google Gemini API embeddings (gemini-embedding-001) to avoid ONNX/PyTorch
native crashes and keep memory usage below 100 MB on Render deployments.
"""

from datetime import datetime
import hashlib
import json
import logging
import os
import re
import sys
from typing import Any, Dict, Optional

# Prevent ChromaDB telemetry
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# Add project root to Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import chromadb
from google import genai
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.lazy_embedding import LazyEmbeddingFunction
from app.services.s3_service import download_policy_temp
from config import Config
from database import get_all_policy_files
from scripts.step1_extract import extract_text_from_pdf

api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
gemini_client = genai.Client(api_key=api_key) if api_key else None

logger = logging.getLogger(__name__)

POLICY_FOLDER = Config.POLICY_FOLDER

# Global collection instance variable
collection = None


def get_pdf_version(filename: str) -> str:
    """Extract version from PDF filename.

    Examples:
    - leave_policy_v1.pdf → "1.0"
    - leave_policy_v2.pdf → "2.0"
    - leave_policy.pdf → "1.0"
    """
    match = re.search(r"_v(\d+)", filename, re.IGNORECASE)
    if match:
        version_num = match.group(1)
        return f"{version_num}.0"
    return "1.0"


def get_pdf_hash(pdf_path: str) -> str:
    """Generate MD5 hash for a PDF file."""
    hash_md5 = hashlib.md5()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def load_pdf_registry() -> Dict[str, Any]:
    """Loads stored JSON registry tracking indexed PDFs."""
    registry_path = os.path.join("chroma_db", "pdf_registry.json")
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"REGISTRY_LOAD_FAILED | error={e}")
    return {}


def save_pdf_registry(registry: Dict[str, Any]) -> None:
    """Saves updated registry state to JSON."""
    registry_path = os.path.join("chroma_db", "pdf_registry.json")
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    logger.info(f"REGISTRY_SAVED | path={registry_path}")


def build_index():
    """Build Chroma index with versioning and incremental updating support.

    Uses Gemini API embeddings via LazyEmbeddingFunction for zero local RAM
    overhead and no ONNX dependencies.
    """
    global collection

    print("\n" + "=" * 80)
    print("Building HR Policy Knowledge Base with Gemini API Embeddings")
    print("=" * 80)

    # Use Gemini API embedding function
    ef = LazyEmbeddingFunction(model_name="gemini-embedding-001")

    client = chromadb.PersistentClient(path="chroma_db")

    # Get or create collection directly
    collection = client.get_or_create_collection(
        "hr_policies", embedding_function=ef
    )
    print("[INFO] Using or created ChromaDB collection 'hr_policies'")

    splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=40)

    # =========================================================================
    # STEP 1: Get current active PDFs in database & load local registry
    # =========================================================================
    registry = load_pdf_registry()

    current_pdfs = {}
    policy_records = {}

    rows = get_all_policy_files()

    # Safely handle both Dictionary rows (dict) and Tuple rows (tuple)
    for row in rows:
        if isinstance(row, dict):
            file_name = row.get("file_name")
            s3_key = row.get("s3_key")
            version = row.get("version", "1.0")
            file_hash = row.get("file_hash", "")
        else:
            file_name, s3_key, version, file_hash = row

        if file_name and s3_key:
            current_pdfs[file_name] = file_hash
            policy_records[file_name] = {
                "s3_key": s3_key,
                "version": version,
                "hash": file_hash,
            }

    print(f"\n[INFO] Current active PDFs in database: {len(current_pdfs)}")
    for pdf in current_pdfs:
        print(f"  • {pdf}")

    # =========================================================================
    # STEP 2: Get previous PDFs from Chroma metadata
    # =========================================================================
    all_chunks = collection.get()
    previous_pdfs = set()

    if all_chunks and all_chunks.get("metadatas"):
        for metadata in all_chunks["metadatas"]:
            if metadata and "source" in metadata:
                previous_pdfs.add(metadata["source"])

    print(f"\n[INFO] Previous PDFs found in ChromaDB: {len(previous_pdfs)}")
    for pdf in previous_pdfs:
        print(f"  • {pdf}")

    # =========================================================================
    # STEP 3: Remove chunks and registry entries for deleted PDFs
    # =========================================================================
    deleted_pdfs = previous_pdfs - set(current_pdfs.keys())

    if deleted_pdfs:
        print(
            f"\n[WARNING] Removing chunks for deleted PDFs: {len(deleted_pdfs)}"
        )
        for deleted_pdf in deleted_pdfs:
            print(f"  ❌ Removing vector chunks for: {deleted_pdf}")
            
            # Delete via source metadata filter directly from ChromaDB
            try:
                collection.delete(where={"source": deleted_pdf})
                print(f"    ✔ Successfully purged {deleted_pdf} from ChromaDB")
            except Exception as e:
                logger.warning(f"CHROMADB_DELETE_FAILED | file={deleted_pdf} | {e}")

            if deleted_pdf in registry:
                del registry[deleted_pdf]

    if not current_pdfs:
        print("[WARNING] No active policy records found in database.")
        save_pdf_registry(registry)
        return

    # =========================================================================
    # STEP 4: Process ONLY new or changed PDFs (Incremental Indexing)
    # =========================================================================
    total_chunks = 0
    updated_files = 0
    new_files = 0

    pdfs_to_process = []

    for filename, current_hash in current_pdfs.items():
        # Check if file missing from registry or hash changed
        if filename not in registry:
            # Additional check: Does Chroma already contain valid chunks?
            existing_chunks = collection.get(where={"source": filename})
            if existing_chunks and existing_chunks.get("ids"):
                # Register existing chunks without re-embedding
                registry[filename] = {
                    "hash": current_hash,
                    "chunks": len(existing_chunks["ids"]),
                    "updated_at": datetime.now().isoformat(),
                }
                logger.info(f"REGISTERED_EXISTING | file={filename}")
                continue
            
            pdfs_to_process.append(filename)
            new_files += 1
        elif registry[filename].get("hash") != current_hash:
            pdfs_to_process.append(filename)
            updated_files += 1

    if not pdfs_to_process:
        print("\n[INFO] ✅ No new or changed PDFs detected. Vector store is up to date.")
    else:
        print(f"\n[INFO] PDFs queued to process: {len(pdfs_to_process)}")
        for pdf in pdfs_to_process:
            print(f"  • {pdf}")

    # Process required PDFs with error guarding
    for filename in pdfs_to_process:
        s3_key = policy_records[filename]["s3_key"]
        print(f"\n[INFO] Downloading and processing: {filename} (key: {s3_key})")

        filepath = None
        try:
            # 1. Download temp file safely
            filepath = download_policy_temp(s3_key)

            if not filepath or not os.path.exists(filepath):
                print(f"  ❌ [ERROR] Could not download {filename} from S3. Skipping.")
                continue

            # 2. Extract text with Gemini API OCR fallback
            text, used_ocr = extract_text_from_pdf(
                filepath, gemini_client=gemini_client
            )

            if not text or len(text.strip()) == 0:
                print(f"  [WARNING] Failed to extract text from {filename}. Skipping.")
                continue

            print(f"  ✔ Extracted {len(text)} characters (Used OCR: {used_ocr})")

            # 3. Split into chunks
            chunks = splitter.split_text(text)
            if not chunks:
                print("  [WARNING] No chunks produced. Skipping.")
                continue

            # 4. Remove existing stale chunks if updating
            try:
                collection.delete(where={"source": filename})
            except Exception:
                pass

            # 5. Build IDs and metadata
            ids_list = [f"{filename}_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source": filename,
                    "version": policy_records[filename]["version"],
                    "upload_date": datetime.now().isoformat(),
                    "status": "active",
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "used_ocr": str(used_ocr),
                }
                for i in range(len(chunks))
            ]

            # 6. Upsert into ChromaDB
            collection.upsert(
                documents=chunks, ids=ids_list, metadatas=metadatas
            )

            total_chunks += len(chunks)
            print(f"  ✔ Upserted {len(chunks)} chunks for {filename}")

            # 7. Update registry record
            registry[filename] = {
                "hash": current_pdfs[filename],
                "chunks": len(chunks),
                "updated_at": datetime.now().isoformat(),
            }

        except Exception as e:
            # ERROR GUARD: Log error and keep running loop for remaining files
            print(f"  ❌ [ERROR] Failed to process {filename}: {str(e)}")
            logger.exception(f"INDEX_FILE_FAILED | file={filename}")
            continue

        finally:
            # Always clean up temporary files from local disk
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass

    save_pdf_registry(registry)

    # =========================================================================
    # STEP 5: Summary
    # =========================================================================
    print("\n" + "=" * 80)
    print("BUILD SUMMARY")
    print("=" * 80)
    print(f"New PDFs Indexed: {new_files}")
    print(f"Updated PDFs Re-indexed: {updated_files}")
    print(f"Deleted PDFs Purged: {len(deleted_pdfs)}")
    print(f"Total Chunks Processed This Run: {total_chunks}")
    print("=" * 80 + "\n")


def display_index_status():
    """Display current state of Chroma index."""
    global collection

    if collection is None:
        ef = LazyEmbeddingFunction(model_name="gemini-embedding-001")
        client = chromadb.PersistentClient(path="chroma_db")
        try:
            collection = client.get_or_create_collection(
                "hr_policies", embedding_function=ef
            )
        except Exception:
            print("[ERROR] Collection not initialized or empty")
            return

    all_chunks = collection.get()

    if not all_chunks or not all_chunks.get("metadatas"):
        print("[INFO] Collection is empty")
        return

    # Group by source
    sources = {}

    for metadata in all_chunks["metadatas"]:
        if not metadata:
            continue
        source = metadata.get("source", "unknown")
        if source not in sources:
            sources[source] = {
                "count": 0,
                "version": metadata.get("version", "unknown"),
                "status": metadata.get("status", "unknown"),
                "upload_date": metadata.get("upload_date", "unknown"),
            }
        sources[source]["count"] += 1

    # Display
    print("\n" + "=" * 80)
    print("CURRENT INDEX STATUS")
    print("=" * 80)

    for source, info in sorted(sources.items()):
        status_emoji = "✅" if info["status"] == "active" else "⚠️"
        print(f"{status_emoji} {source}")
        print(f"   Version: {info['version']}")
        print(f"   Chunks: {info['count']}")
        print(f"   Status: {info['status']}")
        print(f"   Date: {info['upload_date'][:10]}")
        print()

    total = sum(info["count"] for info in sources.values())
    print(f"Total Chunks Active in Chroma: {total}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    build_index()
    display_index_status()