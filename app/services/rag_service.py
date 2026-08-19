"""Advanced RAG Service for HR Policy Queries.

Includes Query Expansion, Hybrid FAISS + BM25 Search, Math RRF Re-Ranking, and
Model Rate Limit Fallbacks using HuggingFace all-MiniLM-L6-v2 Embeddings.
Location: app/services/rag_service.py
"""

import logging
import os
import re
from google.genai import types
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
from rank_bm25 import BM25Okapi

from app.services.memory_service import add_to_chat_history, get_chat_history
from app.services.whatsapp_service import send_text
from app.services.semantic_cache_service import (
    get_semantic_cached_answer,
    save_semantic_cached_answer,
)
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


def multi_query_expansion(query: str, gemini_client) -> list[str]:
  """Generates 2 query variations to improve search recall."""
  prompt = f"""
Generate 2 alternative search queries for an HR policy search.
Original Query: "{query}"
Output format: Return ONLY the queries separated by newlines, no bullet points or extra text.
"""
  try:
    res = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=100,
            tools=[],
        ),
    )
    variations = [
        q.strip() for q in res.text.strip().split("\n") if q.strip()
    ]
    return [query] + variations[:2]
  except Exception as e:
    logger.warning(f"MULTI_QUERY_EXPANSION_SKIPPED | {e}")
    return [query]


def hybrid_retrieve(
    queries: list[str], top_k: int = 6
) -> tuple[list, list, list]:
  """Executes FAISS + BM25 Hybrid Search and returns dense, sparse, and merged results."""
  load_indexes()
  if not faiss_store or not bm25_index:
    return [], [], []

  dense_docs = []
  sparse_docs = []

  for q in queries:
    # 1. FAISS Dense Retrieval
    d_docs = faiss_store.similarity_search(q, k=top_k)
    dense_docs.extend(d_docs)

    # 2. BM25 Sparse Keyword Retrieval
    tokenized_q = q.lower().split()
    bm25_scores = bm25_index.get_scores(tokenized_q)
    top_indices = sorted(
        range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True
    )[:top_k]
    s_docs = [all_docs[i] for i in top_indices]
    sparse_docs.extend(s_docs)

  all_retrieved = dense_docs + sparse_docs
  return dense_docs, sparse_docs, all_retrieved


def math_rrf_rerank(
    faiss_chunks: list, bm25_chunks: list, top_k: int = 4
) -> tuple[str, str, list]:
  """Reranks candidate chunks mathematically and formats clean citations."""
  if not faiss_chunks and not bm25_chunks:
    return "", "", []

  rrf_scores = {}
  chunk_map = {}

  for rank, doc in enumerate(faiss_chunks, start=1):
    doc_id = doc.page_content
    chunk_map[doc_id] = doc
    rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60 + rank))

  for rank, doc in enumerate(bm25_chunks, start=1):
    doc_id = doc.page_content
    chunk_map[doc_id] = doc
    rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60 + rank))

  sorted_docs = sorted(
      rrf_scores.items(), key=lambda item: item[1], reverse=True
  )
  top_docs = [chunk_map[doc_id] for doc_id, score in sorted_docs[:top_k]]

  context = "\n---\n".join([d.page_content for d in top_docs])

  # Clean source formatting without markdown breaks
  sources_set = set()
  for doc in top_docs:
    source_file = doc.metadata.get("source", "")
    page_num = doc.metadata.get("page", "")

    # Exclude index-only summary files from the citations footer
    if source_file and "INDEX" not in source_file.upper():
      clean_name = os.path.basename(source_file)
      clean_name = re.sub(r"^\d+_", "", clean_name)
      clean_name = clean_name.replace(".pdf", "").replace("_", " ")

      page_text = f" (Page {page_num})" if page_num else ""
      sources_set.add(f"{clean_name}{page_text}")

  if sources_set:
    citation_footer = (
        "\n━━━━━━━━━━━━━━━━━━━\n📁 *Source:* " + ", ".join(sorted(sources_set))
    )
  else:
    citation_footer = ""

  return context, citation_footer, top_docs


