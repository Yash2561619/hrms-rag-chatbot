"""Automated Policy Ingestion and Deletion Service.
Location: app/services/policy_sync_service.py
"""

import logging
import os
import tempfile
import boto3
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
import psycopg2
from psycopg2.extras import execute_values
from redis import Redis
from app.services.semantic_cache_service import clear_semantic_cache

# Invalidate cache when policies change
clear_semantic_cache()
from app.services.rag_service import load_indexes

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")

_embeddings_model = None


def get_embeddings_model():
    """Lazily load FastEmbed embeddings model."""
    global _embeddings_model
    if _embeddings_model is None:
        _embeddings_model = FastEmbedEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
    return _embeddings_model


def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=AWS_REGION,
    )


def invalidate_redis_semantic_cache():
    """Flushes all cached policy answers from Redis."""
    if not REDIS_URL:
        return
    try:
        r = Redis.from_url(REDIS_URL)
        keys = r.keys("semantic_cache:*")
        if keys:
            r.delete(*keys)
            logger.info(f"CACHE_INVALIDATED ✅ | Deleted {len(keys)} cached entries.")
    except Exception as e:
        logger.error(f"CACHE_INVALIDATION_ERROR | {e}")


def sync_new_policy_from_s3(s3_key: str) -> bool:
    """Downloads PDF from S3, generates embeddings, and inserts into PostgreSQL."""
    filename = os.path.basename(s3_key)
    logger.info(f"POLICY_INGEST_START | file={filename}")

    s3 = get_s3_client()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        # 1. Download file from S3
        s3.download_file(S3_BUCKET_NAME, s3_key, tmp_path)

        # 2. Extract & Chunk text
        loader = PyPDFLoader(tmp_path)
        pages = loader.load()

        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)
        chunks = splitter.split_documents(pages)

        if not chunks:
            logger.warning(f"NO_TEXT_CHUNKS_EXTRACTED | file={filename}")
            return False

        # Set 1-indexed page number and filename in metadata
        for chunk in chunks:
            chunk.metadata["source"] = filename
            if "page" in chunk.metadata:
                chunk.metadata["page"] = int(chunk.metadata["page"]) + 1

        # 3. Generate FastEmbed embeddings
        model = get_embeddings_model()
        texts = [c.page_content for c in chunks]
        vectors = list(model.embed_documents(texts))

        records = [
            (
                chunk.page_content,
                psycopg2.extras.Json(chunk.metadata),
                str(vec),
            )
            for chunk, vec in zip(chunks, vectors)
        ]

        # 4. Insert into PostgreSQL pgvector
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Remove previous versions of this policy if already present
        cur.execute(
            "DELETE FROM policy_vectors WHERE metadata->>'source' = %s;",
            (filename,)
        )

        execute_values(
            cur,
            """
            INSERT INTO policy_vectors (content, metadata, embedding) 
            VALUES %s;
            """,
            records,
        )
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"POLICY_INGEST_SUCCESS ✅ | Ingested {len(records)} chunks for {filename}")

        # 5. Invalidate stale cache & force reload in-memory BM25 index
        invalidate_redis_semantic_cache()
        load_indexes(force_reload=True)
        return True

    except Exception as e:
        logger.exception(f"POLICY_INGEST_FAILED | file={filename} | error={e}")
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def delete_policy_everywhere(filename: str) -> bool:
    """Deletes policy from S3, removes vectors from PostgreSQL, and clears Redis cache."""
    logger.info(f"POLICY_DELETE_START | file={filename}")
    s3 = get_s3_client()

    try:
        # 1. Delete from S3
        s3_key = f"policies/{filename}"
        try:
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
            logger.info(f"S3_OBJECT_DELETED | key={s3_key}")
        except Exception as s3_err:
            logger.warning(f"S3_DELETE_WARNING | {s3_err}")

        # 2. Delete from PostgreSQL pgvector
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM policy_vectors WHERE metadata->>'source' LIKE %s;",
            (f"%{filename}%",)
        )
        deleted_count = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()

        logger.info(f"PGVECTOR_CHUNKS_DELETED | rows_removed={deleted_count}")

        # 3. Clear Redis Cache & force reload BM25
        invalidate_redis_semantic_cache()
        load_indexes(force_reload=True)
        return True

    except Exception as e:
        logger.exception(f"POLICY_DELETE_FAILED | file={filename} | error={e}")
        return False