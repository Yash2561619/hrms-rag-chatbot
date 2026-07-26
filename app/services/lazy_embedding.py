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
        logger.info(f"[LAZY] LazyEmbeddingFunction initialized for model: {model_name}")
    
    def _ensure_loaded(self):
        """Load model on first access."""
        if not self._loaded:
            logger.info(f"[LAZY_LOAD] Loading embedding model: {self.model_name}")
            try:
                # Import the actual embedding function
                from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
                
                logger.info(f"[LAZY_LOAD] Instantiating SentenceTransformerEmbeddingFunction...")
                
                # Create the actual embedding function
                self._embedding_fn = SentenceTransformerEmbeddingFunction(
                    model_name=self.model_name
                )
                
                self._loaded = True
                logger.info(f"[LAZY_LOAD] ✅ Embedding model loaded successfully: {self.model_name}")
                
            except ImportError as ie:
                logger.error(f"[LAZY_LOAD] ❌ ImportError loading embedding function: {ie}")
                logger.error(f"[LAZY_LOAD] Make sure chromadb is installed with: pip install chromadb")
                raise
            except Exception as e:
                logger.error(f"[LAZY_LOAD] ❌ Failed to load embedding model: {e}")
                logger.error(f"[LAZY_LOAD] Model name: {self.model_name}")
                import traceback
                logger.error(f"[LAZY_LOAD] Traceback: {traceback.format_exc()}")
                raise
    
    def __call__(self, input):
        """
        Embed texts when called.
        This is called by ChromaDB when querying or upserting.
        Triggers lazy loading on first call.
        """
        if not self._loaded:
            logger.info(f"[LAZY_LOAD] First embedding call detected, loading model now...")
            self._ensure_loaded()
        
        try:
            return self._embedding_fn(input)
        except Exception as e:
            logger.error(f"[LAZY_LOAD] ❌ Error during embedding: {e}")
            raise