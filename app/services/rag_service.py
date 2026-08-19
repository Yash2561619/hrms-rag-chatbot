"""Advanced RAG Service for HR Policy Queries.

Standardized with Langfuse @observe Decorators for Full Observability.
Location: app/services/rag_service.py
"""

import logging
import os
import re
import time
from google.genai import types
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
from langfuse.decorators import langfuse_context, observe
from rank_bm25 import BM25Okapi

from app.services.memory_service import add_to_chat_history, get_chat_history
from app.services.semantic_cache_service import (
    get_semantic_cached_answer,
    save_semantic_cached_answer,
)
from app.services.whatsapp_service import send_text

logger = logging.getLogger(__name__)

# Global index references cached in RAM
faiss_store = None
bm25_index = None
all_docs = []


def load_indexes():
  """Loads FAISS using lightweight ONNX-based FastEmbed (<60 MB RAM)."""
  global faiss_store, bm25_index, all_docs

  if faiss_store is None:
    try:
      embeddings = FastEmbedEmbeddings(
          model_name="sentence-transformers/all-MiniLM-L6-v2"
      )

      if os.path.exists("faiss_index"):
        faiss_store = FAISS.load_local(
            "faiss_index", embeddings, allow_dangerous_deserialization=True
        )
        all_docs = list(faiss_store.docstore._dict.values())

        tokenized_corpus = [
            doc.page_content.lower().split() for doc in all_docs
        ]
        bm25_index = BM25Okapi(tokenized_corpus)
        logger.info("HYBRID_INDEXES_LOADED_SUCCESSFULLY (FastEmbed + BM25) ✅")
      else:
        logger.error("faiss_index folder not found.")
    except Exception as e:
      logger.error(f"LOAD_INDEXES_ERROR | {e}")


@observe(name="query_expansion", as_type="generation")
def multi_query_expansion(query: str, gemini_client) -> list[str]:
  """Generates query variations with automatic observation logging."""
  prompt = f"""Generate 2 alternative search queries for an HR policy search.
Original Query: "{query}"
Output format: Return ONLY the queries separated by newlines, no bullet points or extra text."""
  try:
    res = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=100),
    )
    variations = [q.strip() for q in res.text.strip().split("\n") if q.strip()]
    return [query] + variations[:2]
  except Exception as e:
    logger.warning(f"MULTI_QUERY_EXPANSION_SKIPPED | {e}")
    return [query]


@observe(name="hybrid_retrieval", as_type="span")
def hybrid_retrieve(queries: list[str], top_k: int = 6) -> tuple[list, list, list]:
  """Executes dense + sparse vector search with span recording."""
  load_indexes()
  if not faiss_store or not bm25_index:
    return [], [], []

  dense_docs = []
  sparse_docs = []

  for q in queries:
    dense_docs.extend(faiss_store.similarity_search(q, k=top_k))

    tokenized_q = q.lower().split()
    bm25_scores = bm25_index.get_scores(tokenized_q)
    top_indices = sorted(
        range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True
    )[:top_k]
    sparse_docs.extend([all_docs[i] for i in top_indices])

  return dense_docs, sparse_docs, dense_docs + sparse_docs


@observe(name="rrf_reranking", as_type="span")
def math_rrf_rerank(
    faiss_chunks: list, bm25_chunks: list, top_k: int = 4
) -> tuple[str, str, list]:
  """Reranks candidate chunks mathematically."""
  if not faiss_chunks and not bm25_chunks:
    return "", "", []

  rrf_scores = {}
  chunk_map = {}

  for rank, doc in enumerate(faiss_chunks, start=1):
    chunk_map[doc.page_content] = doc
    rrf_scores[doc.page_content] = rrf_scores.get(doc.page_content, 0.0) + (
        1.0 / (60 + rank)
    )

  for rank, doc in enumerate(bm25_chunks, start=1):
    chunk_map[doc.page_content] = doc
    rrf_scores[doc.page_content] = rrf_scores.get(doc.page_content, 0.0) + (
        1.0 / (60 + rank)
    )

  sorted_docs = sorted(
      rrf_scores.items(), key=lambda item: item[1], reverse=True
  )
  top_docs = [chunk_map[doc_id] for doc_id, _ in sorted_docs[:top_k]]
  context = "\n---\n".join([d.page_content for d in top_docs])

  sources_set = set()
  for doc in top_docs:
    source_file = doc.metadata.get("source", "")
    page_num = doc.metadata.get("page", "")
    if source_file and "INDEX" not in source_file.upper():
      clean_name = os.path.basename(source_file)
      clean_name = re.sub(r"^\d+_", "", clean_name).replace(".pdf", "").replace("_", " ")
      sources_set.add(f"{clean_name}{f' (Page {page_num})' if page_num else ''}")

  citation_footer = (
      "\n━━━━━━━━━━━━━━━━━━━\n📁 *Source:* " + ", ".join(sorted(sources_set))
      if sources_set
      else ""
  )
  return context, citation_footer, top_docs


