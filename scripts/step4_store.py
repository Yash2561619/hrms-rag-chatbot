"""
Step 4: Vector Index Construction
Demonstrates ChromaDB for storing embeddings and performing semantic search.
ChromaDB stores both raw text and embedding vectors.
"""

import chromadb
from chromadb.utils import embedding_functions


def create_index(chunks: list, collection_name: str = "hr_policies"):
    """
    Create a ChromaDB collection with embeddings.
    
    Args:
        chunks: List of text chunks
        collection_name: Name of the collection
    
    Returns:
        ChromaDB collection object
    """
    # Initialize embedding function
    ef = embedding_functions.DefaultEmbeddingFunction()
    
    # Create ChromaDB client
    client = chromadb.Client()
    
    # Create collection
    collection = client.create_collection(
        name=collection_name,
        embedding_function=ef
    )
    
    # Add documents
    collection.add(
        documents=chunks,
        ids=[f"chunk_{i}" for i in range(len(chunks))]
    )
    
    return collection


if __name__ == "__main__":
    # Demo chunks
    chunks = [
        "Casual leave: 12 days per year. Must apply 2 days in advance.",
        "Sick leave: 10 days per year. Can be taken without notice but report within 24 hours.",
        "Annual leave: 20 days per year. Plan in advance with manager approval.",
        "Salary is processed on the 25th of every month.",
        "Salary slips are available in the employee portal after salary processing.",
        "Health insurance covers all employees with comprehensive coverage.",
        "Insurance claims must be submitted within 30 days of the expense.",
        "IT password policy requires 12+ characters with mixed case and numbers.",
    ]
    
    print("✓ Creating ChromaDB index...\n")
    
    collection = create_index(chunks)
    
    print(f"✓ Index created successfully!")
    print(f"✓ Total chunks stored: {collection.count()}\n")
    
    # Demonstrate retrieval
    print("--- Query Test ---")
    query = "How many casual leave days do I get?"
    
    results = collection.query(query_texts=[query], n_results=3)
    
    print(f"Query: {query}\n")
    print("Top 3 results:")
    for i, (doc, distance) in enumerate(zip(results["documents"][0], results["distances"][0]), 1):
        similarity = 1 - distance  # Convert L2 distance to similarity
        print(f"{i}. (similarity: {similarity:.4f}) {doc[:80]}...")
