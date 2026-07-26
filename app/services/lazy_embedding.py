"""app/services/lazy_embedding.py."""

import logging
import os
import chromadb
from google import genai

logger = logging.getLogger(__name__)


class LazyEmbeddingFunction(chromadb.EmbeddingFunction):

    def __init__(self, api_key=None, model_name="gemini-embedding-001"):
        # Default to the active gemini-embedding-001 model
        self.model_name = model_name.replace("models/", "")
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._client = None
        logger.info(
            f"[EMBEDDING] Initialized Gemini Embedding Function with model: {self.model_name}"
        )

    def name(self) -> str:
        """Required by modern ChromaDB to validate custom embedding functions."""
        return "lazy_gemini_embedding"

    def _ensure_client(self):
        if not self._client:
            if not self.api_key:
                logger.error("[EMBEDDING] ❌ GEMINI_API_KEY missing")
                raise ValueError("GEMINI_API_KEY is required")
            try:
                self._client = genai.Client(api_key=self.api_key)
                logger.info(
                    "[EMBEDDING] ✅ Gemini client initialized for embeddings"
                )
            except Exception as e:
                logger.error(f"[EMBEDDING] ❌ Failed to init Gemini client: {e}")
                raise

    def __call__(self, input: list[str]) -> list[list[float]]:
        self._ensure_client()
        if not input:
            return []

        if isinstance(input, str):
            input = [input]

        try:
            response = self._client.models.embed_content(
                model=self.model_name, contents=input
            )
            return [e.values for e in response.embeddings]
        except Exception as e:
            logger.error(f"[EMBEDDING] ❌ API Error during embedding: {e}")
            raise