"""Centralized Observability Service using Langfuse Python SDK.
Location: app/services/telemetry_service.py
"""

import os
import logging
from typing import Dict, Optional, List
from langfuse import Langfuse

logger = logging.getLogger(__name__)

# Initialize client cleanly from environment variables
try:
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if public_key and secret_key:
        langfuse = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host
        )
    else:
        langfuse = None
except Exception as e:
    logger.warning(f"LANGFUSE_INIT_FAILED | {e}")
    langfuse = None


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
):
    """Creates a structured trace hierarchy: Root Trace -> Retrieval Span -> LLM Generation -> Scores."""
    if not langfuse:
        return

    try:
        # 1. Root Trace (End-to-End User Interaction)
        trace = langfuse.trace(
            name="hr_policy_chat",
            user_id=user_id,
            session_id=session_id,
            input={"query": query_text},
            output={"response": response_text},
            metadata={
                "cache_hit": cache_hit,
                "latency_ms": round(latency_ms, 2),
                "chunks_count": len(retrieved_chunks) if retrieved_chunks else 0,
            },
            tags=["production", "whatsapp", "cache_hit" if cache_hit else "rag_pipeline"],
        )

        # 2. Retrieval Span (Vector & BM25 search phase)
        if retrieved_chunks:
            trace.span(
                name="hybrid_vector_bm25_retrieval",
                input={"query": query_text},
                output={"chunks": retrieved_chunks},
                metadata={"retrieved_count": len(retrieved_chunks)},
            )

        # 3. LLM Generation Observation (Captures Gemini Token Usage & Model Metadata)
        if not cache_hit:
            trace.generation(
                name="gemini_generation",
                model="gemini-2.5-flash",
                input=query_text,
                output=response_text,
                usage={
                    "input": tokens_used.get("prompt_tokens", 0),
                    "output": tokens_used.get("completion_tokens", 0),
                    "total": tokens_used.get("total_tokens", 0),
                },
            )

        # 4. Observability Scores
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

        # 5. Flush buffer immediately for low-latency dispatch
        langfuse.flush()

    except Exception as e:
        logger.warning(f"LANGFUSE_RECORD_FAILED | {e}")