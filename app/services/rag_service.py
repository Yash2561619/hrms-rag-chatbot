"""Advanced RAG Service for HR Policy Queries.

Includes Native PostgreSQL pgvector + BM25 Hybrid Search, Math RRF Re-Ranking,
Semantic Redis Caching, Instant Groq Failover on 429/503 Quotas, Raw Chunk Fallback,
Balanced Similarity Guards, In-Memory Reset on Force Reload, and Langfuse Observability.

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

DATABASE_URL = os.getenv("DATABASE_URL")
embeddings_model = FastEmbedEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)
bm25_index = None
all_docs = []

# Lowered threshold to ensure natural-language queries match policy sections
DENSE_SIMILARITY_THRESHOLD = 0.22


def load_indexes(force_reload: bool = False):
    """Loads documents from PostgreSQL pgvector into RAM for BM25 sparse matching."""
    global bm25_index, all_docs

    if (bm25_index is None or force_reload) and DATABASE_URL:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            cur.execute("SELECT content, metadata FROM policy_vectors;")
            rows = cur.fetchall()

            # Completely reset in-memory document list
            fresh_docs = [
                Document(page_content=r[0], metadata=r[1] if r[1] else {})
                for r in rows
            ]
            all_docs = fresh_docs

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
                logger.info("PGVECTOR_AND_BM25_CLEARED ✅ | 0 documents in index")

            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"PGVECTOR_LOAD_ERROR | {e}")


def pgvector_dense_search(query_vec: List[float], top_k: int = 8) -> List[Document]:
    """Performs native HNSW cosine distance search (<=>) directly in PostgreSQL."""
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
    if not gemini_client:
        return [query]

    prompt = f"""Generate 2 alternative search queries for an HR policy search.
Original Query: "{query}"
Output format: Return ONLY the queries separated by newlines, no bullet points or extra text."""
    try:
        res = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=60,
                top_p=0.9,
            ),
        )
        if res and res.text:
            variations = [
                q.strip() for q in res.text.strip().split("\n") if q.strip()
            ]
            return [query] + variations[:2]
    except Exception as e:
        logger.warning(f"MULTI_QUERY_EXPANSION_SKIPPED | {e}")

    return [query]


def hybrid_retrieve(
    queries: list[str], top_k: int = 8
) -> tuple[list, list, list]:
    """Executes pgvector (Dense) + BM25 (Sparse) search with relevance filters."""
    load_indexes()
    dense_docs = []
    sparse_docs = []

    for q in queries:
        # 1. Native Postgres pgvector dense retrieval
        q_vec = list(embeddings_model.embed_query(q))
        d_docs = pgvector_dense_search(q_vec, top_k=top_k)
        dense_docs.extend(d_docs)

        # 2. In-memory BM25 sparse retrieval
        if bm25_index and all_docs:
            tokenized_q = q.lower().split()
            bm25_scores = bm25_index.get_scores(tokenized_q)
            positive_indices = [
                i for i, score in enumerate(bm25_scores) if score > 0.0
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
    dense_chunks: list, bm25_chunks: list, top_k: int = 5
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


def format_raw_chunks_fallback(chunks: list) -> tuple[str, str]:
    """Cleans and formats retrieved chunks into a bulleted list if all LLMs fail."""
    if not chunks:
        return (
            "❌ I couldn't find any relevant policy information for your question.",
            "",
        )

    clean_sentences = []
    seen = set()
    sources_set = set()

    for doc in chunks[:5]:
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
                if len(clean_sentences) >= 6:
                    break
        if len(clean_sentences) >= 6:
            break

    bullet_points = (
        "\n".join([f"• {s}." for s in clean_sentences])
        if clean_sentences
        else "• Details are available in the official policy document."
    )
    citation_footer = (
        "\n━━━━━━━━━━━━━━━━━━━\n📁 *Source:* " + ", ".join(sorted(sources_set))
        if sources_set
        else ""
    )

    fallback_text = (
        "📋 *Policy Excerpts (High Traffic Mode)*\n━━━━━━━━━━━━━━━━━━━\n\n"
        f"{bullet_points}\n\n"
        "_Please ask again in a moment for a synthesized summary._"
    )

    return fallback_text, citation_footer


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
    """Discovers available models on active Groq account with reliable fallbacks."""
    if not groq_client:
        return None
    try:
        models = groq_client.models.list()
        active_ids = [m.id for m in models.data]
        for pref in [
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-120b",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-20b",
            "groq/compound-mini",
            "qwen/qwen3.6-27b",
        ]:
            if pref in active_ids:
                return pref
        return "llama-3.3-70b-versatile"
    except Exception:
        return "llama-3.3-70b-versatile"


def is_valid_hr_response(text: str) -> bool:
    """Validates that the generated response contains meaningful text."""
    if not text or len(text.strip()) < 20:
        return False
    return True


def execute_llm_with_backoff_failover(
    gemini_client, prompt: str
) -> Tuple[Optional[str], dict, str]:
    """Cascading Execution: Gemini 2.5 Flash -> Dynamic Groq with Instant 429/503 Failover."""

    # ---------------------------------------------------------
    # Tier 1: Gemini 2.5 Flash
    # ---------------------------------------------------------
    if gemini_client:
        for attempt in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=900,
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
                err_str = str(err)
                if (
                    "429" in err_str
                    or "503" in err_str
                    or "RESOURCE_EXHAUSTED" in err_str
                    or "UNAVAILABLE" in err_str
                ):
                    logger.warning("GEMINI_UNAVAILABLE_OR_QUOTA_EXHAUSTED | Immediate failover to Groq ⚡")
                    break  # Failover immediately without sleeping

                sleep_duration = (0.5 * (2**attempt)) + random.uniform(0.1, 0.4)
                logger.warning(f"GEMINI_RETRY | attempt={attempt+1} | error={err}")
                time.sleep(sleep_duration)

    # ---------------------------------------------------------
    # Tier 2: Groq Failover
    # ---------------------------------------------------------
    if groq_client:
        selected_model = get_active_groq_model()
        if selected_model:
            try:
                logger.info(f"ROUTING_TO_SECONDARY_LLM | groq/{selected_model} ⚡")
                chat_completion = groq_client.chat.completions.create(
                    model=selected_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=900,
                )
                raw_content = chat_completion.choices[0].message.content or ""
                cleaned_content = clean_reasoning_and_artifacts(raw_content)
                if is_valid_hr_response(cleaned_content):
                    return cleaned_content, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, selected_model
            except Exception as groq_err:
                logger.warning(f"GROQ_FAILOVER_ERROR | {groq_err}")

    return None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "none"


def handle_rag_query(employee, query: str, collection_unused, gemini_client):
    """Handles end-to-end RAG query flow with Cache, pgvector, LLMs, and Raw Chunks Fallback."""
    start_time = time.time()
    sender = employee["whatsapp"]
    employee_id = employee.get("employee_id") or "UNKNOWN_EMP"
    session_id = f"session_{employee_id}"
    logger.info(f"RAG_QUERY_START | user={employee_id}")

    try:
        # 1. Semantic Cache Probe
        cached_response, cached_footer = get_semantic_cached_answer(query)
        if cached_response and "❌" not in cached_response:
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
                retrieved_chunks=[],
                model_name="semantic_cache",
            )
            return

        # 2. Hybrid Retrieval with Score Filter
        chat_history_str = get_chat_history(employee_id, max_messages=4)
        expanded_queries = multi_query_expansion(query, gemini_client)
        dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(expanded_queries, top_k=8)

        # Early Guard: If no vector or keyword chunks were retrieved at all
        if not all_retrieved:
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

        # Rerank and pick top 5 most relevant excerpts
        context, citation_footer, top_docs = math_rrf_rerank(dense_docs, sparse_docs, top_k=5)

        if not context.strip():
            not_found_msg = "❌ This information is not covered in our official policy documents."
            send_text(sender, not_found_msg)
            return

        # Balanced system prompt that provides detailed answers while avoiding false refusals
        prompt = f"""You are an expert HR Policy Assistant. Answer the employee question accurately using the Policy Excerpts below.

