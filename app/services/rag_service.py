"""Advanced RAG Service for HR Policy Queries.

Includes Native PostgreSQL pgvector + BM25 Hybrid Search, Math RRF Re-Ranking,
Semantic Redis Caching, Exponential Backoff with Jitter, Dynamic Groq Failover,
Strict Similarity Guards, and Langfuse Observability.
Location: app/services/rag_service.py
"""

import logging
import os
import random
import re
import time
from typing import List, Optional, Tuple
from google.genai import types
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_core.documents import Document
import psycopg2
from rank_bm25 import BM25Okapi

from app.services.memory_service import add_to_chat_history, get_chat_history
from app.services.semantic_cache_service import (
    get_semantic_cached_answer,
    save_semantic_cached_answer,
)
from app.services.telemetry_service import trace_rag_interaction
from app.services.whatsapp_service import send_text

logger = logging.getLogger(__name__)

# Initialize Groq Client safely
try:
    from groq import Groq

    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_client = Groq(api_key=groq_api_key) if groq_api_key else None
except Exception as e:
    logger.warning(f"GROQ_INIT_FAILED | {e}")
    groq_client = None

# Global references
DATABASE_URL = os.getenv("DATABASE_URL")
embeddings_model = FastEmbedEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)
bm25_index = None
all_docs = []

DENSE_SIMILARITY_THRESHOLD = 0.40  # Minimum cosine similarity cutoff


def load_indexes(force_reload: bool = False):
    """Loads documents from PostgreSQL pgvector into RAM for BM25 sparse matching."""
    global bm25_index, all_docs

    if (bm25_index is None or force_reload) and DATABASE_URL:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute("SELECT content, metadata FROM policy_vectors;")
            rows = cur.fetchall()

            all_docs = [
                Document(page_content=r[0], metadata=r[1] if r[1] else {})
                for r in rows
            ]
            tokenized_corpus = [
                doc.page_content.lower().split() for doc in all_docs
            ]

            if tokenized_corpus:
                bm25_index = BM25Okapi(tokenized_corpus)
                logger.info(
                    f"PGVECTOR_AND_BM25_READY ✅ | Loaded {len(all_docs)} documents"
                )
            else:
                bm25_index = None
                all_docs = []

            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"PGVECTOR_LOAD_ERROR | {e}")


def pgvector_dense_search(query_vec: List[float], top_k: int = 6) -> List[Document]:
    """Performs native HNSW cosine distance search with strict similarity cutoff."""
    if not DATABASE_URL:
        return []
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT content, metadata, 1 - (embedding <=> %s::vector) AS similarity
            FROM policy_vectors
            WHERE (1 - (embedding <=> %s::vector)) >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
            """,
            (str(query_vec), str(query_vec), DENSE_SIMILARITY_THRESHOLD, str(query_vec), top_k),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return [
            Document(page_content=r[0], metadata=r[1] if r[1] else {})
            for r in rows
        ]
    except Exception as e:
        logger.error(f"PGVECTOR_QUERY_ERROR | {e}")
        return []


def multi_query_expansion(query: str, gemini_client) -> list[str]:
    """Generates query variations to improve search recall."""
    prompt = f"""Generate 2 alternative search queries for an HR policy search.
