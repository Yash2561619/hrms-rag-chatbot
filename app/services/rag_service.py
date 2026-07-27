"""RAG Service for WhatsApp HR Assistant.

Location: app/services/rag_service.py
"""

import logging
import os
import time
from typing import Any, Dict
import chromadb
from google.genai.errors import ClientError

from app.services.lazy_embedding import LazyEmbeddingFunction
from app.services.response_service import format_hr_response
from app.services.s3_service import sync_chroma_from_s3
from app.services.whatsapp_service import send_text
from config import Config
from database import log_activity

logger = logging.getLogger(__name__)

POLICY_FOLDER = Config.POLICY_FOLDER


def get_chroma_collection() -> Any:
  """Lazy-load ChromaDB collection and sync from S3 if missing locally."""
  try:
    # Ensure local vector store is synced from S3 before attaching client
    if not os.path.exists("chroma_db") or not os.listdir("chroma_db"):
      logger.info("LOCAL_CHROMA_MISSING | Triggering sync_chroma_from_s3()...")
      sync_chroma_from_s3()

    client = chromadb.PersistentClient(path="chroma_db")
    embedding_fn = LazyEmbeddingFunction(model_name="gemini-embedding-001")
    return client.get_or_create_collection(
        name="hr_policies", embedding_function=embedding_fn
    )
  except Exception as e:
    logger.error(f"LAZY_CHROMA_INIT_FAILED | error={e}")
    return None


