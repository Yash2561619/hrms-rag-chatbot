"""Centralized Observability Service using Langfuse Python SDK.
Location: app/services/telemetry_service.py
"""

import os
import logging
import atexit
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

langfuse_client = None
langfuse_enabled = False

try:
    from langfuse import Langfuse

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host_url = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com"

    if public_key and secret_key:
        try:
            # Compatible with both host and base_url parameters
            langfuse_client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host_url
            )
            langfuse_enabled = True
            logger.info(f"✅ LANGFUSE_INITIALIZED | host={host_url}")
        except Exception as e:
            logger.error(f"❌ LANGFUSE_INIT_ERROR | {e}")
            langfuse_client = None
            langfuse_enabled = False
    else:
        logger.warning("⚠️ LANGFUSE_CREDENTIALS_MISSING | Public or Secret key not found in env")
        langfuse_client = None
except ImportError as e:
    logger.error(f"❌ LANGFUSE_SDK_NOT_INSTALLED | {e}")
    langfuse_client = None
except Exception as e:
    logger.error(f"❌ LANGFUSE_IMPORT_ERROR | {e}")
    langfuse_client = None


def _flush_langfuse_on_exit():
    """Guarantee pending traces are flushed before app shutdown."""
    if langfuse_client and hasattr(langfuse_client, "flush"):
        try:
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
    """Creates a structured trace hierarchy for Gemini, Groq, or Cache hits."""
    if not langfuse_enabled or not langfuse_client:
        return

    try:
        source_tag = (
            "cache_hit"
            if cache_hit
            else ("groq_failover" if "groq" in model_name.lower() or "gpt-oss" in model_name.lower() or "qwen" in model_name.lower() else "gemini_primary")
        )

        trace_params = {
            "name": "hr_policy_query",
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

        # Initialize trace across SDK versions
        trace = None
        if hasattr(langfuse_client, "trace"):
            trace = langfuse_client.trace(**trace_params)
        elif hasattr(langfuse_client, "create_trace"):
            trace = langfuse_client.create_trace(**trace_params)

        if not trace:
            # In some SDK v3 setups, direct span creation without root trace object is used
            logger.debug("TRACE_OBJ_NONE_FALLBACK")
            return

        # 1. Log Retrieval Span
        if retrieved_chunks and hasattr(trace, "span"):
            try:
                trace.span(
                    name="hybrid_faiss_bm25_retrieval",
                    input={"query": query_text},
                    output={"chunks": retrieved_chunks[:4]},
                    metadata={"retrieved_count": len(retrieved_chunks)},
                )
            except Exception as span_err:
                logger.debug(f"RETRIEVAL_SPAN_NOTICE | {span_err}")

        # 2. Log Generation Event (LLM Inference)
        if not cache_hit and model_name != "none" and hasattr(trace, "generation"):
            try:
                trace.generation(
                    name="llm_policy_generation",
                    model=model_name,
                    input=query_text,
                    output=response_text,
                    usage={
                        "input": tokens_used.get("prompt_tokens", 0),
                        "output": tokens_used.get("completion_tokens", 0),
                        "total": tokens_used.get("total_tokens", 0),
                    },
                )
            except Exception as gen_err:
                logger.debug(f"GENERATION_SPAN_NOTICE | {gen_err}")

        # 3. Log Score Metric
        if hasattr(trace, "score"):
            try:
                trace.score(
                    name="cache_hit",
                    value=1.0 if cache_hit else 0.0,
                    comment="Semantic Redis Cache Hit Indicator",
                )
            except Exception as score_err:
                logger.debug(f"SCORE_NOTICE | {score_err}")

        # 4. Immediate Flush to send trace without waiting
        if hasattr(langfuse_client, "flush"):
            langfuse_client.flush()
            logger.info(f"✅ TRACE_LOGGED_LANGFUSE | user={user_id} | model={model_name} | latency={latency_ms:.1f}ms")

    except Exception as e:
        logger.warning(f"TRACE_CREATION_FAILED | {e}")