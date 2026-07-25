import os
import time
import logging
from typing import  Any, Dict

from config import Config
from database import log_activity
from google.genai.errors import ClientError

from app.services.whatsapp_service import send_text
from app.services.intent_service import classify_intent
from app.services.response_service import format_hr_response

logger = logging.getLogger(__name__)

POLICY_FOLDER = Config.POLICY_FOLDER




def handle_rag_query(
    employee: Dict[str, Any],
    message: str,
    collection: Any,
    gemini_client: Any,
    reranker: Any,
) -> None:

    
    sender = employee["whatsapp"]
    employee_id = employee["employee_id"]

    try:
        logger.info(
            f"RAG_QUERY_START | user={employee_id} | question={message[:80]}"
        )

        # =====================================================
        # 1. INPUT VALIDATION
        # =====================================================

        if not message or len(message.strip()) < 3:
            send_text(sender, "⚠️ Please ask a more specific question.")
            return

        if collection is None:
            logger.error("CHROMA_COLLECTION_NONE")
            send_text(
                sender, "❌ Knowledge base is not available right now."
            )
            return

        if gemini_client is None:
            logger.error("GEMINI_CLIENT_NONE")
            send_text(sender, "❌ AI service is not available right now.")
            return

        # =====================================================
        # 2. INTENT CLASSIFICATION
        # =====================================================

        intent = classify_intent(message)
        logger.info(
            f"RAG_INTENT | user={employee_id} | intent={intent}"
        )
        

        # =====================================================
        # 3. CHROMA COLLECTION HEALTH CHECK
        # =====================================================

        try:
            chunk_count = collection.count()
            logger.info(
                f"RAG_COLLECTION_COUNT | user={employee_id} | chunks={chunk_count}"
            )

            if chunk_count == 0:
                logger.warning(
                    f"RAG_EMPTY_COLLECTION | user={employee_id}"
                )
                send_text(
                    sender,
                    "📚 The HR policy knowledge base is currently empty. Please contact the HR administrator.",
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
        # 4. CHROMA VECTOR SEARCH WITH FALLBACK
        # =====================================================

        results = None
        n_results = min(10, chunk_count)

        # Attempt 1: Metadata-filtered search
        

        # Attempt 2: Global vector search fallback
        if (
            not results
            or "documents" not in results
            or not results["documents"]
            or not results["documents"][0]
        ):
            try:
                logger.info(
                    f"RAG_GLOBAL_SEARCH | n_results={n_results}"
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
            logger.warning(
                f"RAG_NO_RESULTS | user={employee_id}"
            )
            send_text(
                sender,
                "📚 I could not find relevant information in the HR policies.",
            )
            return

        # =====================================================
        # 5. DISTANCE THRESHOLD GUARDRAIL
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
        # 6. RE-RANKING VIA CROSS-ENCODER
        # =====================================================

        documents = results["documents"][0]
        metadatas = results["metadatas"][0]

        pairs = [[message, doc] for doc in documents]
        scores = reranker.compute_score(pairs)

        ranked = sorted(
            zip(scores, documents, metadatas),
            key=lambda x: x[0],
            reverse=True,
        )

        # Select top 3 re-ranked chunks
        top_docs = [doc for _, doc, _ in ranked[:3]]
        top_metadata = [meta for _, _, meta in ranked[:3]]

        context = "\n\n".join(top_docs)
        results["metadatas"][0] = top_metadata

        # =====================================================
        # 7. METADATA SOURCE EXTRACTION
        # =====================================================

        sources = []
        if results.get("metadatas"):
            for metadata in results["metadatas"][0]:
                if metadata:
                    source = metadata.get("source", "Unknown Policy")
                    if source not in sources:
                        sources.append(source)

        logger.info(
            f"RAG_SOURCES | user={employee_id} | count={len(sources)}"
        )

        # =====================================================
        # 8. PROMPT CONSTRUCT
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
        # 9. LLM GENERATION WITH RETRY (EXPONENTIAL BACKOFF)
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
                # Handle 429 Rate Limits using exponential backoff
                if "429" in str(e):
                    logger.warning(
                        f"GEMINI_429 | user={employee_id} | attempt={attempt + 1} | wait={backoff}s"
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
        # 10. RESPONSE FORMATTING & PAYLOAD TRUNCATION
        # =====================================================

        if response and response.text:
            raw_answer = response.text.strip()
        else:
            # Fallback to direct raw retrieved context if LLM call fails
            logger.warning(
                f"GEMINI_FALLBACK_USED | user={employee_id}"
            )
            fallback_text = context[:600].strip()
            raw_answer = (
                "⚠️ The AI service is temporarily busy, but I found relevant HR policy information:\n\n"
                f"{fallback_text}"
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
        # 11. DISPATCH RESPONSE & LOGGING
        # =====================================================

        send_text(sender, answer)
        log_activity(
            f"RAG_QUERY | {employee['name']} | {message[:50]}"
        )
        logger.info(
            f"RAG_QUERY_SUCCESS | user={employee_id}"
        )

    except Exception:
        logger.exception(
            f"RAG_FATAL_ERROR | user={employee_id}"
        )
        send_text(
            sender,
            "❌ Sorry, something went wrong while searching the HR policies.",
        )