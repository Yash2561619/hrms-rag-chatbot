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
f
from app.services.s3_service import sync_chroma_from_s3
from app.services.whatsapp_service import send_text
from config import Config
from database import log_activity

logger = logging.getLogger(__name__)

POLICY_FOLDER = Config.POLICY_FOLDER


chroma_lock = threading.Lock()


def handle_rag_query(
    employee: dict, message: str, collection, gemini_client
) -> None:
  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    logger.info(f"RAG_QUERY_START | user={employee_id}")

    if not message or len(message.strip()) < 3:
      send_text(sender, "⚠️ Please ask a more specific question.")
      return

    if collection is None or gemini_client is None:
      send_text(
          sender,
          "⏳ Knowledge base is initializing. Please try again in a few"
          " seconds.",
      )
      return

    # 1. Generate query embedding directly via Gemini API (Fast & low RAM)
    logger.info(f"GENERATING_QUERY_EMBEDDING | user={employee_id}")
    embed_response = gemini_client.models.embed_content(
        model="gemini-embedding-001", contents=message
    )

    if not embed_response or not embed_response.embeddings:
      send_text(
          sender, "❌ Could not process query embedding. Please try again."
      )
      return

    query_vector = embed_response.embeddings[0].values

    # 2. Query ChromaDB using pre-calculated vector + Thread Lock
    logger.info(f"QUERYING_CHROMADB_VECTOR | user={employee_id}")
    with chroma_lock:
      results = collection.query(
          query_embeddings=[query_vector],
          n_results=3,  # Keep n_results low (3) to save RAM
          include=["documents", "metadatas", "distances"],
      )

    # 3. Process documents and distance threshold
    if not results or not results.get("documents") or not results["documents"][0]:
      send_text(
          sender,
          "📚 I couldn't find relevant information in the HR policies.",
      )
      return

    distances = results.get("distances", [[]])[0]
    best_distance = distances[0] if distances else 2.0

    if best_distance > 1.2:
      send_text(
          sender,
          "📚 I couldn't find relevant information in the HR policies.",
      )
      return

    documents = results["documents"][0]
    metadatas = results["metadatas"][0] if results.get("metadatas") else []
    context = "\n\n".join(documents[:3])

    # 4. Generate final answer with Gemini
    prompt = f"""
You are ApexHR, an HR assistant. Answer using ONLY the HR policy context below.
If not found, reply: "I couldn't find this information in the HR policies."

QUESTION: {message}
CONTEXT: {context}
ANSWER:
"""

    gen_response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"temperature": 0.2, "max_output_tokens": 300},
    )

    answer = (
        gen_response.text.strip()
        if gen_response and gen_response.text
        else "⚠️ Service busy, please try again."
    )

    # Append source attributions
    sources = list(
        {
            m.get("source", "Policy")
            for m in metadatas[:3]
            if m and "source" in m
        }
    )
    if sources:
      answer += "\n\n📄 Sources:\n" + "\n".join(
          f"• {s.replace('.pdf', '')}" for s in sources
      )

    send_text(sender, answer)
    logger.info(f"RAG_QUERY_SUCCESS | user={employee_id}")

  except Exception as e:
    logger.exception(f"RAG_QUERY_FATAL | user={employee_id} | error={e}")
    send_text(
        sender, "❌ Something went wrong while searching the HR policies."
    )