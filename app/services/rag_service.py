"""Advanced RAG Service for HR Policy Queries.

Includes Query Expansion, Hybrid FAISS + BM25 Search, Math RRF Re-Ranking,
Semantic Redis Caching, Exponential Backoff with Jitter, Dynamic Groq Failover,
Artifact/Reasoning Stripping, Clean Cache Guard, and Langfuse Observability.
Location: app/services/rag_service.py
"""

import logging
import os
import random
import re
import time
from typing import Optional, Tuple
from google.genai import types
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
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
    """Generates 2 query variations to improve search recall with quick exponential backoff."""
    prompt = f"""
Generate 2 alternative search queries for an HR policy search.
Original Query: "{query}"
Output format: Return ONLY the queries separated by newlines, no bullet points or extra text.
"""
    for attempt in range(2):
        try:
            res = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=100,
                ),
            )
            variations = [
                q.strip() for q in res.text.strip().split("\n") if q.strip()
            ]
            return [query] + variations[:2]
        except Exception as e:
            if attempt == 0:
                sleep_time = 0.3 + random.uniform(0.1, 0.3)
                time.sleep(sleep_time)
            else:
                logger.warning(f"MULTI_QUERY_EXPANSION_SKIPPED | {e}")
                return [query]

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
        d_docs = faiss_store.similarity_search(q, k=top_k)
        dense_docs.extend(d_docs)

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


def clean_reasoning_and_artifacts(text: str) -> str:
    """Strips <think> tags and excessive repeated divider artifacts."""
    if not text:
        return ""
    # Strip thinking blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
    # Collapse repetitive horizontal lines
    cleaned = re.sub(r"[━─-]{10,}", "━━━━━━━━━━━━━━━━━━━", cleaned)
    return cleaned.strip()


def get_active_groq_model() -> Optional[str]:
    """Dynamically identifies the best active English-capable general text model on Groq."""
    if not groq_client:
        return None
    try:
        models = groq_client.models.list()
        # Exclude audio, guard, and non-English/specialized models
        active_ids = [
            m.id for m in models.data 
            if not any(x in m.id.lower() for x in ["whisper", "guard", "allam", "vision"])
        ]
        
        preferred_order = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "qwen-2.5-32b",
            "deepseek-r1-distill-llama-70b",
        ]
        for pref in preferred_order:
            if pref in active_ids:
                return pref
        return active_ids[0] if active_ids else "llama-3.1-8b-instant"
    except Exception as e:
        logger.warning(f"GROQ_MODEL_DISCOVERY_FAILED | {e}")
        return "llama-3.1-8b-instant"


def is_valid_hr_response(text: str) -> bool:
    """Validates that the generated response actually contains policy text."""
    if not text or len(text.strip()) < 40:
        return False
    meaningful_text = re.sub(r"[📋📌•\s━─-*]+", "", text)
    return len(meaningful_text) > 30


def execute_llm_with_backoff_failover(
    gemini_client, prompt: str
) -> Tuple[Optional[str], dict, str]:
    """Cascading Execution: Gemini 2.5 Flash -> Groq with Output Quality Guard."""

    # ---------------------------------------------------------
    # Tier 1: Gemini 2.5 Flash (Primary)
    # ---------------------------------------------------------
    max_retries = 2
    base_delay = 0.5

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
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
            jitter = random.uniform(0.1, 0.4)
            sleep_duration = (base_delay * (2 ** attempt)) + jitter
            logger.warning(
                f"GEMINI_ATTEMPT_{attempt+1}_FAILED | Retrying in {sleep_duration:.2f}s | error={err}"
            )
            time.sleep(sleep_duration)

    # ---------------------------------------------------------
    # Tier 2: Filtered Groq Failover
    # ---------------------------------------------------------
    if groq_client:
        selected_model = get_active_groq_model()
        if selected_model:
            for attempt in range(2):
                try:
                    logger.info(f"ROUTING_TO_SECONDARY_LLM | groq/{selected_model} ⚡")
                    chat_completion = groq_client.chat.completions.create(
                        model=selected_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=1024,
                    )
                    raw_content = chat_completion.choices[0].message.content or ""
                    cleaned_content = clean_reasoning_and_artifacts(raw_content)
                    
                    if is_valid_hr_response(cleaned_content):
                        usage = chat_completion.usage
                        tokens_used = {
                            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                        }
                        return cleaned_content, tokens_used, selected_model
                    else:
                        logger.warning(f"GROQ_OUTPUT_INVALID_OR_EMPTY ({selected_model})")
                except Exception as groq_err:
                    jitter = random.uniform(0.1, 0.3)
                    sleep_duration = (0.4 * (2 ** attempt)) + jitter
                    logger.warning(f"GROQ_ATTEMPT_{attempt+1}_FAILED ({selected_model}) | {groq_err}")
                    time.sleep(sleep_duration)

    return None, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "none"