@observe(name="gemini_llm_generation", as_type="generation")
def execute_llm_generation(gemini_client, prompt: str):
  """Generates LLM completion and extracts token metadata into Langfuse."""
  response = gemini_client.models.generate_content(
      model="gemini-2.5-flash",
      contents=prompt,
      config=types.GenerateContentConfig(
          temperature=0.1, max_output_tokens=700
      ),
  )

  if hasattr(response, "usage_metadata") and response.usage_metadata:
    langfuse_context.update_current_observation(
        usage={
            "input": getattr(
                response.usage_metadata, "prompt_token_count", 0
            ),
            "output": getattr(
                response.usage_metadata, "candidates_token_count", 0
            ),
            "total": getattr(response.usage_metadata, "total_token_count", 0),
        }
    )
  return response.text.strip() if response and response.text else None


@observe(name="hr_policy_chat")
def handle_rag_query(employee, query: str, collection_unused, gemini_client):
  """Root Trace: Handles end-to-end RAG workflow with telemetry and scores."""
  sender = employee["whatsapp"]
  employee_id = employee.get("employee_id") or "UNKNOWN_EMP"

  # Update Root Trace Context in Langfuse
  langfuse_context.update_current_trace(
      user_id=employee_id,
      session_id=f"session_{employee_id}",
      tags=["production", "whatsapp"],
      metadata={"employee_id": employee_id, "department": employee.get("department", "General")},
  )

  try:
    # 1. Semantic Cache Probe
    cached_response, cached_footer = get_semantic_cached_answer(query)
    if cached_response:
      add_to_chat_history(employee_id, query, cached_response)
      send_text(sender, f"{cached_response}{cached_footer}")
      langfuse_context.score_current_trace(
          name="cache_hit", value=1.0, comment="Served from Redis Semantic Cache"
      )
      langfuse_context.flush()
      return

    langfuse_context.score_current_trace(
        name="cache_hit", value=0.0, comment="Semantic Cache Miss"
    )

    # 2. Hybrid RAG Retrieval Pipeline
    chat_history_str = get_chat_history(employee_id, max_messages=4)
    expanded_queries = multi_query_expansion(query, gemini_client)
    dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(
        expanded_queries, top_k=6
    )

    if not all_retrieved:
      send_text(
          sender,
          "❌ I couldn't find relevant information in the company policy documents.",
      )
      langfuse_context.flush()
      return

    context, citation_footer, top_docs = math_rrf_rerank(
        dense_docs, sparse_docs, top_k=3
    )

    prompt = f"""You are an AI HR Assistant. Formulate your answer directly for WhatsApp reading as a structured card.

FORMATTING RULES:
1. Start directly with the card header:
📋 *Policy Information*
━━━━━━━━━━━━━━━━━━━
2. Do NOT write conversational fluff, pleasantries, or preamble sentences like "Here is a summary...", "Based on the policy...", "According to...".
3. Use bullet points (•) with bold category labels (e.g., • *Permanent Employee:* 60 Days notice).
4. Group related points under bold section headers with emojis (e.g., 📌 *Notice Periods:*, 📌 *Important Guidelines:*).
5. Never leave sentences unfinished or truncated.
6. If the context does not contain the answer, reply ONLY with:
❌ This information is not covered in our official policy documents.

---
Conversation History:
{chat_history_str}
---
HR Policy Context:
{context}
---
Employee Question:
{query}

Card Response:"""

    response_text = execute_llm_generation(gemini_client, prompt)

    if response_text:
      add_to_chat_history(employee_id, query, response_text)
      save_semantic_cached_answer(query, response_text, citation_footer)

    send_text(sender, f"{response_text}{citation_footer}")
    langfuse_context.flush()

  except Exception as e:
    logger.exception(f"RAG_FATAL_ERROR | user={employee_id}")
    send_text(
        sender,
        "❌ An error occurred while retrieving policy details. Please try again later.",
    )
    langfuse_context.flush()