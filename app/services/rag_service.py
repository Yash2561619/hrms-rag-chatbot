import logging
import os
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from app.services.whatsapp_service import send_text

logger = logging.getLogger(__name__)

# Cached in-memory vector store reference
faiss_vector_store = None


def load_faiss_index():
  """Loads local FAISS index using API-based Gemini embeddings."""
  global faiss_vector_store
  if faiss_vector_store is None:
    try:
      api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
      embeddings = GoogleGenerativeAIEmbeddings(
          model="models/gemini-embedding-001", google_api_key=api_key
      )
      faiss_vector_store = FAISS.load_local(
          "faiss_index", embeddings, allow_dangerous_deserialization=True
      )
      logger.info("FAISS_INDEX_LOADED_SUCCESSFULLY ✅")
    except Exception as e:
      logger.error(f"FAISS_LOAD_ERROR | error={e}")
      faiss_vector_store = None
  return faiss_vector_store


def handle_rag_query(employee, message, collection, gemini_client):
  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    logger.info(f"RAG_QUERY_START | user={employee_id}")

    vector_store = load_faiss_index()
    if vector_store is None:
      send_text(
          sender,
          "⏳ Knowledge base is initializing. Please try again in a moment.",
      )
      return

    # Similarity search executes in <15ms
    docs = vector_store.similarity_search(message, k=3)

    if not docs:
      send_text(
          sender,
          "📚 I couldn't find relevant information in the HR policies.",
      )
      return

    context = "\n\n".join([doc.page_content for doc in docs])

    prompt = f"""
You are ApexHR, an HR assistant. Answer using ONLY the HR policy context below.
If not found, reply: "I couldn't find this information in the HR policies."

QUESTION: {message}
CONTEXT: {context}
ANSWER:
"""

    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"temperature": 0.2, "max_output_tokens": 300},
    )

    answer = (
        response.text.strip()
        if response and response.text
        else "⚠️ Service busy, please try again."
    )
    send_text(sender, answer)
    logger.info(f"RAG_QUERY_SUCCESS | user={employee_id}")

  except Exception as e:
    logger.exception(f"RAG_FATAL_ERROR | user={employee_id} | error={e}")
    send_text(
        sender, "❌ Something went wrong while searching the HR policies."
    )