Original Query: "{query}"
Output format: Return ONLY the queries separated by newlines, no bullet points or extra text."""
    for attempt in range(2):
        try:
            res = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    max_output_tokens=60,
                    top_p=0.9,
                ),
            )
            variations = [
                q.strip() for q in res.text.strip().split("\n") if q.strip()
            ]
            return [query] + variations[:2]
        except Exception as e:
            if attempt == 0:
                time.sleep(0.3 + random.uniform(0.1, 0.3))
            else:
                logger.warning(f"MULTI_QUERY_EXPANSION_SKIPPED | {e}")
                return [query]

    return [query]


def hybrid_retrieve(
    queries: list[str], top_k: int = 6
) -> tuple[list, list, list]:
    """Executes pgvector (Dense) + BM25 (Sparse) search with relevance filters."""
    load_indexes()
    dense_docs = []
    sparse_docs = []

    for q in queries:
        # 1. Postgres pgvector dense retrieval (Filtered by similarity >= 0.40)
        q_vec = list(embeddings_model.embed_query(q))
        d_docs = pgvector_dense_search(q_vec, top_k=top_k)
        dense_docs.extend(d_docs)

        # 2. BM25 sparse retrieval (Filtered by positive score only)
        if bm25_index and all_docs:
            tokenized_q = q.lower().split()
            bm25_scores = bm25_index.get_scores(tokenized_q)
            positive_indices = [
                i for i, score in enumerate(bm25_scores) if score > 0.5
            ]
            top_indices = sorted(
                positive_indices,
                key=lambda i: bm25_scores[i],
                reverse=True,
            )[:top_k]
            s_docs = [all_docs[i] for i in top_indices]
            sparse_docs.extend(s_docs)

    all_retrieved = dense_docs + sparse_docs
    return dense_docs, sparse_docs, all_retrieved


def math_rrf_rerank(
    dense_chunks: list, bm25_chunks: list, top_k: int = 4
) -> tuple[str, str, list]:
    """Reranks candidate chunks using Reciprocal Rank Fusion."""
    if not dense_chunks and not bm25_chunks:
        return "", "", []

    rrf_scores = {}
    chunk_map = {}

    for rank, doc in enumerate(dense_chunks, start=1):
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
    top_docs = [chunk_map[doc_id] for doc_id, _ in sorted_docs[:top_k]]

    context = "\n---\n".join([d.page_content for d in top_docs])

    sources_set = set()
    for doc in top_docs:
        source_file = doc.metadata.get("source", "")
        page_num = doc.metadata.get("page", "")

        if source_file and "INDEX" not in source_file.upper():
            clean_name = os.path.basename(source_file)
            clean_name = re.sub(r"^\d+_", "", clean_name)
            clean_name = clean_name.replace(".pdf", "").replace("_", " ")

            page_text = f" (Page {page_num})" if page_num else ""
            sources_set.add(f"{clean_name}{page_text}")

    citation_footer = (
        "\n━━━━━━━━━━━━━━━━━━━\n📁 *Source:* " + ", ".join(sorted(sources_set))
        if sources_set
        else ""
    )

    return context, citation_footer, top_docs


def clean_reasoning_and_artifacts(text: str) -> str:
    """Strips <think> tags and excessive formatting artifacts."""
    if not text:
        return ""
    if "</think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    elif "<think>" in text:
        text = text.replace("<think>", "").strip()

    cleaned = re.sub(r"[━─\-]{10,}", "━━━━━━━━━━━━━━━━━━━", text)
    return cleaned.strip()


def get_active_groq_model() -> Optional[str]:
    """Discovers available models on active Groq account."""
    if not groq_client:
        return None
    try:
        models = groq_client.models.list()
        active_ids = [m.id for m in models.data]
        for pref in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "groq/compound-mini", "qwen/qwen3.6-27b"]:
            if pref in active_ids:
                return pref
        return "openai/gpt-oss-120b"
    except Exception:
        return "openai/gpt-oss-120b"


def is_valid_hr_response(text: str) -> bool:
    """Validates that the generated response contains meaningful text."""
    if not text or len(text.strip()) < 30:
        return False
    return True


def execute_llm_with_backoff_failover(
    gemini_client, prompt: str
) -> Tuple[Optional[str], dict, str]:
    """Cascading Execution: Gemini 2.5 Flash -> Dynamic Groq."""
    max_retries = 2
    base_delay = 0.5

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,  # Low temperature for strict adherence
                    max_output_tokens=700,
                    top_p=0.85,
                ),
            )
            if response and response.text:
                cleaned_text = clean_reasoning_and_artifacts(response.text)
                if is_valid_hr_response(cleaned_text):
                    tokens_used = {
                        "prompt_tokens": getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
                        "completion_tokens": getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
                        "total_tokens": getattr(response.usage_metadata, "total_token_count", 0) or 0,
                    }
                    return cleaned_text, tokens_used, "gemini-2.5-flash"
        except Exception as err:
            sleep_duration = (base_delay * (2**attempt)) + random.uniform(0.1, 0.4)
            logger.warning(f"GEMINI_RETRY | attempt={attempt+1} | error={err}")
            time.sleep(sleep_duration)

    if groq_client:
        selected_model = get_active_groq_model()
        if selected_model:
            try:
                chat_completion = groq_client.chat.completions.create(
                    model=selected_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=700,
                )
                raw_content = chat_completion.choices[0].message.content or ""
                cleaned_content = clean_reasoning_and_artifacts(raw_content)
                if is_valid_hr_response(cleaned_content):
                    return cleaned_content, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, selected_model
            except Exception as groq_err:
                logger.warning(f"GROQ_FAILED | error={groq_err}")

    return None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "none"


def handle_rag_query(employee, query: str, collection_unused, gemini_client):
    """Handles end-to-end RAG flow with strict relevance guards."""
    start_time = time.time()
    sender = employee["whatsapp"]
    employee_id = employee.get("employee_id") or "UNKNOWN_EMP"
    session_id = f"session_{employee_id}"
    logger.info(f"RAG_QUERY_START | user={employee_id}")

    try:
        # 1. Semantic Cache Probe
        cached_response, cached_footer = get_semantic_cached_answer(query)
        if cached_response:
            latency_ms = (time.time() - start_time) * 1000
            add_to_chat_history(employee_id, query, cached_response)
            send_text(sender, f"{cached_response}{cached_footer}")
            trace_rag_interaction(
                user_id=employee_id,
                session_id=session_id,
                query_text=query,
                response_text=cached_response,
                latency_ms=latency_ms,
                cache_hit=True,
                tokens_used={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                model_name="semantic_cache",
            )
            return

        # 2. Hybrid Retrieval with Score Thresholds
        chat_history_str = get_chat_history(employee_id, max_messages=4)
        expanded_queries = multi_query_expansion(query, gemini_client)
        dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(expanded_queries, top_k=6)

        # Early Guard: If no retrieved documents pass the similarity threshold
        if not all_retrieved or not dense_docs:
            latency_ms = (time.time() - start_time) * 1000
            not_found_msg = "❌ This information is not covered in our official policy documents."
            send_text(sender, not_found_msg)
            trace_rag_interaction(
                user_id=employee_id,
                session_id=session_id,
                query_text=query,
                response_text=not_found_msg,
                latency_ms=latency_ms,
                cache_hit=False,
                tokens_used={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                retrieved_chunks=[],
                model_name="none",
            )
            return

        context, citation_footer, top_docs = math_rrf_rerank(dense_docs, sparse_docs, top_k=3)

        # 3. Grounded Prompt Formulation
        prompt = f"""You are an AI HR Assistant. Answer the question using ONLY the provided HR Policy Excerpts.
