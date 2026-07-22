import os
import re
import logging
import hashlib
import json

from datetime import datetime
from typing import Optional

# Disable ChromaDB telemetry before importing chromadb
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import Config
from scripts.step1_extract import extract_text_from_pdf

logger = logging.getLogger(__name__)

POLICY_FOLDER = Config.POLICY_FOLDER

# Global collection instance variable
collection = None


def get_pdf_version(filename: str) -> str:
    """
    Extract version from PDF filename.

    Examples:
    - leave_policy_v1.pdf → "1.0"
    - leave_policy_v2.pdf → "2.0"
    - leave_policy.pdf → "1.0"
    """
    match = re.search(r'_v(\d+)', filename, re.IGNORECASE)
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


def load_pdf_registry():
    registry_path = os.path.join("chroma_db", "pdf_registry.json")

    if os.path.exists(registry_path):
        with open(registry_path, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def save_pdf_registry(registry):
    registry_path = os.path.join("chroma_db", "pdf_registry.json")

    os.makedirs(os.path.dirname(registry_path), exist_ok=True)

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)

    logger.info(f"REGISTRY_SAVED | path={registry_path}")

def build_index():
    """
    Build Chroma index with versioning and incremental updating support.
    
    Features:
    - Keep multiple PDF versions/track version info in metadata
    - Remove old chunks & registry entries when a PDF is deleted
    - Only re-process new or modified PDFs (Hash-based incremental indexing)
    """
    global collection

    print("\n" + "=" * 80)
    print("Building HR Policy Knowledge Base with Versioning")
    print("=" * 80)

    client = chromadb.PersistentClient(path="chroma_db")

    # Get or create collection
    try:
        collection = client.get_collection("hr_policies")
        print("[INFO] Using existing collection (preserving old chunks)")
    except Exception:
        collection = client.create_collection("hr_policies")
        print("[INFO] Created new collection")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=300,
        chunk_overlap=40
    )

    # =========================================================================
    # STEP 1: Get current PDFs in folder & load local registry
    # =========================================================================

    if not os.path.exists(POLICY_FOLDER):
        print(f"[ERROR] Policy folder '{POLICY_FOLDER}' does not exist.")
        return

    current_pdfs = {
        f: get_pdf_hash(os.path.join(POLICY_FOLDER, f))
        for f in os.listdir(POLICY_FOLDER)
        if f.lower().endswith(".pdf")
    }
    registry = load_pdf_registry()

    print(f"\n[INFO] Current PDFs in folder: {len(current_pdfs)}")
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

    print(f"\n[INFO] Previous PDFs in Chroma: {len(previous_pdfs)}")
    for pdf in previous_pdfs:
        print(f"  • {pdf}")

    # =========================================================================
    # STEP 3: Remove chunks and registry entries for deleted PDFs
    # =========================================================================

    deleted_pdfs = previous_pdfs - set(current_pdfs.keys())

    if deleted_pdfs:
        print(f"\n[WARNING] Removing chunks for deleted PDFs: {len(deleted_pdfs)}")
        for deleted_pdf in deleted_pdfs:
            print(f"  ❌ Removing: {deleted_pdf}")
            chunks_to_delete = collection.get(where={"source": deleted_pdf})
            
            if chunks_to_delete and chunks_to_delete.get("ids"):
                collection.delete(ids=chunks_to_delete["ids"])
                print(f"     Deleted {len(chunks_to_delete['ids'])} chunks from Chroma")
            
            # Clean up local registry tracking if present
            if deleted_pdf in registry:
                del registry[deleted_pdf]

    if not current_pdfs:
        print("[WARNING] No policy PDFs found in target folder.")
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
        if filename not in registry:
            # Brand new PDF
            pdfs_to_process.append(filename)
            new_files += 1
        elif registry[filename].get('hash') != current_hash:
            # Existing PDF content has changed
            pdfs_to_process.append(filename)
            updated_files += 1

    if not pdfs_to_process:
        print('\n[INFO] ✅ No new or changed PDFs detected.')
    else:
        print(f'\n[INFO] PDFs to process: {len(pdfs_to_process)}')
        for pdf in pdfs_to_process:
            print(f'  • {pdf}')

    # Process required PDFs
    for filename in pdfs_to_process:
        filepath = os.path.join(POLICY_FOLDER, filename)
        print(f'\n[INFO] Processing: {filename}')

        try:
            # Extract text
            text, used_ocr = extract_text_from_pdf(filepath)

            if not text or len(text.strip()) == 0:
                print('  [WARNING] OCR/Extraction failed or empty document. Skipping.')
                continue

            print(f'  ✔ Extracted {len(text)} characters (Used OCR: {used_ocr})')

            # Split into chunks
            chunks = splitter.split_text(text)

            if not chunks:
                print('  [WARNING] No chunks produced. Skipping.')
                continue

            # Remove old chunks only if this PDF is being updated
            existing = collection.get(where={'source': filename})
            existing_ids = existing.get('ids', []) if existing else []

            if existing_ids:
                print(f'  🔄 UPDATING: Removing {len(existing_ids)} old chunks')
                collection.delete(ids=existing_ids)
            else:
                print('  ✨ NEW: Adding new PDF')

            # Generate IDs and metadata
            ids_list = [f'{filename}_{i}' for i in range(len(chunks))]

            metadatas = [
                {
                    'source': filename,
                    'version': get_pdf_version(filename),
                    'upload_date': datetime.now().isoformat(),
                    'status': 'active',
                    'chunk_index': i,
                    'total_chunks': len(chunks),
                    'used_ocr': str(used_ocr)
                }
                for i in range(len(chunks))
            ]

            # Upsert chunks into ChromaDB
            collection.upsert(
                documents=chunks,
                ids=ids_list,
                metadatas=metadatas
            )

            total_chunks += len(chunks)
            print(f'  ✔ Upserted {len(chunks)} chunks')

            # Update registry data for current file
            registry[filename] = {
                'hash': current_pdfs[filename],
                'chunks': len(chunks),
                'updated_at': datetime.now().isoformat()
            }

        except Exception as e:
            print(f'  [ERROR] Failed to process {filename}: {str(e)}')
            continue

    # Save local registry state
    save_pdf_registry(registry)

    # =========================================================================
    # STEP 5: Summary
    # =========================================================================

    print("\n" + "=" * 80)
    print("BUILD SUMMARY")
    print("=" * 80)
    print(f"New PDFs: {new_files}")
    print(f"Updated PDFs: {updated_files}")
    print(f"Deleted PDFs: {len(deleted_pdfs)}")
    print(f"Total Chunks Processed This Run: {total_chunks}")
    print("=" * 80 + "\n")


def display_index_status():
    """
    Display current state of Chroma index.
    Show what's indexed and how many chunks per file.
    """
    global collection

    if collection is None:
        client = chromadb.PersistentClient(path="chroma_db")
        try:
            collection = client.get_collection("hr_policies")
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
                "upload_date": metadata.get("upload_date", "unknown")
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
    print(f"Total Chunks: {total}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    build_index()
    display_index_status()