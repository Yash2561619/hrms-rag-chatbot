"""Centralized Observability Service using Langfuse Python SDK.
Supports dynamic multi-LLM tracing (Gemini + Groq).
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
        # Determine execution tag
        if cache_hit:
            source_tag = "cache_hit"
        elif "llama" in model_name.lower():
            source_tag = "groq_fallback"
        elif "gemini" in model_name.lower():
            source_tag = "gemini_primary"
        else:
            source_tag = "extractive_fallback"

        # 1. Root Trace
        trace = langfuse_client.create_trace(
            name="hr_policy_chat",
            user_id=user_id,
            session_id=session_id,
            input={"query": query_text},
            output={"response": response_text},
            metadata={
                "cache_hit": cache_hit,
                "latency_ms": round(latency_ms, 2),
                "model_used": model_name if not cache_hit else "semantic_cache",
                "chunks_count": len(retrieved_chunks) if retrieved_chunks else 0,
            },
            tags=["production", "whatsapp", source_tag],
        )

        # 2. Retrieval Span
        if retrieved_chunks:
            trace.create_span(
                name="hybrid_vector_bm25_retrieval",
                input={"query": query_text},
                output={"chunks": retrieved_chunks},
                metadata={"retrieved_count": len(retrieved_chunks)},
            )

        # 3. LLM Generation (Captures either Gemini or Groq tokens & model ID)
        if not cache_hit and model_name != "none":
            generation_name = "groq_generation" if "llama" in model_name.lower() else "gemini_generation"
            trace.create_generation(
                name=generation_name,
                model=model_name,
                input=query_text,
                output=response_text,
                usage={
                    "input": tokens_used.get("prompt_tokens", 0),
                    "output": tokens_used.get("completion_tokens", 0),
                    "total": tokens_used.get("total_tokens", 0),
                },
            )

        # 4. Cache Hit Metric Score
        trace.create_score(
            name="cache_hit_rate",
            value=1.0 if cache_hit else 0.0,
            comment="Semantic Redis Cache Hit Indicator",
        )

        # 5. Hallucination Metric Score
        if hallucination_score is not None:
            trace.create_score(
                name="hallucination_score",
                value=hallucination_score,
                comment="Context Faithfulness Score",
            )

        langfuse_client.flush()

    except Exception as e:
        logger.warning(f"LANGFUSE_RECORD_FAILED | {e}")