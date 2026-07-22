"""
Step 3: Embedding and Similarity
Demonstrates sentence-transformers all-MiniLM-L6-v2 and cosine similarity.
Model produces 384-dimensional vectors; proximity = semantic similarity.
"""

from chromadb.utils import embedding_functions
import numpy as np


def compute_similarity(vector1: list, vector2: list) -> float:
    """Compute cosine similarity between two vectors."""
    a = np.array(vector1)
    b = np.array(vector2)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


if __name__ == "__main__":
    # Initialize embedding function
    ef = embedding_functions.DefaultEmbeddingFunction()
    
    # Demo: Embed related and unrelated text
    chunk1 = "Casual leave application requires 2 days advance notice."
    chunk2 = "Sick leave can be taken without advance notice but must be reported within 24 hours."
    chunk3 = "IT password policy requires passwords to be at least 12 characters with mixed case."
    
    print("✓ Embedding with all-MiniLM-L6-v2\n")
    
    # Embed all chunks
    embeddings = ef([chunk1, chunk2, chunk3])
    
    print(f"Embedding 1: {embeddings[0][:5]}... (dimension: {len(embeddings[0])})")
    print(f"Embedding 2: {embeddings[1][:5]}...")
    print(f"Embedding 3: {embeddings[2][:5]}...\n")
    
    # Compute similarities
    sim_1_2 = compute_similarity(embeddings[0], embeddings[1])
    sim_1_3 = compute_similarity(embeddings[0], embeddings[2])
    sim_2_3 = compute_similarity(embeddings[1], embeddings[2])
    
    print(f"Similarity (Leave chunks): {sim_1_2:.4f} (high, both about leave)")
    print(f"Similarity (Leave vs IT policy): {sim_1_3:.4f} (low, different topics)")
    print(f"Similarity (Leave vs IT policy): {sim_2_3:.4f} (low, different topics)\n")
    
    print("✓ Key insight: Semantically similar chunks have higher cosine similarity!")
