"""Semantic Caching Service using Upstash Redis & FastEmbed Embeddings.

Stores and retrieves cached RAG policy answers based on Cosine Similarity >= 0.92.
Location: app/services/semantic_cache_service.py
"""

import json
import logging
import os
from typing import List, Optional, Tuple
from fastembed import TextEmbedding
import numpy as np
from upstash_redis import Redis

logger = logging.getLogger(__name__)

CACHE_INDEX_KEY = "semantic_rag_cache_index"
SIMILARITY_THRESHOLD = 0.92  # 92% semantic similarity threshold

_redis_client: Optional[Redis] = None
_embedder: Optional[TextEmbedding] = None


def get_redis_client() -> Optional[Redis]:
    """Lazily initializes and returns the Upstash Redis client."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    redis_url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("REDIS_URL")
    redis_token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

    if redis_url and redis_token:
        try:
            _redis_client = Redis(url=redis_url, token=redis_token)
            logger.info("UPSTASH_REDIS_CLIENT_INITIALIZED ✅")
        except Exception as e:
            logger.error(f"UPSTASH_REDIS_INIT_FAILED | {e}")
            _redis_client = None
    else:
        logger.warning("UPSTASH_REDIS_ENV_VARS_MISSING | Semantic Cache disabled")
        _redis_client = None

    return _redis_client


def get_embedder() -> TextEmbedding:
    """Lazily initializes and returns the FastEmbed TextEmbedding model."""
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    return _embedder


def _get_embedding(text: str) -> List[float]:
    """Generates normalized vector embedding."""
    embed_model = get_embedder()
    embeddings = list(embed_model.embed([text.lower().strip()]))
    vec = np.array(embeddings[0], dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def get_semantic_cached_answer(query: str) -> Tuple[Optional[str], Optional[str]]:
    """Checks Upstash Redis for a semantically similar previous question.

    Returns: (cached_response, cached_source_footer) or (None, None)
    """
    client = get_redis_client()
    if not client or not query.strip():
        return None, None

    try:
        cached_raw = client.get(CACHE_INDEX_KEY)
        if not cached_raw:
            return None, None

        if isinstance(cached_raw, list):
            cache_entries = cached_raw
        elif isinstance(cached_raw, str):
            cache_entries = json.loads(cached_raw)
        else:
            return None, None

        if not cache_entries:
            return None, None

        query_vec = np.array(_get_embedding(query), dtype=np.float32)

        best_score = -1.0
        best_entry = None

        for entry in cache_entries:
            if not isinstance(entry, dict) or "vector" not in entry:
                continue

            entry_vec = np.array(entry["vector"], dtype=np.float32)
            if entry_vec.shape != query_vec.shape:
                continue

            score = float(np.dot(query_vec, entry_vec))

            if score > best_score:
                best_score = score
                best_entry = entry

        logger.info(
            f"SEMANTIC_CACHE_PROBE | score={best_score:.4f} | threshold={SIMILARITY_THRESHOLD}"
        )

        if best_score >= SIMILARITY_THRESHOLD and best_entry:
            logger.info(
                f"SEMANTIC_CACHE_HIT ✅ | matched: '{best_entry.get('question')}' (score: {best_score:.3f})"
            )
            return best_entry.get("response"), best_entry.get("citation_footer", "")

        return None, None

    except Exception as e:
        logger.warning(f"SEMANTIC_CACHE_READ_ERROR | {e}")
        return None, None


def save_semantic_cached_answer(
    query: str, response: str, citation_footer: str = ""
):
    """Saves newly generated policy answer into the semantic cache."""
    client = get_redis_client()
    if not client or not response or "❌" in response or not query.strip():
        return

    try:
        query_vec = _get_embedding(query)

        new_entry = {
            "question": query.strip(),
            "vector": query_vec,
            "response": response.strip(),
            "citation_footer": citation_footer,
        }

        cached_raw = client.get(CACHE_INDEX_KEY)
        if cached_raw:
            if isinstance(cached_raw, list):
                cache_entries = cached_raw
            elif isinstance(cached_raw, str):
                cache_entries = json.loads(cached_raw)
            else:
                cache_entries = []
        else:
            cache_entries = []

        cache_entries.insert(0, new_entry)
        cache_entries = cache_entries[:200]  # Cap at 200 items

        client.set(CACHE_INDEX_KEY, json.dumps(cache_entries), ex=2592000)
        logger.info(f"SEMANTIC_CACHE_SAVED ✅ | query='{query[:30]}...'")

    except Exception as e:
        logger.warning(f"SEMANTIC_CACHE_WRITE_ERROR | {e}")


def clear_semantic_cache() -> bool:
    """Invalidates the entire semantic cache index on policy update or deletion."""
    client = get_redis_client()
    if not client:
        return False
    try:
        client.delete(CACHE_INDEX_KEY)
        logger.info("SEMANTIC_CACHE_CLEARED ✅ | Key removed from Redis")
        return True
    except Exception as e:
        logger.error(f"SEMANTIC_CACHE_CLEAR_ERROR | {e}")
        return False