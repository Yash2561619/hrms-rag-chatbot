"""Semantic Caching Service using Upstash Redis & FastEmbed Embeddings.

Stores and retrieves cached RAG policy answers based on Cosine Similarity >= 0.92.
Location: app/services/semantic_cache_service.py
"""

import json
import logging
import numpy as np
from fastembed import TextEmbedding
from upstash_redis import Redis
from config import Config

logger = logging.getLogger(__name__)

# Initialize Upstash Redis & Embedding Model
redis_client = Redis(
    url=Config.UPSTASH_REDIS_REST_URL,
    token=Config.UPSTASH_REDIS_REST_TOKEN
)

# Lightweight embedding model (<60 MB RAM)
embedder = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
CACHE_INDEX_KEY = "semantic_rag_cache_index"
SIMILARITY_THRESHOLD = 0.92  # 92% semantic similarity threshold


def _get_embedding(text: str) -> list[float]:
    """Generates normalized vector embedding."""
    embeddings = list(embedder.embed([text.lower().strip()]))
    vec = np.array(embeddings[0])
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def get_semantic_cached_answer(query: str) -> tuple[str | None, str | None]:
    """
    Checks Upstash Redis for a semantically similar previous question.
    Returns: (cached_response, cached_source_footer) or (None, None)
    """
    try:
        # Fetch all cached entries from Redis (stored as a JSON list)
        cached_raw = redis_client.get(CACHE_INDEX_KEY)
        if not cached_raw:
            return None, None

        cache_entries = json.loads(cached_raw)
        if not cache_entries:
            return None, None

        query_vec = np.array(_get_embedding(query))

        best_score = -1.0
        best_entry = None

        # Calculate Cosine Similarity across cached questions
        for entry in cache_entries:
            entry_vec = np.array(entry["vector"])
            # Dot product of normalized vectors equals Cosine Similarity
            score = float(np.dot(query_vec, entry_vec))
            
            if score > best_score:
                best_score = score
                best_entry = entry

        logger.info(f"SEMANTIC_CACHE_PROBE | score={best_score:.4f} | threshold={SIMILARITY_THRESHOLD}")

        if best_score >= SIMILARITY_THRESHOLD and best_entry:
            logger.info(f"SEMANTIC_CACHE_HIT ✅ | matched: '{best_entry['question']}' (score: {best_score:.3f})")
            return best_entry["response"], best_entry.get("citation_footer", "")

        return None, None

    except Exception as e:
        logger.warning(f"SEMANTIC_CACHE_READ_ERROR | {e}")
        return None, None


def save_semantic_cached_answer(query: str, response: str, citation_footer: str = ""):
    """Saves newly generated Gemini policy answer into the semantic cache."""
    try:
        # Don't cache fallback errors or empty responses
        if not response or "❌" in response:
            return

        query_vec = _get_embedding(query)
        
        new_entry = {
            "question": query.strip(),
            "vector": query_vec,
            "response": response.strip(),
            "citation_footer": citation_footer
        }

        cached_raw = redis_client.get(CACHE_INDEX_KEY)
        cache_entries = json.loads(cached_raw) if cached_raw else []

        # Keep the latest 200 most recent unique policy questions (prevents payload bloat)
        cache_entries.insert(0, new_entry)
        cache_entries = cache_entries[:200]

        # Save back to Upstash Redis with 30-day TTL (2592000 seconds)
        redis_client.set(CACHE_INDEX_KEY, json.dumps(cache_entries), ex=2592000)
        logger.info(f"SEMANTIC_CACHE_SAVED ✅ | query='{query[:30]}...'")

    except Exception as e:
        logger.warning(f"SEMANTIC_CACHE_WRITE_ERROR | {e}")