RULES:
1. Explain the relevant policies, conditions, eligibility, and rules clearly from the excerpts.
2. If the excerpts truly contain NO relevant information for "{query}", respond ONLY with:
"❌ This information is not covered in our official policy documents."
3. Format the response clearly:
   📋 *Policy Details*
   ━━━━━━━━━━━━━━━━━━━
   • *Topic/Rule:* Clear explanation with numbers, limits, conditions, or steps.
   • *Procedure / Note:* Any required approvals, consequences, or guidelines.

---
POLICY EXCERPTS:
{context}

---
EMPLOYEE QUESTION:
{query}

Response:"""

        # 3. Cascading LLM Execution
        response_text, tokens_used, model_used = execute_llm_with_backoff_failover(gemini_client, prompt)

        # 4. Handle Result & Clean Cache Guard
        if not response_text or "❌" in response_text or "not covered" in response_text.lower():
            final_whatsapp_msg = "❌ This information is not covered in our official policy documents."
            send_text(sender, final_whatsapp_msg)
        elif model_used == "none":
            # Both LLMs failed -> Serve raw chunks fallback
            logger.warning(f"LLM_OUTAGE_TRIGGERED_RAW_FALLBACK | user={employee_id}")
            raw_fallback_text, raw_footer = format_raw_chunks_fallback(top_docs)
            final_whatsapp_msg = f"{raw_fallback_text}{raw_footer}"
            send_text(sender, final_whatsapp_msg)
            response_text = raw_fallback_text
        else:
            save_semantic_cached_answer(query, response_text, citation_footer)
            add_to_chat_history(employee_id, query, response_text)
            final_whatsapp_msg = f"{response_text}{citation_footer}"
            send_text(sender, final_whatsapp_msg)

        latency_ms = (time.time() - start_time) * 1000
        trace_rag_interaction(
            user_id=employee_id,
            session_id=session_id,
            query_text=query,
            response_text=response_text or "No answer",
            latency_ms=latency_ms,
            cache_hit=False,
            tokens_used=tokens_used,
            retrieved_chunks=[d.page_content for d in top_docs],
            model_name=model_used,
        )

    except Exception:
        logger.exception(f"RAG_FATAL_ERROR | user={employee_id}")
        send_text(
            sender,
            "❌ An error occurred while retrieving policy details. Please try again later.",
        )