DO NOT use outside knowledge or make assumptions. If the answer is not explicitly stated in the excerpts, respond ONLY with:
"❌ This information is not covered in our official policy documents."

STRICT FORMATTING:
1. Start directly with "📋 *Policy Information*" followed by "━━━━━━━━━━━━━━━━━━━".
2. Output a MAXIMUM of 4 bullet points formatted as: • *Category:* Exact detail from excerpt.
3. No introductory conversational text or conversational closures.

---
POLICY EXCERPTS:
{context}

---
CHAT HISTORY:
{chat_history_str}

---
EMPLOYEE QUESTION:
{query}

Card Response:"""

        response_text, tokens_used, model_used = execute_llm_with_backoff_failover(gemini_client, prompt)

        if not response_text or "❌" in response_text:
            final_response = "❌ This information is not covered in our official policy documents."
            send_text(sender, final_response)
        else:
            save_semantic_cached_answer(query, response_text, citation_footer)
            add_to_chat_history(employee_id, query, response_text)
            send_text(sender, f"{response_text}{citation_footer}")

        latency_ms = (time.time() - start_time) * 1000
        trace_rag_interaction(
            user_id=employee_id,
            session_id=session_id,
            query_text=query,
            response_text=response_text or "Not covered",
            latency_ms=latency_ms,
            cache_hit=False,
            tokens_used=tokens_used,
            retrieved_chunks=[d.page_content for d in top_docs],
            model_name=model_used,
        )

    except Exception:
        logger.exception(f"RAG_FATAL_ERROR | user={employee_id}")
        send_text(sender, "❌ An error occurred while retrieving policy details. Please try again later.")