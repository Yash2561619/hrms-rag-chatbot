"""
Step 5: Full Query Pipeline (Updated to google.genai)
Demonstrates end-to-end RAG: retrieve chunks → construct prompt → call Claude.
Token cost: 500-800 tokens per query vs 8000 for full document (90-95% reduction).
"""

import chromadb
from chromadb.utils import embedding_functions
import google.genai as genai  # NEW SDK
import os
from dotenv import load_dotenv


def query_rag(collection, query: str, num_results: int = 3) -> str:
    """
    Execute RAG query pipeline.
    
    Args:
        collection: ChromaDB collection with indexed chunks
        query: User question
        num_results: Number of chunks to retrieve
    
    Returns:
        Answer from Gemini
    """
    # Load environment variables
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in .env file")
    
    # Create client with new SDK
    client = genai.Client(api_key=api_key)
    
    # Step 1: Retrieve relevant chunks
    results = collection.query(query_texts=[query], n_results=num_results)
    context = "\n\n".join(results["documents"][0])
    
    # Step 2: Construct prompt with context
    system = """You are an HR assistant for Next AI Technologies.
Answer employee questions using ONLY the provided policy context.
If the answer is not present in the context, say so explicitly.
Do not infer or extrapolate beyond what the context states."""

    user_message = f"Context:\n{context}\n\nQuestion: {query}"

    # Step 3: Exact token counting (Optional but recommended)
    token_count = client.models.count_tokens(model="gemini-2.5-flash", contents=user_message)
    print(f"[Info] Accurate input tokens: {token_count.total_tokens}")
    print(f"[Info] Retrieved {len(results['documents'][0])} chunks\n")

    # Step 4: Call Gemini API using a config dict
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_message,
        config={
            "system_instruction": system,
            "max_output_tokens": 200,
            "temperature": 0.7,
        }
    )
    
    return response.text


if __name__ == "__main__":
    # Create sample index
    from step4_store import create_index
    
    chunks = [
        "Casual leave: 12 days per year. Must apply 2 days in advance.",
        "Sick leave: 10 days per year. Can be taken without notice but report within 24 hours.",
        "Annual leave: 20 days per year. Plan in advance with manager approval.",
        "Salary is processed on the 25th of every month.",
        "Salary slips are available in the employee portal after salary processing.",
    ]
    
    print("✓ Building index...\n")
    collection = create_index(chunks)
    
    # Test queries
    test_queries = [
        "How many casual leave days do I get per year?",
        "When is salary processed?",
    ]
    
    for query in test_queries:
        print(f"Q: {query}")
        
        try:
            answer = query_rag(collection, query)
            print(f"A: {answer}\n")
        except Exception as e:
            print(f"Error: {e}")
            print("(Make sure GEMINI_API_KEY is set in .env)\n")