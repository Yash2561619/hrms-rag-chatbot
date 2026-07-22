"""
Show Knowledge Base: Terminal visualization of all indexed chunks.
Useful for debugging and understanding what the RAG system knows.
"""

import pdfplumber
import chromadb
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter


def show_knowledge_base(pdf_path: str):
    """Extract, chunk, embed, and display all indexed chunks."""
    
    print("\n" + "=" * 80)
    print("KNOWLEDGE BASE VISUALIZATION")
    print("=" * 80 + "\n")
    
    # Extract
    print(f"[1/4] Extracting text from {pdf_path}...")
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            num_pages = len(pdf.pages)
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        print(f"      ✓ {num_pages} pages, {len(text)} characters\n")
    except FileNotFoundError:
        print(f"      ✗ File not found: {pdf_path}")
        return
    
    # Chunk
    print("[2/4] Chunking text...")
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(text)
    print(f"      ✓ {len(chunks)} chunks created\n")
    
    # Embed
    print("[3/4] Creating embeddings...")
    ef = embedding_functions.DefaultEmbeddingFunction()
    client = chromadb.Client()
    collection = client.create_collection("hr_policies", embedding_function=ef)
    collection.add(documents=chunks, ids=[f"chunk_{i}" for i in range(len(chunks))])
    print(f"      ✓ Indexed in ChromaDB\n")
    
    # Display
    print("[4/4] Knowledge base chunks:\n")
    print("-" * 80)
    
    for i, chunk in enumerate(chunks, 1):
        # Truncate chunk for display
        display_text = chunk[:200].replace("\n", " ")
        if len(chunk) > 200:
            display_text += "..."
        
        print(f"\n[Chunk {i}] ({len(chunk)} chars)")
        print(f"  {display_text}")
    
    print("\n" + "-" * 80)
    print(f"\nTotal chunks in knowledge base: {len(chunks)}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    import sys
    
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "hr_policy.pdf"
    show_knowledge_base(pdf_path)
