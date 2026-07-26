"""app/services/lazy_embedding.py."""

import logging
import os
import traceback
import chromadb
from google import genai

logger = logging.getLogger(__name__)


class LazyEmbeddingFunction(chromadb.EmbeddingFunction):

    def __init__(self, api_key=None, model_name="text-embedding-004"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model_name = model_name
        self._client = None

    def name(self) -> str:
        return "lazy_gemini_embedding"

    def _ensure_client(self):
        if not self._client:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY is missing")
            self._client = genai.Client(api_key=self.api_key)

    def __call__(self, input: list[str]) -> list[list[float]]:
        self._ensure_client()
        if not input:
            return []

        if isinstance(input, str):
            input = [input]

        try:
            # Ensure model name starts with models/ if using genai.Client
            target_model = (
                self.model_name
                if self.model_name.startswith("models/")
                else f"models/{self.model_name}"
            )

            response = self._client.models.embed_content(
                model=target_model, contents=input
            )
            return [e.values for e in response.embeddings]
        except Exception as e:
            logger.error(f"[EMBEDDING] ❌ API Error during embedding: {e}")
            raise