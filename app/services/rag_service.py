"""Advanced RAG Service for HR Policy Queries.

Includes Query Expansion, Hybrid FAISS + BM25 Search, API Re-Ranking, and Model
Rate Limit Fallbacks. Location: app/services/rag_service.py
"""

import logging
import os
import re
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from rank_bm25 import BM25Okapi
from app.services.whatsapp_service import send_text

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


def hybrid_retrieve(queries: list[str], top_k: int = 6) -> list:
  """Executes FAISS + BM25 Hybrid Search with Reciprocal Rank Fusion (RRF)."""
  load_indexes()
  if not faiss_store or not bm25_index:
    return []

  doc_scores = {}
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

  # 3. Reciprocal Rank Fusion (RRF)
  for rank, doc in enumerate(dense_docs + sparse_docs):
    doc_scores[doc.page_content] = doc_scores.get(doc.page_content, 0) + (
        1 / (rank + 60)
    )

  sorted_contents = sorted(
      doc_scores.keys(), key=lambda x: doc_scores[x], reverse=True
  )

  merged_docs = []
  for content in sorted_contents[:top_k]:
    for doc in dense_docs + sparse_docs:
      if doc.page_content == content:
        merged_docs.append(doc)
        break

  return merged_docs


def api_rerank(query: str, chunks: list, gemini_client) -> str:
  """Reranks candidate chunks via Gemini API to filter top 2 most relevant passages."""
  if not chunks:
    return ""

  formatted_candidates = "\n---\n".join(
      [f"Passage {idx+1}:\n{doc.page_content}" for idx, doc in enumerate(chunks)]
  )

  prompt = f"""
    User Query: "{query}"

    Passages retrieved from HR policy files:
    {formatted_candidates}

    Select ONLY the 2 most relevant passages that directly answer the query. Return their exact text content concatenated together.
    """

  try:
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"temperature": 0.0, "max_output_tokens": 400},
    )
    return response.text.strip()
  except Exception as e:
    logger.warning(f"RERANK_FALLBACK | Returning raw top chunks: {e}")
    return "\n\n".join([doc.page_content for doc in chunks[:2]])


def format_raw_chunks_fallback(chunks: list) -> str:
  """Cleans and formats retrieved chunks into a bulleted list if API limits are hit."""
  if not chunks:
    return (
        "❌ I couldn't find any relevant policy information for your question."
    )

  clean_sentences = []
  seen = set()

  for doc in chunks[:3]:
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

  return (
      "⚠️ *High server traffic. Here are relevant policy excerpts:* \n\n"
      f"{bullet_points}\n\n"
      "_Please try asking again in a minute for a full generated summary._"
  )


def handle_rag_query(employee, query: str, collection_unused, gemini_client):
  """Main entry point for handling employee policy questions with Advanced RAG."""
  sender = employee["whatsapp"]
  logger.info(f"RAG_QUERY_START | user={employee.get('employee_id')}")

  try:
    # Step 1: Multi-Query Expansion & Hybrid Retrieval
    expanded_queries = multi_query_expansion(query, gemini_client)
    retrieved_chunks = hybrid_retrieve(expanded_queries, top_k=6)

    if not retrieved_chunks:
      send_text(
          sender,
          "❌ I couldn't find relevant information in the HR policy"
          " documents.",
      )
      return

    # Step 2: Context Re-ranking
    context = api_rerank(query, retrieved_chunks, gemini_client)

    prompt = f"""
        Answer the employee question concise and professionally using ONLY the provided HR Policy Context.
        Context:
        {context}

        Question:
        {query}
        """

    response_text = None

    # Step 3: Attempt Primary Model
    try:
      response = gemini_client.models.generate_content(
          model="gemini-2.5-flash",
          contents=prompt,
          config={"temperature": 0.2, "max_output_tokens": 300},
      )
      if response and response.text:
        response_text = response.text.strip()
    except Exception as primary_err:
      logger.warning(
          f"PRIMARY_LLM_FAILED | gemini-2.5-flash error: {primary_err}"
      )

      # Step 4: Attempt Fallback Model
      try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"temperature": 0.2, "max_output_tokens": 300},
        )
        if response and response.text:
          response_text = response.text.strip()
      except Exception as fallback_err:
        logger.error(f"FALLBACK_LLM_FAILED | gemini-2.0-flash error: {fallback_err}")

    # Step 5: Deliver Clean Formatted Excerpts if both LLMs fail
    if not response_text:
      logger.info(
          f"FALLING_BACK_TO_RAW_EXCERPTS | user={employee.get('employee_id')}"
      )
      response_text = format_raw_chunks_fallback(retrieved_chunks)

    send_text(sender, response_text)
    logger.info(f"RAG_QUERY_SUCCESS | user={employee.get('employee_id')}")

  except Exception as e:
    logger.exception(f"RAG_FATAL_ERROR | user={employee.get('employee_id')}")
    send_text(
        sender,
        "❌ An error occurred while processing your policy request. Please try"
        " again later.",
    )