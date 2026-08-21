"""Centralized Observability Service using Langfuse Python SDK.
Supports dynamic multi-LLM tracing and SDK v2/v3 compatibility.
Location: app/services/telemetry_service.py

FIXES APPLIED:
1. ✅ Use LANGFUSE_BASE_URL (not LANGFUSE_HOST)
2. ✅ Guarantee flush() is called on app exit (atexit handler)
3. ✅ Validate initialization and early-return if disabled
4. ✅ Detailed logging for each step
"""

import os
import logging
import atexit
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Initialize client cleanly from environment variables
langfuse_client = None
langfuse_enabled = False

try:
    from langfuse import Langfuse

    # ✅ FIX #1: Correct environment variable name is LANGFUSE_BASE_URL
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if public_key and secret_key:
        try:
            langfuse_client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=base_url  # 'host' param accepts the base_url
            )
            langfuse_enabled = True
            logger.info(f"✅ LANGFUSE_INITIALIZED | host={base_url}")
        except Exception as e:
            logger.error(f"❌ LANGFUSE_INIT_ERROR | {e}")
            langfuse_client = None
            langfuse_enabled = False
    else:
        missing = []
        if not public_key:
            missing.append("LANGFUSE_PUBLIC_KEY")
        if not secret_key:
            missing.append("LANGFUSE_SECRET_KEY")
        logger.warning(f"⚠️ LANGFUSE_CREDENTIALS_MISSING | Missing: {', '.join(missing)}")
        langfuse_client = None

except ImportError as e:
    logger.error(f"❌ LANGFUSE_SDK_NOT_INSTALLED | pip install langfuse | Error: {e}")
    langfuse_client = None
except Exception as e:
    logger.error(f"❌ LANGFUSE_IMPORT_ERROR | {e}")
    langfuse_client = None


# ✅ FIX #2: Ensure flush happens on app exit (even if errors occur)
def _flush_langfuse_on_exit():
    """Guarantee pending traces are flushed before app shutdown."""
    if langfuse_client is None:
        return
    
    if not hasattr(langfuse_client, "flush"):
        logger.warning("⚠️ LANGFUSE_NO_FLUSH_METHOD")
        return
    
    try:
        logger.info("Flushing Langfuse traces on app exit...")
        langfuse_client.flush()
        logger.info("✅ LANGFUSE_FLUSHED_ON_EXIT")
    except Exception as e:
        logger.error(f"❌ LANGFUSE_FLUSH_FAILED_ON_EXIT | {e}")


atexit.register(_flush_langfuse_on_exit)


