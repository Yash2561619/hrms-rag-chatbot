"""
Gemini API Embedding Wrapper for ChromaDB.
Location: app/services/lazy_embedding.py

Uses Google's text-embedding-004 over HTTP to keep RAM under 100 MB
and eliminate native ONNX/PyTorch crashes (Status 132) on Render.
"""

import os
import logging
import traceback
from google import genai

logger = logging.getLogger(__name__)


class LazyEmbeddingFunction:
    """
    Wrapper for Gemini API embeddings compatible with ChromaDB.
    Replaces SentenceTransformers/ONNX models with API calls.
    """

    def __init__(self, api_key=None, model_name="text-embedding-004"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self._client = None
        logger.info(f"[EMBEDDING] Initialized Gemini Embedding Function with model: {self.model_name}")

    def _ensure_client(self):
        """Initialize Google GenAI client on first use."""
        if not self._client:
            if not self.api_key:
                logger.error("[EMBEDDING] ❌ GEMINI_API_KEY is missing!")
                raise ValueError("GEMINI_API_KEY environment variable is missing.")

            try:
                logger.info("[EMBEDDING] Instantiating Gemini client...")
                self._client = genai.Client(api_key=self.api_key)
                logger.info("[EMBEDDING] ✅ Gemini client initialized for embeddings")
            except Exception as e:
                logger.error(f"[EMBEDDING] ❌ Failed to initialize Gemini client: {e}")
                logger.error(traceback.format_exc())
                raise

    def __call__(self, input: list[str]) -> list[list[float]]:
        """
        Embed texts via Google Gemini API.
        Called automatically by ChromaDB during query/upsert operations.
        """
        self._ensure_client()

        if not input:
            return []

        try:
            # Handle single string input if Chroma passes a string instead of a list
            if isinstance(input, str):
                input = [input]

            response = self._client.models.embed_content(
                model=self.model_name,
                contents=input
            )

            # Return list of floating point vector embeddings
            return [e.values for e in response.embeddings]

        except Exception as e:
            logger.error(f"[EMBEDDING] ❌ API Error during embedding generation: {e}")
            logger.error(traceback.format_exc())
            raise