"""
Test script to verify ChromaDB and LazyEmbeddingFunction work correctly.
Run this locally to debug before deploying.

Usage:
    python test_chroma_init.py
"""

import os
import sys
import logging

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Disable telemetry
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

def test_lazy_embedding():
    """Test 1: Can we import LazyEmbeddingFunction?"""
    print("\n" + "="*60)
    print("TEST 1: Import LazyEmbeddingFunction")
    print("="*60)
    
    try:
        from app.services.lazy_embedding import LazyEmbeddingFunction
        print("✅ LazyEmbeddingFunction imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Failed to import: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_lazy_embedding_instantiation():
    """Test 2: Can we create a LazyEmbeddingFunction instance?"""
    print("\n" + "="*60)
    print("TEST 2: Create LazyEmbeddingFunction instance")
    print("="*60)
    
    try:
        from app.services.lazy_embedding import LazyEmbeddingFunction
        ef = LazyEmbeddingFunction(model_name="BAAI/bge-small-en-v1.5")
        print("✅ LazyEmbeddingFunction instance created")
        print(f"   Model: {ef.model_name}")
        print(f"   Loaded: {ef._loaded} (should be False - lazy!)")
        return True
    except Exception as e:
        print(f"❌ Failed to create instance: {e}")
        import traceback
        print(traceback.format_exc())
        return False

def test_chromadb_client():
    """Test 3: Can we create a ChromaDB client?"""
    print("\n" + "="*60)
    print("TEST 3: Create ChromaDB persistent client")
    print("="*60)
    
    try:
        import chromadb
        client = chromadb.PersistentClient(path="chroma_db")
        print("✅ ChromaDB client created successfully")
        return True
    except Exception as e:
        print(f"❌ Failed to create ChromaDB client: {e}")
        import traceback
        print(traceback.format_exc())
        return False

def test_collection_creation():
    """Test 4: Can we create a collection with lazy embedding?"""
    print("\n" + "="*60)
    print("TEST 4: Create collection with LazyEmbeddingFunction")
    print("="*60)
    
    try:
        import chromadb
        from app.services.lazy_embedding import LazyEmbeddingFunction
        
        ef = LazyEmbeddingFunction(model_name="BAAI/bge-small-en-v1.5")
        client = chromadb.PersistentClient(path="chroma_db_test")
        
        print("Creating collection...")
        collection = client.get_or_create_collection(
            name="test_collection",
            embedding_function=ef
        )
        print("✅ Collection created successfully")
        print(f"   Collection name: {collection.name}")
        print(f"   Embedding function loaded yet: {ef._loaded} (should still be False!)")
        return True
    except Exception as e:
        print(f"❌ Failed to create collection: {e}")
        import traceback
        print(traceback.format_exc())
        return False

def test_embedding_on_first_use():
    """Test 5: Does embedding model load on first query?"""
    print("\n" + "="*60)
    print("TEST 5: Verify model loads on first query (NOT at init)")
    print("="*60)
    
    try:
        import chromadb
        from app.services.lazy_embedding import LazyEmbeddingFunction
        
        ef = LazyEmbeddingFunction(model_name="BAAI/bge-small-en-v1.5")
        client = chromadb.PersistentClient(path="chroma_db_test2")
        collection = client.get_or_create_collection(
            name="test_collection2",
            embedding_function=ef
        )
        
        print(f"Before upsert - Model loaded: {ef._loaded} (should be False)")
        
        # Add a document - this should trigger model loading
        print("Adding document to collection (this will trigger model loading)...")
        print("⏳ This may take 10-15 seconds on first run...")
        
        collection.upsert(
            documents=["This is a test document about HR policies."],
            ids=["test_1"],
            metadatas=[{"source": "test.pdf"}]
        )
        
        print(f"After upsert - Model loaded: {ef._loaded} (should be True)")
        
        if ef._loaded:
            print("✅ Model loaded successfully on first use!")
            return True
        else:
            print("❌ Model did not load as expected")
            return False
            
    except Exception as e:
        print(f"❌ Failed during embedding test: {e}")
        import traceback
        print(traceback.format_exc())
        return False

def main():
    print("\n" + "="*60)
    print("ChromaDB & LazyEmbeddingFunction Tests")
    print("="*60)
    
    results = []
    
    # Run tests
    results.append(("Import LazyEmbeddingFunction", test_lazy_embedding()))
    results.append(("Create LazyEmbeddingFunction", test_lazy_embedding_instantiation()))
    results.append(("Create ChromaDB client", test_chromadb_client()))
    results.append(("Create collection", test_collection_creation()))
    results.append(("Lazy load on first use", test_embedding_on_first_use()))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} | {test_name}")
    
    all_passed = all(result[1] for result in results)
    
    print("="*60)
    if all_passed:
        print("✅ All tests passed! Your setup is correct.")
        print("\nYou can now deploy to Render.")
    else:
        print("❌ Some tests failed. Fix the errors above before deploying.")
    print("="*60)
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)