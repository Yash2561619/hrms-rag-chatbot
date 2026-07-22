import google.genai as genai
import os
from dotenv import load_dotenv

# Initialize the client once at the top level or inside main
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in .env file")

client = genai.Client(api_key=api_key)


def answer_with_context(query: str, context: str) -> str:
    """Answer using ONLY the provided context (prevents hallucination)."""
    system = """You are an HR assistant for Next AI Technologies.

You MUST answer ONLY using the supplied context.

If the answer cannot be found in the context, reply exactly:

"I don't have enough information in the HR policy document to answer that question."

Do NOT use your own knowledge.
Do NOT answer from memory.
Do NOT provide background information.
Do NOT make assumptions.
Do NOT mention facts outside the supplied context.
If the question is unrelated to HR policy, politely state that it is outside the scope of the HR policy document."""

    user_message = f"Context:\n{context}\n\nQuestion: {query}"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=user_message,
        config={
            "system_instruction": system,
            "max_output_tokens": 256,
            "temperature": 0.7,
        }
    )
    return response.text


def answer_without_context(query: str) -> str:
    """Answer using LLM's parametric knowledge (prone to hallucination)."""
    system = "You are an HR assistant. Answer the employee's question."
    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config={
            "system_instruction": system,
            "max_output_tokens": 256,
            "temperature": 0.7,
        }
    )
    return response.text


if __name__ == "__main__":
    # Context: Limited HR policy
    limited_context = """
    HR Policy - Leave Section Only:
    - Casual Leave: 12 days per year
    - Sick Leave: 10 days per year
    """
    
    # Query: Something NOT in the context
    query = "What is the dress code policy?"
    
    print("=" * 60)
    print("HALLUCINATION PREVENTION DEMO")
    print("=" * 60)
    print(f"\nContext provided: Casual & Sick Leave only")
    print(f"Query: {query}")
    print(f"(Note: Dress code is NOT in the provided context)\n")
    
    print("-" * 60)
    print("ANSWER WITH CONTEXT (RAG - Prevents Hallucination):")
    print("-" * 60)
    
    try:
        answer_with = answer_with_context(query, limited_context)
        print(answer_with)
    except Exception as e:
        print(f"Error: {e}\n(Make sure GEMINI_API_KEY is set in .env)")
    
    print("\n" + "-" * 60)
    print("ANSWER WITHOUT CONTEXT (LLM Knowledge Only - May Hallucinate):")
    print("-" * 60)
    
    try:
        answer_without = answer_without_context(query)
        print(answer_without)
    except Exception as e:
        print(f"Error: {e}\n(Make sure GEMINI_API_KEY is set in .env)")
    
    print("\n" + "=" * 60)
    print("KEY INSIGHT:")
    print("The first answer (with context) refuses to answer outside the policy.")
    print("The second answer (without context) may invent a dress code policy.")
    print("This is why RAG is critical for trustworthy HR assistants!")
    print("=" * 60)