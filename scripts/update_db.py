"""
Database & Vector Index Update Script.
Extracts PDFs, chunks text, builds FAISS index with Gemini Embeddings,
and uploads faiss_index.zip to S3.

Location: scripts/update_db.py
"""

import hashlib
import json
import logging
import os
import zipfile
import boto3
import pdfplumber
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

REGISTRY_FILE = "faiss_index/pdf_registry.json"
S3_BUCKET = os.getenv("S3_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)


def get_pdf_hash(filepath: str) -> str:
  """Calculates SHA256 hash of a file to check for content updates."""
  hasher = hashlib.sha256()
  with open(filepath, "rb") as f:
    while chunk := f.read(8192):
      hasher.update(chunk)
  return hasher.hexdigest()


def get_pdf_version(filename: str) -> str:
  """Generates a basic version string for tracking."""
  return "v1.0"


def load_pdf_registry() -> dict:
  """Loads tracking registry for processed PDF files."""
  if os.path.exists(REGISTRY_FILE):
    try:
      with open(REGISTRY_FILE, "r") as f:
        return json.load(f)
    except Exception as e:
      logger.error(f"REGISTRY_LOAD_FAILED | error={e}")
  return {}


def save_pdf_registry(registry: dict) -> None:
  """Saves PDF registry metadata to disk."""
  os.makedirs("faiss_index", exist_ok=True)
  with open(REGISTRY_FILE, "w") as f:
    json.dump(registry, f, indent=4)


def zip_directory(src_dir: str, output_zip: str) -> None:
  """Compresses FAISS directory into a zip archive for S3 upload."""
  with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as ziph:
    for root, _, files in os.walk(src_dir):
      for file in files:
        file_path = os.path.join(root, file)
        arcname = os.path.relpath(file_path, src_dir)
        ziph.write(file_path, arcname)


def build_index(policy_folder: str = "uploads/policies"):
  """Reads PDFs from folder, generates FAISS index, and pushes faiss_index.zip to S3."""
  print("=" * 50)
  print("STARTING FAISS INDEX BUILDING PROCESS")
  print("=" * 50)

  if not os.path.exists(policy_folder) or not os.listdir(policy_folder):
    print(f"⚠️ Policy folder '{policy_folder}' is empty or does not exist.")
    return

  api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
  if not api_key:
    raise ValueError("GEMINI_API_KEY environment variable missing!")

  embeddings = GoogleGenerativeAIEmbeddings(
      model="models/gemini-embedding-001", google_api_key=api_key
  )

  splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80)

  all_chunks = []
  metadatas = []

  for filename in os.listdir(policy_folder):
    if filename.endswith(".pdf"):
      filepath = os.path.join(policy_folder, filename)
      print(f"📄 Processing {filename}...")

      extracted_text = ""
      try:
        with pdfplumber.open(filepath) as pdf:
          for page in pdf.pages:
            text = page.extract_text()
            if text:
              extracted_text += text + "\n"
      except Exception as e:
        print(f"❌ Failed to extract PDF {filename}: {e}")
        continue

      if extracted_text.strip():
        chunks = splitter.split_text(extracted_text)
        for chunk in chunks:
          all_chunks.append(chunk)
          metadatas.append({"source": filename})
        print(f"  ✔ Created {len(chunks)} chunks")

  if not all_chunks:
    print("⚠️ No valid text chunks generated.")
    return

  # Build FAISS Vector Database
  print("\n⚙️ Generating FAISS index using Gemini Embeddings...")
  vector_store = FAISS.from_texts(
      texts=all_chunks, embedding=embeddings, metadatas=metadatas
  )

  # Save FAISS locally
  output_dir = "faiss_index"
  vector_store.save_local(output_dir)
  print(f"✅ Local FAISS index saved to '{output_dir}/'")

  # Zip and Upload to S3
  zip_name = "faiss_index.zip"
  print(f"📦 Zipping '{output_dir}' to '{zip_name}'...")
  zip_directory(output_dir, zip_name)

  if S3_BUCKET:
    print(f"🚀 Uploading {zip_name} to S3 bucket '{S3_BUCKET}'...")
    s3.upload_file(zip_name, S3_BUCKET, zip_name)
    print("✅ FAISS index uploaded to S3 successfully!")

  # Cleanup local zip
  if os.path.exists(zip_name):
    os.remove(zip_name)


if __name__ == "__main__":
  build_index()