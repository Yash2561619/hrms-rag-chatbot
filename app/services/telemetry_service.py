"""Centralized Observability Service using Langfuse Python SDK.
Supports dynamic multi-LLM tracing and SDK v2/v3 compatibility.
Location: app/services/telemetry_service.py
"""

import os
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Initialize client cleanly from environment variables
try:
    from langfuse import Langfuse

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if public_key and secret_key:
        langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host
        )
    else:
        langfuse_client = None
except Exception as e:
    logger.warning(f"LANGFUSE_INIT_FAILED | {e}")
    langfuse_client = None


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
    if not langfuse_client:
        return

    try:
        source_tag = (
            "cache_hit"
            if cache_hit
            else ("groq_fallback" if "llama" in model_name.lower() else "gemini_primary")
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

        # Compatible with Langfuse v2 and v3
        trace = None
        if hasattr(langfuse_client, "trace"):
            trace = langfuse_client.trace(**trace_kwargs)
        elif hasattr(langfuse_client, "create_trace"):
            trace = langfuse_client.create_trace(**trace_kwargs)

        if trace:
            # 1. Retrieval Span
            if retrieved_chunks:
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

            # 2. Generation Observation
            if not cache_hit and model_name != "none":
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

            # 3. Cache Score
            score_kwargs = {
                "name": "cache_hit_rate",
                "value": 1.0 if cache_hit else 0.0,
                "comment": "Semantic Redis Cache Hit Indicator",
            }
            if hasattr(trace, "score"):
                trace.score(**score_kwargs)
            elif hasattr(trace, "create_score"):
                trace.create_score(**score_kwargs)

        if hasattr(langfuse_client, "flush"):
            langfuse_client.flush()

    except Exception as e:
        logger.warning(f"LANGFUSE_RECORD_FAILED | {e}")