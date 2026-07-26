"""
Lazy-loading wrapper for SentenceTransformerEmbeddingFunction.
Only loads the model into memory when first query/upsert happens,
not during Flask startup.

Location: app/services/lazy_embedding.py
"""

import logging

logger = logging.getLogger(__name__)


class LazyEmbeddingFunction:
    """
    Wrapper that defers model loading until first use.
    Saves ~300-400 MB RAM at startup.
    
    The embedding model (BAAI/bge-small-en-v1.5) loads only when:
    - First RAG query is executed
    - First upsert to ChromaDB happens
    
    NOT at Flask startup or Chroma initialization.
    """
    
    def __init__(self, model_name="BAAI/bge-small-en-v1.5"):
        self.model_name = model_name
        self._embedding_fn = None
        self._loaded = False
        logger.info(f"LazyEmbeddingFunction initialized (model will load on first use): {model_name}")
    
    def _ensure_loaded(self):
        """Load model on first access."""
        if not self._loaded:
            logger.info(f"[LAZY_LOAD] Loading embedding model: {self.model_name}")
            try:
                from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
                self._embedding_fn = SentenceTransformerEmbeddingFunction(
                    model_name=self.model_name
                )
                self._loaded = True
                logger.info(f"[LAZY_LOAD] ✅ Embedding model loaded successfully")
            except Exception as e:
                logger.exception(f"[LAZY_LOAD] ❌ Failed to load embedding model: {e}")
                raise
    
    def __call__(self, input):
        """
        Embed texts when called.
        This is called by ChromaDB when querying or upserting.
        """
        self._ensure_loaded()
        return self._embedding_fn(input)