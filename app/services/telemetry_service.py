"""Centralized Observability Service using Langfuse Python SDK.

Supports modern Langfuse v3/v4 OpenTelemetry context model and legacy fallbacks.
Location: app/services/telemetry_service.py
"""

import atexit
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

langfuse_client = None
langfuse_enabled = False

# ---------------------------------------------------------------------------
# Langfuse Client Initialization
# ---------------------------------------------------------------------------
public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
secret_key = os.getenv("LANGFUSE_SECRET_KEY")
host_url = (
    os.getenv("LANGFUSE_BASE_URL")
    or os.getenv("LANGFUSE_HOST")
    or "https://cloud.langfuse.com"
)

if public_key and secret_key:
    try:
        from langfuse import Langfuse

        try:
            langfuse_client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=host_url,
            )
        except TypeError:
            langfuse_client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host_url,
            )

        langfuse_enabled = True
        logger.info(f"✅ LANGFUSE_INITIALIZED | host={host_url}")
    except Exception as e:
        logger.error(f"❌ LANGFUSE_INIT_ERROR | {e}")
        langfuse_client = None
        langfuse_enabled = False
else:
    logger.warning("⚠️ LANGFUSE_DISABLED | LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY missing.")


def _async_flush():
    """Background flush to avoid blocking HTTP/webhook threads."""
    if langfuse_client and hasattr(langfuse_client, "flush"):
        try:
            langfuse_client.flush()
        except Exception as e:
            logger.debug(f"LANGFUSE_FLUSH_ERROR | {e}")


def _flush_langfuse_on_exit():
    """Guarantee pending traces are flushed before application termination."""
    if langfuse_client and hasattr(langfuse_client, "flush"):
        try:
            langfuse_client.flush()
            logger.info("✅ LANGFUSE_FLUSHED_ON_EXIT")
        except Exception as e:
            logger.debug(f"LANGFUSE_EXIT_FLUSH_NOTICE | {e}")


atexit.register(_flush_langfuse_on_exit)


# ---------------------------------------------------------------------------
# Main RAG Tracing Function
# ---------------------------------------------------------------------------
def trace_rag_interaction(
    user_id: str,
    session_id: str,
    query_text: str,
    response_text: str,
    latency_ms: float,
    cache_hit: bool,
    tokens_used: Optional[Dict[str, int]] = None,
    retrieved_chunks: Optional[List[str]] = None,
    hallucination_score: Optional[float] = None,
    model_name: str = "gemini-2.5-flash",
    metadata: Optional[Dict[str, Any]] = None,
):
    """Creates a structured trace hierarchy for Gemini, Groq, or Cache hits."""
    if not langfuse_enabled or not langfuse_client:
        return

    tokens = tokens_used or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    meta = metadata or {}
    meta.update({
        "cache_hit": str(cache_hit),
        "latency_ms": str(round(latency_ms, 2)),
        "chunks_count": str(len(retrieved_chunks)) if retrieved_chunks else "0",
    })

    source_tag = (
        "cache_hit"
        if cache_hit
        else ("groq_failover" if any(k in model_name.lower() for k in ["groq", "gpt-oss", "qwen", "compound"]) else "gemini_primary")
    )
    tags = ["production", "whatsapp", source_tag]

    try:
        # -------------------------------------------------------------------
        # Modern OpenTelemetry/Observation API (Langfuse v3 & v4)
        # -------------------------------------------------------------------
        if hasattr(langfuse_client, "start_as_current_observation"):
            with langfuse_client.start_as_current_observation(
                as_type="span",
                name="whatsapp_rag_query",
                input={"query": query_text},
            ) as root_span:
                root_span.update(
                    output={"response": response_text},
                    metadata=meta,
                )

                # Set user & session attributes if helper exists
                try:
                    from langfuse import propagate_attributes
                    with propagate_attributes(
                        user_id=str(user_id),
                        session_id=str(session_id),
                        tags=tags,
                    ):
                        pass
                except Exception:
                    pass

                # 1. Retrieval Span
                if not cache_hit and retrieved_chunks:
                    with langfuse_client.start_as_current_observation(
                        as_type="span",
                        name="hybrid_pgvector_bm25_retrieval",
                        input={"query": query_text},
                    ) as ret_span:
                        ret_span.update(
                            output={"chunks": retrieved_chunks[:4]},
                            metadata={"retrieved_count": str(len(retrieved_chunks))},
                        )

                # 2. LLM Generation Observation
                if not cache_hit and model_name != "none":
                    with langfuse_client.start_as_current_observation(
                        as_type="generation",
                        name="llm_policy_generation",
                        input=query_text,
                        model=model_name,
                    ) as gen_span:
                        gen_span.update(
                            output=response_text,
                            usage={
                                "input": tokens.get("prompt_tokens", 0),
                                "output": tokens.get("completion_tokens", 0),
                                "total": tokens.get("total_tokens", 0),
                            },
                        )

        # -------------------------------------------------------------------
        # Legacy SDK Fallback (Langfuse v2 API)
        # -------------------------------------------------------------------
        elif hasattr(langfuse_client, "trace"):
            trace = langfuse_client.trace(
                name="whatsapp_rag_query",
                user_id=str(user_id),
                session_id=str(session_id),
                input={"query": query_text},
                output={"response": response_text},
                metadata=meta,
                tags=tags,
            )

            if not cache_hit and retrieved_chunks and hasattr(trace, "span"):
                trace.span(
                    name="hybrid_pgvector_bm25_retrieval",
                    input={"query": query_text},
                    output={"chunks": retrieved_chunks[:4]},
                    metadata={"retrieved_count": len(retrieved_chunks)},
                )

            if not cache_hit and model_name != "none" and hasattr(trace, "generation"):
                trace.generation(
                    name="llm_policy_generation",
                    model=model_name,
                    input=query_text,
                    output=response_text,
                    usage={
                        "input": tokens.get("prompt_tokens", 0),
                        "output": tokens.get("completion_tokens", 0),
                        "total": tokens.get("total_tokens", 0),
                    },
                )

            if hasattr(trace, "score"):
                trace.score(
                    name="cache_hit",
                    value=1.0 if cache_hit else 0.0,
                    comment="Semantic Redis Cache Hit Indicator",
                )

        # Flush in non-blocking background thread
        threading.Thread(target=_async_flush, daemon=True).start()
        logger.info(f"✅ TRACE_LOGGED_LANGFUSE | user={user_id} | model={model_name} | latency={latency_ms:.1f}ms")

    except Exception as e:
        logger.warning(f"LANGFUSE_TRACE_FAILED | user={user_id} | error={e}")