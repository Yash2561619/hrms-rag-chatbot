"""Advanced RAG Service for HR Policy Queries.

Includes Query Expansion, Hybrid FAISS + BM25 Search, Math RRF Re-Ranking, and
Model Rate Limit Fallbacks. Location: app/services/rag_service.py
"""

logging
import os
import re
from app.services.whatsapp_service import send_text
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

# Global index references cached in RAM
faiss_store = None
bm25_index = None
all_docs = []


def load_indexes():
  """Lazy-loads FAISS and builds BM25 index in RAM on boot (~15 MB RAM total)."""
  global faiss_store, bm25_index, all_docs

  if faiss_store is None:
    try:
      api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
      embeddings = GoogleGenerativeAIEmbeddings(
          model="models/gemini-embedding-001", google_api_key=api_key
      )

      if os.path.exists("faiss_index"):
        faiss_store = FAISS.load_local(
            "faiss_index", embeddings, allow_dangerous_deserialization=True
        )
        all_docs = list(faiss_store.docstore._dict.values())

        # Tokenize corpus for lightweight BM25 keyword matching
        tokenized_corpus = [
            doc.page_content.lower().split() for doc in all_docs
        ]
        bm25_index = BM25Okapi(tokenized_corpus)
        logger.info("HYBRID_SEARCH_INDEXES_LOADED_SUCCESSFULLY ✅")
      else:
        logger.error("FAISS index directory 'faiss_index' not found!")
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
        config={"temperature": 0.2, "max_output_tokens": 100},
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
    faiss_chunks: list, bm25_chunks: list, top_k: int = 2
) -> tuple[str, str, list]:
  """Reranks candidate chunks mathematically using Reciprocal Rank Fusion (RRF).

  Consumes 0 API calls and 0 MB RAM.
  """
  if not faiss_chunks and not bm25_chunks:
    return "", "", []

  rrf_scores = {}
  chunk_map = {}

  # Score FAISS vector ranks
  for rank, doc in enumerate(faiss_chunks, start=1):
    doc_id = doc.page_content
    chunk_map[doc_id] = doc
    rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60 + rank))

  # Score BM25 keyword ranks
  for rank, doc in enumerate(bm25_chunks, start=1):
    doc_id = doc.page_content
    chunk_map[doc_id] = doc
    rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60 + rank))

  # Sort chunks by highest RRF math score
  sorted_docs = sorted(
      rrf_scores.items(), key=lambda item: item[1], reverse=True
  )
  top_docs = [chunk_map[doc_id] for doc_id, score in sorted_docs[:top_k]]

  # Format context string for Gemini
  context = "\n---\n".join([d.page_content for d in top_docs])

  # Extract official source citations for WhatsApp footer
  sources_set = set()
  for doc in top_docs:
    source_file = doc.metadata.get("source", "")
    page_num = doc.metadata.get("page", "")
    if source_file:
      page_text = f" (Page {page_num})" if page_num else ""
      sources_set.add(f"📄 *{source_file}*{page_text}")

  citation_footer = (
      "\n\n_Source: " + ", ".join(sources_set) + "_" if sources_set else ""
  )

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

  for doc in chunks[:3]:
    # Extract sources for fallback citation
    source_file = doc.metadata.get("source", "")
    page_num = doc.metadata.get("page", "")
    if source_file:
      page_text = f" (Page {page_num})" if page_num else ""
      sources_set.add(f"📄 *{source_file}*{page_text}")

    text = re.sub(r"\s+", " ", doc.page_content).strip()

    for sentence in text.split(". "):
      sentence = sentence.strip()
      if len(sentence) > 25 and sentence.lower() not in seen:
        seen.add(sentence.lower())
        clean_sentences.append(sentence)
        if len(clean_sentences) >= 4:
          break
    if len(clean_sentences) >= 4:
      break

  bullet_points = "\n".join([f"• {s}." for s in clean_sentences])
  citation_footer = (
      "\n\n_Source: " + ", ".join(sources_set) + "_" if sources_set else ""
  )

  fallback_text = (
      "⚠️ *High server traffic. Here are relevant policy excerpts:* \n\n"
      f"{bullet_points}\n\n"
      "_Please try asking again in a minute for a full generated summary._"
  )

  return fallback_text, citation_footer


def handle_rag_query(employee, query: str, collection_unused, gemini_client):
  """Main entry point for handling employee policy questions with Advanced RAG."""
  sender = employee["whatsapp"]
  logger.info(f"RAG_QUERY_START | user={employee.get('employee_id')}")

  try:
    # Step 1: Multi-Query Expansion & Hybrid Retrieval
    expanded_queries = multi_query_expansion(query, gemini_client)
    dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(
        expanded_queries, top_k=6
    )

    if not all_retrieved:
      send_text(
          sender,
          "❌ I couldn't find relevant information in the HR policy"
          " documents.",
      )
      return

    # Step 2: Context Re-ranking and Citation Extraction
    context, citation_footer, top_docs = math_rrf_rerank(
        dense_docs, sparse_docs, top_k=2
    )

    prompt = f"""
Answer the employee question concisely and professionally using ONLY the provided HR Policy Context.
Context:
{context}

Question:
{query}
"""

    response_text = None

    # Step 3: Primary Generation Call (gemini-2.5-flash)
    try:
      response = gemini_client.models.generate_content(
          model="gemini-2.5-flash",
          contents=prompt,
          config={"temperature": 0.2, "max_output_tokens": 300},
      )
      if response and response.text:
        response_text = response.text.strip()
    except Exception as err:
      logger.warning(
          f"LLM_GENERATION_FAILED | gemini-2.5-flash error: {err}. Falling"
          " back to raw excerpts."
      )

    # Step 4: Deliver Clean Formatted Excerpts if API fails or rate-limits
    if not response_text:
      logger.info(
          f"FALLING_BACK_TO_RAW_EXCERPTS | user={employee.get('employee_id')}"
      )
      response_text, citation_footer = format_raw_chunks_fallback(all_retrieved)

    # Step 5: Send final WhatsApp message with source citation footer
    final_whatsapp_msg = f"{response_text}{citation_footer}"

    send_text(sender, final_whatsapp_msg)
    logger.info(f"RAG_QUERY_SUCCESS | user={employee.get('employee_id')}")

  except Exception as e:
    logger.exception(f"RAG_FATAL_ERROR | user={employee.get('employee_id')}")
    send_text(
        sender,
        "❌ An error occurred while processing your policy request. Please try"
        " again later.",
    )