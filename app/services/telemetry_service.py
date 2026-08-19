"""Centralized Observability and Telemetry Service using Langfuse.

Location: app/services/telemetry_service.py
"""

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Safe initialization of Langfuse Client
try:
    from langfuse import Langfuse

    langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if langfuse_public_key and langfuse_secret_key:
        langfuse_client = Langfuse(
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
            host=langfuse_host,
        )
    else:
        langfuse_client = None
except Exception as e:
    logger.warning(f"LANGFUSE_CLIENT_INIT_FAILED | {e}")
    langfuse_client = None


def trace_rag_interaction(
    user_id: str,
    session_id: str,
    query_text: str,
    response_text: str,
    latency_ms: float,
    cache_hit: bool,
    tokens_used: Dict[str, int],
    retrieved_chunks: Optional[list] = None,
    hallucination_score: Optional[float] = None,
):
    """Logs structured RAG spans, token usage, and cache metrics to Langfuse."""
    if not langfuse_client:
        return

    try:
        # 1. Root Trace for the User Request
        trace = langfuse_client.trace(
            name="hr_chat_request",
            user_id=user_id,
            session_id=session_id,
            input={"query": query_text},
            output={"response": response_text},
            metadata={
                "cache_hit": cache_hit,
                "latency_ms": round(latency_ms, 2),
                "chunks_retrieved_count": (
                    len(retrieved_chunks) if retrieved_chunks else 0
                ),
            },
            tags=["production", "whatsapp", "cache_hit" if cache_hit else "rag"],
        )

        # 2. Retrieval Span (if vector search occurred)
        if retrieved_chunks:
            trace.span(
                name="hybrid_vector_bm25_retrieval",
                input={"query": query_text},
                output={"chunks": retrieved_chunks},
                metadata={"chunks_count": len(retrieved_chunks)},
            )

        # 3. LLM Generation Tracking (if non-cache flow)
        if not cache_hit:
            trace.generation(
                name="gemini_2_5_flash_generation",
                model="gemini-2.5-flash",
                usage={
                    "input": tokens_used.get("prompt_tokens", 0),
                    "output": tokens_used.get("completion_tokens", 0),
                    "total": tokens_used.get("total_tokens", 0),
                },
                input=query_text,
                output=response_text,
            )

        # 4. Metrics & Evaluation Scores
        trace.score(
            name="cache_hit",
            value=1.0 if cache_hit else 0.0,
            comment="Redis Semantic Cache Hit Evaluation",
        )

        if hallucination_score is not None:
            trace.score(
                name="hallucination_score",
                value=hallucination_score,
                comment="Context Faithfulness Score",
            )

        # 5. Flush buffer immediately
        langfuse_client.flush()

    except Exception as e:
        logger.warning(f"TELEMETRY_LOGGING_FAILED | {e}")