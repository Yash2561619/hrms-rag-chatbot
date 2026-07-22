"""
RAG Complete: Standalone 30-line RAG pipeline.
One function to run end-to-end: extract → chunk → embed → store → query.
"""

import pdfplumber
import chromadb
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter
import anthropic
import os


def rag_pipeline(pdf_path: str, query: str) -> str:
    """Complete RAG pipeline in 30 lines."""
    
    # Extract text
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    
    # Chunk text
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(text)
    
    # Create index
    ef = embedding_functions.DefaultEmbeddingFunction()
    client = chromadb.Client()
    collection = client.create_collection("hr_policies", embedding_function=ef)
    collection.add(documents=chunks, ids=[f"chunk_{i}" for i in range(len(chunks))])
    
    # Retrieve
    results = collection.query(query_texts=[query], n_results=3)
    context = "\n\n".join(results["documents"][0])
    
    # Generate
    claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = claude.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system="Answer using ONLY the context. Say if info is missing.",
        messages=[{"role": "user", "content": f"Context:\n{context}\n\nQ: {query}"}]
    )
    
    return response.content[0].text


if __name__ == "__main__":
    print("RAG Complete Pipeline")
    print("This is a minimal working example of the full RAG system.\n")
    
    # Example usage (requires hr_policy.pdf)
    print("Example usage:")
    print('  answer = rag_pipeline("hr_policy.pdf", "What is the leave policy?")')
    print("\nFor a working demo, provide a valid PDF file.")