def handle_rag_query(employee, query: str, collection_unused, gemini_client):
    """Handles end-to-end RAG query flow with Semantic Cache, Hybrid RAG, Multi-LLM Failover, and Langfuse Tracing."""
    start_time = time.time()
    sender = employee["whatsapp"]
    employee_id = employee.get("employee_id") or "UNKNOWN_EMP"
    session_id = f"session_{employee_id}"
    logger.info(f"RAG_QUERY_START | user={employee_id}")

    try:
        # =========================================================================
        # 1. SEMANTIC CACHE PROBE (Sub-50ms Fast Path)
        # =========================================================================
        cached_response, cached_footer = get_semantic_cached_answer(query)
        if cached_response:
            latency_ms = (time.time() - start_time) * 1000
            add_to_chat_history(employee_id, query, cached_response)
            final_whatsapp_msg = f"{cached_response}{cached_footer}"
            send_text(sender, final_whatsapp_msg)

            # Langfuse Trace: Cache Hit
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
            logger.info(
                f"RAG_QUERY_SUCCESS (FROM_CACHE) ⚡ | user={employee_id} | latency={latency_ms:.1f}ms"
            )
            return

        # =========================================================================
        # 2. FULL RAG RETRIEVAL (Executed on Cache Miss)
        # =========================================================================
        chat_history_str = get_chat_history(employee_id, max_messages=4)
        expanded_queries = multi_query_expansion(query, gemini_client)
        dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(
            expanded_queries, top_k=6
        )

        if not all_retrieved:
            latency_ms = (time.time() - start_time) * 1000
            no_info_msg = "❌ I couldn't find relevant information in the company policy documents."
            send_text(sender, no_info_msg)

            trace_rag_interaction(
                user_id=employee_id,
                session_id=session_id,
                query_text=query,
                response_text=no_info_msg,
                latency_ms=latency_ms,
                cache_hit=False,
                tokens_used={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                retrieved_chunks=[],
                model_name="none",
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

        # 3. Multi-LLM Execution with Jittered Backoff
        response_text, tokens_used, model_used = execute_llm_with_backoff_failover(gemini_client, prompt)

        # 4. Fallback Handling & Clean Semantic Cache Guard
        if not response_text or model_used == "none":
            # Both LLMs failed -> Serve raw excerpts to user without saving to Redis cache
            response_text, citation_footer = format_raw_chunks_fallback(all_retrieved)
        else:
            # ONLY save to Redis semantic cache when generated by Gemini or Groq
            save_semantic_cached_answer(query, response_text, citation_footer)

        # 5. Save to Conversation Memory
        if response_text:
            add_to_chat_history(employee_id, query, response_text)

        # 6. Deliver WhatsApp Message
        final_whatsapp_msg = f"{response_text}{citation_footer}"
        send_text(sender, final_whatsapp_msg)

        latency_ms = (time.time() - start_time) * 1000

        # 7. Log Observability Trace to Langfuse
        trace_rag_interaction(
            user_id=employee_id,
            session_id=session_id,
            query_text=query,
            response_text=response_text,
            latency_ms=latency_ms,
            cache_hit=False,
            tokens_used=tokens_used,
            retrieved_chunks=[d.page_content for d in top_docs],
            model_name=model_used,
        )

        logger.info(
            f"RAG_QUERY_SUCCESS ({model_used}) | user={employee_id} | latency={latency_ms:.1f}ms"
        )

    except Exception as e:
        logger.exception(f"RAG_FATAL_ERROR | user={employee_id}")
        send_text(
            sender,
            "❌ An error occurred while retrieving policy details. Please try again later.",
        )