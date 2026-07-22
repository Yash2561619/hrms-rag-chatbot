"""
Step 2: Chunking Strategy
Demonstrates RecursiveCharacterTextSplitter with semantic hierarchy.
Why 500 characters? Token limit of embedding model is 256 tokens (~1000 chars).
Why 50-char overlap? Policy answers span paragraph boundaries.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list:
    """
    Split text using RecursiveCharacterTextSplitter.
    
    Strategy: paragraphs → sentences → words → characters.
    This preserves semantic coherence better than naive fixed-length splits.
    
    Args:
        text: Raw extracted text
        chunk_size: 500 chars keeps embedding model context window comfortable
        chunk_overlap: 50 chars ensures multi-sentence rules are captured
    
    Returns:
        List of text chunks
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    chunks = splitter.split_text(text)
    return chunks


if __name__ == "__main__":
    # Demo: Create sample policy text
    sample_policy = """
    HR POLICY DOCUMENT
    
    Section 1: Leave Policy
    Employees are entitled to the following leave:
    - Casual Leave: 12 days per year
    - Sick Leave: 10 days per year
    - Annual Leave: 20 days per year
    
    Casual leave must be applied 2 days in advance. Sick leave can be taken 
    without advance notice but must be reported within 24 hours.
    
    Section 2: Salary and Benefits
    Salary is processed on the 25th of every month.
    Salary slips are available in the employee portal.
    
    Section 3: Health Insurance
    All employees are covered under comprehensive health insurance.
    Claims must be submitted within 30 days of incurring the expense.
    """
    
    chunks = chunk_text(sample_policy)
    
    print(f"✓ Chunking successful!")
    print(f"✓ Total chunks: {len(chunks)}\n")
    
    for i, chunk in enumerate(chunks, 1):
        print(f"--- Chunk {i} ({len(chunk)} chars) ---")
        print(chunk)
        print()