def format_raw_chunks_fallback(chunks: list) -> tuple[str, str]:
  """Cleans and formats retrieved chunks into a bulleted list if API limits are hit."""
  if not chunks:
    return (
        "❌ I couldn't find any relevant policy information for your question.",
        "",
    )

  clean_sentences = []
  seen = set()
  sources_set = set()

  for doc in chunks[:4]:
    source_file = doc.metadata.get("source", "")
    page_num = doc.metadata.get("page", "")
    if source_file and "INDEX" not in source_file.upper():
      clean_name = os.path.basename(source_file)
      clean_name = re.sub(r"^\d+_", "", clean_name)
      clean_name = clean_name.replace(".pdf", "").replace("_", " ")
      page_text = f" (Page {page_num})" if page_num else ""
      sources_set.add(f"{clean_name}{page_text}")

    text = re.sub(r"\s+", " ", doc.page_content).strip()

    for sentence in text.split(". "):
      sentence = sentence.strip()
      if len(sentence) > 25 and sentence.lower() not in seen:
        seen.add(sentence.lower())
        clean_sentences.append(sentence)
        if len(clean_sentences) >= 5:
          break
    if len(clean_sentences) >= 5:
      break

  bullet_points = "\n".join([f"• {s}." for s in clean_sentences])
  if sources_set:
    citation_footer = (
        "\n━━━━━━━━━━━━━━━━━━━\n📁 *Source:* " + ", ".join(sorted(sources_set))
    )
  else:
    citation_footer = ""

  fallback_text = (
      "📋 *Policy Excerpts (High Traffic Mode)*\n━━━━━━━━━━━━━━━━━━━\n\n"
      f"{bullet_points}\n\n"
      "_Please ask again in a moment for a synthesized summary._"
  )

  return fallback_text, citation_footer

def handle_rag_query(employee, query: str, collection_unused, gemini_client):
    """Handles end-to-end RAG query flow with Semantic Cache + Hybrid RAG."""
    sender = employee["whatsapp"]
    employee_id = employee.get("employee_id")
    logger.info(f"RAG_QUERY_START | user={employee_id}")

    try:
        # =========================================================================
        # 1. SEMANTIC CACHE PROBE (Sub-50ms Fast Path)
        # =========================================================================
        cached_response, cached_footer = get_semantic_cached_answer(query)
        if cached_response:
            add_to_chat_history(employee_id, query, cached_response)
            final_whatsapp_msg = f"{cached_response}{cached_footer}"
            send_text(sender, final_whatsapp_msg)
            logger.info(f"RAG_QUERY_SUCCESS (FROM_CACHE) ⚡ | user={employee_id}")
            return

        # =========================================================================
        # 2. FULL RAG RETRIEVAL (Only executed on Cache Miss)
        # =========================================================================
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

        response_text = None

        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=700,
                    tools=[],
                ),
            )
            if response and response.text:
                response_text = response.text.strip()
        except Exception as err:
            logger.warning(f"LLM_GENERATION_FAILED | {err}")

        if not response_text:
            response_text, citation_footer = format_raw_chunks_fallback(all_retrieved)

        # 3. Save successful interaction to Chat Memory
        if response_text:
            add_to_chat_history(employee_id, query, response_text)
            # 4. Save into Semantic Cache for future employees
            save_semantic_cached_answer(query, response_text, citation_footer)

        # 5. Deliver final response
        final_whatsapp_msg = f"{response_text}{citation_footer}"
        send_text(sender, final_whatsapp_msg)
        logger.info(f"RAG_QUERY_SUCCESS | user={employee_id}")

    except Exception as e:
        logger.exception(f"RAG_FATAL_ERROR | user={employee_id}")
        send_text(
            sender,
            "❌ An error occurred while retrieving policy details. Please try again later.",
        )