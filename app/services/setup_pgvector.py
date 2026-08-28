"""Database Setup and Vector Migration Script for pgvector from AWS S3.
Location: app/services/setup_pgvector.py
"""

import os
import sys

# Ensure root directory is in sys.path when script is executed directly
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import tempfile
import boto3
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
import psycopg2
from psycopg2.extras import execute_values

from app.services.semantic_cache_service import clear_semantic_cache

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")
S3_POLICY_PREFIX = os.getenv("S3_POLICY_PREFIX", "policies/")

if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL is missing from your .env file.")
    sys.exit(1)

if not S3_BUCKET_NAME:
    print("❌ ERROR: S3_BUCKET_NAME (or AWS_BUCKET_NAME) is missing from your .env file.")
    sys.exit(1)

# -------------------------------------------------------------------------
# 1. Connect to PostgreSQL and Configure pgvector
# -------------------------------------------------------------------------
print("🔌 Connecting to PostgreSQL...")
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("⚙️  Enabling pgvector extension...")
cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

print("⚙️  Creating policy_vectors table...")
cur.execute("""
CREATE TABLE IF NOT EXISTS policy_vectors (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    metadata JSONB,
    embedding vector(384)
);
""")

print("⚙️  Creating HNSW index...")
cur.execute("""
CREATE INDEX IF NOT EXISTS policy_vectors_hnsw_idx 
ON policy_vectors USING hnsw (embedding vector_cosine_ops);
""")
conn.commit()

# -------------------------------------------------------------------------
# 2. Download Policy PDFs from AWS S3 into a Temporary Directory
# -------------------------------------------------------------------------
print(f"☁️  Connecting to S3 Bucket: {S3_BUCKET_NAME} ({AWS_REGION})...")
s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
)

with tempfile.TemporaryDirectory() as temp_dir:
    print(f"📥 Fetching policy files from S3 path '{S3_POLICY_PREFIX}'...")
    
    response = s3_client.list_objects_v2(
        Bucket=S3_BUCKET_NAME,
        Prefix=S3_POLICY_PREFIX
    )
    
    contents = response.get("Contents", [])
    if not contents and S3_POLICY_PREFIX:
        print(f"ℹ️  No files under prefix '{S3_POLICY_PREFIX}', checking bucket root...")
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET_NAME)
        contents = response.get("Contents", [])

    downloaded_count = 0
    for obj in contents:
        key = obj["Key"]
        if key.lower().endswith(".pdf"):
            filename = os.path.basename(key)
            if not filename:
                continue
            local_path = os.path.join(temp_dir, filename)
            s3_client.download_file(S3_BUCKET_NAME, key, local_path)
            downloaded_count += 1
            print(f"   ⬇️ Downloaded: {filename}")

    if downloaded_count == 0:
        print(f"⚠️  No PDF files found in S3 bucket '{S3_BUCKET_NAME}'.")
        cur.close()
        conn.close()
        sys.exit(0)

    # -------------------------------------------------------------------------
    # 3. Load & Chunk Downloaded PDFs
    # -------------------------------------------------------------------------
    print(f"📄 Processing {downloaded_count} PDF documents...")
    loader = PyPDFDirectoryLoader(temp_dir)
    raw_docs = loader.load()

    # Normalize page numbers to 1-indexed for citations
    for doc in raw_docs:
        if "page" in doc.metadata:
            doc.metadata["page"] = int(doc.metadata["page"]) + 1
        if "source" in doc.metadata:
            doc.metadata["source"] = os.path.basename(doc.metadata["source"])

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)
    chunks = splitter.split_documents(raw_docs)
    print(f"🧩 Created {len(chunks)} text chunks.")

    # -------------------------------------------------------------------------
    # 4. Generate Embeddings using FastEmbed
    # -------------------------------------------------------------------------
    print("⚡ Generating embeddings with FastEmbed (all-MiniLM-L6-v2)...")
    embeddings_model = FastEmbedEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )
    texts = [c.page_content for c in chunks]
    vectors = list(embeddings_model.embed_documents(texts))

    records = []
    for chunk, vec in zip(chunks, vectors):
        records.append((
            chunk.page_content,
            psycopg2.extras.Json(chunk.metadata),
            str(vec),
        ))

    # -------------------------------------------------------------------------
    # 5. Insert Records into PostgreSQL policy_vectors Table
    # -------------------------------------------------------------------------
    print("💾 Storing vectors into PostgreSQL pgvector...")
    cur.execute("TRUNCATE TABLE policy_vectors;")
    execute_values(
        cur,
        """
        INSERT INTO policy_vectors (content, metadata, embedding) 
        VALUES %s;
        """,
        records,
    )
    conn.commit()

# Clear outdated semantic cache
clear_semantic_cache()

print(f"✅ SUCCESS: Ingested {len(records)} chunks from S3 directly into PostgreSQL pgvector!")

cur.close()
conn.close()