"""
Lazy Gemini Embedding Function for ChromaDB.

Location: app/services/lazy_embedding.py
"""

import logging
import os
from chromadb import Documents, EmbeddingFunction, Embeddings
from google import genai

logger = logging.getLogger(__name__)


class LazyEmbeddingFunction(EmbeddingFunction):

  def __init__(self, api_key=None, model_name="gemini-embedding-001"):
    # Strip model prefix if provided
    self.model_name = model_name.replace("models/", "")
    self.api_key = (
        api_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    self._client = None
    logger.info(
        "[EMBEDDING] Initialized Gemini Embedding Function with model:"
        f" {self.model_name}"
    )

  def name(self) -> str:
    """Required by modern ChromaDB to uniquely identify custom embedding functions."""
    return "lazy_gemini_embedding"

  @property
  def client(self):
    """Lazy-instantiates the Gemini Client on demand to optimize RAM during app boot."""
    if self._client is None:
      if not self.api_key:
        logger.error("[EMBEDDING] ❌ GEMINI_API_KEY is missing!")
        raise ValueError(
            "GEMINI_API_KEY or GOOGLE_API_KEY environment variable is required."
        )

      try:
        self._client = genai.Client(api_key=self.api_key)
        logger.info(
            "[EMBEDDING] ✅ Gemini client initialized for embeddings"
        )
      except Exception as e:
        logger.error(f"[EMBEDDING] ❌ Failed to init Gemini client: {e}")
        raise e

    return self._client

  def __call__(self, input: Documents) -> Embeddings:
    """Generate vector embeddings via the Gemini API."""
    if not input:
      return []

    # Handle string or list inputs cleanly
    if isinstance(input, str):
      input = [input]

    try:
      response = self.client.models.embed_content(
          model=self.model_name, contents=input
      )
      return [e.values for e in response.embeddings]
    except Exception as e:
      logger.error(f"[EMBEDDING] ❌ API Error during embedding generation: {e}")
      raise e