def handle_rag_query(
    employee: Dict[str, Any],
    message: str,
    collection: Any,
    gemini_client: Any,
) -> None:

  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    logger.info(
        f"RAG_QUERY_START | user={employee_id} | question={message[:80]}"
    )

    # =====================================================
    # 1. INPUT VALIDATION & LAZY INITIALIZATION
    # =====================================================
    if not message or len(message.strip()) < 3:
      send_text(sender, "⚠️ Please ask a more specific question.")
      return

    if collection is None:
      logger.warning("CHROMA_COLLECTION_NONE | Attempting lazy load...")
      collection = get_chroma_collection()

    if collection is None:
      logger.error("CHROMA_COLLECTION_STILL_NONE")
      send_text(
          sender,
          (
              "⏳ The HR knowledge base is currently initializing. Please try"
              " asking again in a few seconds."
          ),
      )
      return

    if gemini_client is None:
      logger.error("GEMINI_CLIENT_NONE")
      send_text(sender, "❌ AI service is not available right now.")
      return

    # =====================================================
    # 2. CHROMA COLLECTION HEALTH CHECK
    # =====================================================
    try:
      chunk_count = collection.count()
      logger.info(
          f"RAG_COLLECTION_COUNT | user={employee_id} | chunks={chunk_count}"
      )

      if chunk_count == 0:
        logger.warning(f"RAG_EMPTY_COLLECTION | user={employee_id}")
        send_text(
            sender,
            (
                "📚 The HR policy knowledge base is currently empty. Please"
                " contact the HR administrator."
            ),
        )
        return

    except Exception:
      logger.exception("RAG_COLLECTION_CHECK_FAILED")
      send_text(
          sender,
          "❌ Unable to access the HR knowledge base right now.",
      )
      return

    # =====================================================
    # 3. CHROMA VECTOR SEARCH
    # =====================================================
    results = None
    n_results = min(10, chunk_count)

    try:
      logger.info(
          f"RAG_GLOBAL_SEARCH | user={employee_id} | n_results={n_results}"
      )
      results = collection.query(
          query_texts=[message],
          n_results=n_results,
          include=["documents", "metadatas", "distances"],
      )
    except Exception:
      logger.exception("CHROMA_QUERY_FAILED")
      send_text(
          sender,
          "❌ Unable to search HR policies right now.",
      )
      return

    # Handle empty document results
    if (
        not results
        or "documents" not in results
        or not results["documents"]
        or not results["documents"][0]
    ):
      logger.warning(f"RAG_NO_RESULTS | user={employee_id}")
      send_text(
          sender,
          "📚 I could not find relevant information in the HR policies.",
      )
      return

    # =====================================================
    # 4. DISTANCE THRESHOLD GUARDRAIL
    # =====================================================
    try:
      distances = results.get("distances", [[]])[0]
      best_distance = distances[0] if distances else None

      logger.info(
          f"RAG_DISTANCE | user={employee_id} | best={best_distance}"
      )

      # High distance indicates low semantic relevance
      if best_distance is None or best_distance > 1.2:
        logger.warning(
            f"RAG_LOW_CONFIDENCE | user={employee_id} | distance={best_distance}"
        )
        send_text(
            sender,
            "📚 I couldn't find relevant information in the HR policies.",
        )
        return

    except Exception:
      logger.warning("RAG_DISTANCE_CHECK_SKIPPED")

    # =====================================================
    # 5. SELECT TOP CHUNKS & CONTEXT PREPARATION
    # =====================================================
    documents = results["documents"][0]
    metadatas = results["metadatas"][0] if results.get("metadatas") else []

    top_docs = documents[:3]
    top_metadata = metadatas[:3] if metadatas else []

    context = "\n\n".join(top_docs)

    # =====================================================
    # 6. METADATA SOURCE EXTRACTION
    # =====================================================
    sources = []
    if top_metadata:
      for metadata in top_metadata:
        if metadata:
          source = metadata.get("source", "Unknown Policy")
          if source not in sources:
            sources.append(source)

    logger.info(f"RAG_SOURCES | user={employee_id} | count={len(sources)}")

    # =====================================================
    # 7. PROMPT CONSTRUCT
    # =====================================================
    prompt = f"""
You are ApexHR, a professional HR assistant for employees.

INSTRUCTIONS:
1. Answer using ONLY the HR policy context provided below.
2. Be concise and professional.
3. Use bullet points for rules, limits, and benefits.
4. Mention exact numbers, dates, or percentages when available.
5. If the policy does not contain the answer, reply exactly:
   "I couldn't find this information in the HR policies."
6. Never invent or guess HR rules.

EMPLOYEE QUESTION:
{message}

HR POLICY CONTEXT:
{context}

FINAL ANSWER:
"""

    # =====================================================
    # 8. LLM GENERATION WITH RETRY (EXPONENTIAL BACKOFF)
    # =====================================================
    response = None
    backoff = 2

    for attempt in range(3):
      try:
        logger.info(
            f"GEMINI_ATTEMPT | user={employee_id} | attempt={attempt + 1}"
        )

        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "temperature": 0.2,
                "max_output_tokens": 300,
            },
        )

        if response and response.text:
          break

      except ClientError as e:
        if "429" in str(e):
          logger.warning(
              f"GEMINI_429 | user={employee_id} | attempt={attempt + 1} |"
              f" wait={backoff}s"
          )

          if attempt < 2:
            time.sleep(backoff)
            backoff *= 2
            continue

        logger.exception("GEMINI_CLIENT_ERROR")
        response = None
        break

      except Exception:
        logger.exception("GEMINI_UNKNOWN_ERROR")
        response = None
        break

    # =====================================================
    # 9. RESPONSE FORMATTING & PAYLOAD TRUNCATION
    # =====================================================
    if response and response.text:
      raw_answer = response.text.strip()
    else:
      logger.warning(f"GEMINI_FALLBACK_USED | user={employee_id}")
      fallback_text = context[:600].strip()
      raw_answer = (
          "⚠️ The AI service is temporarily busy, but I found relevant HR policy"
          f" information:\n\n{fallback_text}"
      )

    answer = format_hr_response(raw_answer)

    # Enforce maximum character boundary for WhatsApp payload delivery
    if len(answer) > 1500:
      answer = answer[:1500] + "\n\n[Message truncated]"

    # Format and attach unique source file attributions
    if sources:
      answer += "\n\n📄 Source Documents:\n"
      for src in sources:
        clean_name = src.replace("_", " ").replace(".pdf", "")
        answer += f"• {clean_name}\n"

    # =====================================================
    # 10. DISPATCH RESPONSE & LOGGING
    # =====================================================
    send_text(sender, answer)
    log_activity(
        f"RAG_QUERY | {employee.get('name', 'Employee')} | {message[:50]}"
    )
    logger.info(f"RAG_QUERY_SUCCESS | user={employee_id}")

  except Exception:
    logger.exception(f"RAG_FATAL_ERROR | user={employee_id}")
    send_text(
        sender,
        "❌ Sorry, something went wrong while searching the HR policies.",
    )