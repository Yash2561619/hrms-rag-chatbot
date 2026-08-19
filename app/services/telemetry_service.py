"""Centralized Observability and Telemetry Service using Langfuse.

Location: app/services/telemetry_service.py
"""

import logging
import os
import time
from typing import Any, Dict, Optional
from langfuse import Langfuse

logger = logging.getLogger(__name__)

langfuse_client = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)


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
  """Sends structured execution trace, metrics, and evaluation scores to Langfuse."""
  try:
    # 1. Root Trace for WhatsApp / User Session
    trace = langfuse_client.trace(
        name="hr_chat_request",
        user_id=user_id,
        session_id=session_id,
        input={"query": query_text},
        output={"response": response_text},
        metadata={
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
            "chunks_retrieved_count": (
                len(retrieved_chunks) if retrieved_chunks else 0
            ),
        },
        tags=["production", "whatsapp", "cache_hit" if cache_hit else "rag"],
    )

    # 2. Log Vector Retrieval Span
    if retrieved_chunks:
      trace.span(
          name="faiss_vector_retrieval",
          input={"query": query_text},
          output={"chunks": retrieved_chunks},
          metadata={"count": len(retrieved_chunks)},
      )

    # 3. Log LLM Generation & Token Metrics
    trace.generation(
        name="openai_llm_generation",
        model="gpt-4o-mini",
        usage={
            "prompt_tokens": tokens_used.get("prompt_tokens", 0),
            "completion_tokens": tokens_used.get("completion_tokens", 0),
            "total_tokens": tokens_used.get("total_tokens", 0),
        },
        input=query_text,
        output=response_text,
    )

    # 4. Record Evaluation Score (Hallucination / Faithfulness)
    if hallucination_score is not None:
      trace.score(
          name="hallucination_index",
          value=hallucination_score,  # 0.0 (grounded) to 1.0 (hallucinated)
          comment="Automated ragas / cosine grounding check",
      )

    # 5. Record Cache Hit Ratio Metric
    trace.score(
        name="cache_hit_ratio",
        value=1.0 if cache_hit else 0.0,
        comment="Semantic Redis Cache Hit",
    )

  except Exception as e:
    logger.warning(f"TELEMETRY_LOGGING_FAILED | {e}")