def trace_rag_interaction(
    user_id: str,
    session_id: str,
    query_text: str,
    response_text: str,
    latency_ms: float,
    cache_hit: bool,
    tokens_used: Dict[str, int],
    retrieved_chunks: Optional[List[str]] = None,
    hallucination_score: Optional[float] = None,
    model_name: str = "gemini-2.5-flash",
):
    """Creates a structured trace hierarchy for Gemini, Groq, or Cache hits.
    
    This function:
    - Returns immediately (no blocking I/O) if Langfuse is not enabled
    - Creates nested spans for retrieval, generation, and scoring
    - Flushes traces after creation to ensure delivery
    - Logs all success/failure states for debugging
    """
    
    # ✅ FIX #3: Early return if not enabled (graceful degradation)
    if not langfuse_enabled or not langfuse_client:
        logger.debug(f"LANGFUSE_DISABLED | Skipping trace for user={user_id}")
        return

    try:
        # Determine trace source for debugging
        source_tag = (
            "cache_hit"
            if cache_hit
            else ("groq_fallover" if "groq" in model_name.lower() else "gemini_primary")
        )

        trace_kwargs = {
            "name": "hr_policy_chat",
            "user_id": user_id,
            "session_id": session_id,
            "input": {"query": query_text},
            "output": {"response": response_text},
            "metadata": {
                "cache_hit": cache_hit,
                "latency_ms": round(latency_ms, 2),
                "model_used": model_name if not cache_hit else "semantic_cache",
                "chunks_count": len(retrieved_chunks) if retrieved_chunks else 0,
            },
            "tags": ["production", "whatsapp", source_tag],
        }

        # Create root trace (compatible with Langfuse v2 and v3)
        trace = None
        if hasattr(langfuse_client, "trace"):
            trace = langfuse_client.trace(**trace_kwargs)
        elif hasattr(langfuse_client, "create_trace"):
            trace = langfuse_client.create_trace(**trace_kwargs)

        if not trace:
            logger.warning(f"⚠️ TRACE_CREATION_FAILED | trace object is None | user={user_id}")
            return

        # ─────────────────────────────────────────────────────────────
        # 1. Retrieval Span (hybrid vector + BM25 search)
        # ─────────────────────────────────────────────────────────────
        if retrieved_chunks:
            try:
                span_kwargs = {
                    "name": "hybrid_vector_bm25_retrieval",
                    "input": {"query": query_text},
                    "output": {"chunks": retrieved_chunks},
                    "metadata": {"retrieved_count": len(retrieved_chunks)},
                }
                if hasattr(trace, "span"):
                    trace.span(**span_kwargs)
                elif hasattr(trace, "create_span"):
                    trace.create_span(**span_kwargs)
                logger.debug(f"✅ RETRIEVAL_SPAN_CREATED | chunks={len(retrieved_chunks)}")
            except Exception as e:
                logger.warning(f"⚠️ RETRIEVAL_SPAN_FAILED | {e}")

        # ─────────────────────────────────────────────────────────────
        # 2. Generation Observation (LLM inference call)
        # ─────────────────────────────────────────────────────────────
        if not cache_hit and model_name != "none":
            try:
                gen_kwargs = {
                    "name": "llm_generation",
                    "model": model_name,
                    "input": query_text,
                    "output": response_text,
                    "usage": {
                        "input": tokens_used.get("prompt_tokens", 0),
                        "output": tokens_used.get("completion_tokens", 0),
                        "total": tokens_used.get("total_tokens", 0),
                    },
                }
                if hasattr(trace, "generation"):
                    trace.generation(**gen_kwargs)
                elif hasattr(trace, "create_generation"):
                    trace.create_generation(**gen_kwargs)
                logger.debug(f"✅ GENERATION_OBSERVATION_CREATED | model={model_name}")
            except Exception as e:
                logger.warning(f"⚠️ GENERATION_OBSERVATION_FAILED | {e}")

        # ─────────────────────────────────────────────────────────────
        # 3. Cache Score (measure of cache hit effectiveness)
        # ─────────────────────────────────────────────────────────────
        try:
            score_kwargs = {
                "name": "cache_hit_rate",
                "value": 1.0 if cache_hit else 0.0,
                "comment": "Semantic Redis Cache Hit Indicator",
            }
            if hasattr(trace, "score"):
                trace.score(**score_kwargs)
            elif hasattr(trace, "create_score"):
                trace.create_score(**score_kwargs)
            logger.debug(f"✅ SCORE_CREATED | cache_hit={cache_hit}")
        except Exception as e:
            logger.warning(f"⚠️ SCORE_CREATION_FAILED | {e}")

        # ─────────────────────────────────────────────────────────────
        # ✅ FIX #2 (CRITICAL): Flush immediately after trace
        # ─────────────────────────────────────────────────────────────
        if hasattr(langfuse_client, "flush"):
            try:
                langfuse_client.flush()
                logger.info(f"✅ TRACE_FLUSHED | user={user_id} | model={model_name} | latency_ms={latency_ms:.1f}")
            except Exception as e:
                logger.error(f"❌ LANGFUSE_FLUSH_FAILED | {e}")
        else:
            logger.warning("⚠️ LANGFUSE_CLIENT_NO_FLUSH_METHOD")

    except Exception as e:
        # Catch-all for unexpected errors
        logger.exception(f"❌ TRACE_CREATION_FATAL_ERROR | {e}")