"""Centralized Observability Service using Langfuse Python SDK.
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
    """Creates a structured trace hierarchy: Root Trace -> Retrieval Span -> LLM Generation -> Scores."""
    if not langfuse_client:
        return

    try:
        source_tag = "cache_hit" if cache_hit else ("groq_fallback" if "llama" in model_name.lower() else "gemini_primary")

        # 1. Root Trace
        trace = langfuse_client.trace(
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
            trace.span(
                name="hybrid_vector_bm25_retrieval",
                input={"query": query_text},
                output={"chunks": retrieved_chunks},
                metadata={"retrieved_count": len(retrieved_chunks)},
            )

        # 3. LLM Generation
        if not cache_hit and model_name != "none":
            trace.generation(
                name="llm_generation",
                model=model_name,
                input=query_text,
                output=response_text,
                usage={
                    "input": tokens_used.get("prompt_tokens", 0),
                    "output": tokens_used.get("completion_tokens", 0),
                    "total": tokens_used.get("total_tokens", 0),
                },
            )

        # 4. Scores
        trace.score(
            name="cache_hit_rate",
            value=1.0 if cache_hit else 0.0,
            comment="Semantic Redis Cache Hit Indicator",
        )

        if hallucination_score is not None:
            trace.score(
                name="hallucination_score",
                value=hallucination_score,
                comment="Context Faithfulness Score",
            )

        langfuse_client.flush()

    except Exception as e:
        logger.warning(f"LANGFUSE_RECORD_FAILED | {e}")