import json
import logging
import os
import sys

# Ensure project root is accessible in Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google import genai
from app.services.rag_service import (
    hybrid_retrieve,
    math_rrf_rerank,
    multi_query_expansion,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RAG_EVAL")

# Initialize Gemini Client
api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
gemini_client = genai.Client(api_key=api_key)


def load_benchmark_dataset(file_path="eval/benchmark_dataset.json"):
  """Loads the 20-item benchmark test dataset."""
  with open(file_path, "r", encoding="utf-8") as f:
    return json.load(f)


def evaluate_retrieval(top_docs, expected_document, expected_section=None):
  """Tier 1: Evaluates Search Retrieval (Hit Rate & MRR)."""
  hit = 0
  mrr = 0.0

  for rank, doc in enumerate(top_docs, start=1):
    source = doc.metadata.get("source", "")
    content = doc.page_content.lower()

    # Check if the document source matches
    if expected_document.lower() in source.lower():
      # Check section match in page content if section specified
      if expected_section is None or expected_section.lower() in content:
        hit = 1
        mrr = 1.0 / rank
        break

  return hit, mrr


def evaluate_generation_with_llm_judge(
    question, retrieved_context, generated_answer, ground_truth
):
  """Tier 2: Evaluates LLM Generation Quality (Faithfulness & Answer Relevancy)."""
  judge_prompt = f"""
You are an impartial AI evaluator scoring a RAG system's response on a scale from 0.0 to 1.0.

Question: {question}
Retrieved Context: {retrieved_context}
Generated Answer: {generated_answer}
Ground Truth Reference: {ground_truth}

Return ONLY a JSON object matching this schema:
{{
  "faithfulness_score": <float 0.0 to 1.0: Is the answer 100% grounded in the context without extra hallucinations?>,
  "relevancy_score": <float 0.0 to 1.0: Does the answer directly and accurately address the question?>,
  "reasoning": "<short sentence explaining scores>"
}}
"""
  try:
    res = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=judge_prompt,
        config={
            "temperature": 0.0,
            "response_mime_type": "application/json",
        },
    )
    return json.loads(res.text)
  except Exception as e:
    logger.error(f"EVAL_LLM_JUDGE_ERROR | {e}")
    return {"faithfulness_score": 0.0, "relevancy_score": 0.0, "reasoning": str(e)}


def run_full_evaluation():
  dataset = load_benchmark_dataset()
  print(
      f"\n🚀 STARTING TECHNOVA RAG EVALUATION MATRIX ({len(dataset)} Test"
      " Cases)...\n"
  )

  total_hits = 0
  total_mrr = 0.0
  total_faithfulness = 0.0
  total_relevancy = 0.0

  for item in dataset:
    q_id = item["id"]
    q = item["question"]
    ground_truth = item["ground_truth"]
    expected_doc = item["source_document"]
    expected_sec = item.get("expected_section")

    print(f"------------ Test Case #{q_id} ------------")
    print(f"❓ Question: {q}")

    # 1. Step 1: Query Expansion & Retrieval
    expanded_queries = multi_query_expansion(q, gemini_client)
    dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(
        expanded_queries, top_k=6
    )

    # 2. Step 2: Math RRF Re-ranking
    context, citation_footer, top_docs = math_rrf_rerank(
        dense_docs, sparse_docs, top_k=2
    )

    # 3. Evaluate Tier 1: Search Retrieval Performance
    hit, mrr = evaluate_retrieval(top_docs, expected_doc, expected_sec)
    total_hits += hit
    total_mrr += mrr
    print(f"🎯 Retrieval -> Hit: {hit} | MRR: {mrr:.2f}")

    # 4. Generate Answer via Gemini
    prompt = f"Answer concisely using ONLY context:\nContext:\n{context}\n\nQuestion:\n{q}"
    try:
      resp = gemini_client.models.generate_content(
          model="gemini-2.5-flash",
          contents=prompt,
          config={"temperature": 0.2, "max_output_tokens": 200},
      )
      generated_answer = resp.text.strip() if resp and resp.text else ""
    except Exception as e:
      generated_answer = ""

    # 5. Evaluate Tier 2: LLM Quality Scores
    scores = evaluate_generation_with_llm_judge(
        q, context, generated_answer, ground_truth
    )
    total_faithfulness += scores["faithfulness_score"]
    total_relevancy += scores["relevancy_score"]

    print(f"🤖 Answer: {generated_answer}")
    print(
        f"📊 Scores  -> Faithfulness: {scores['faithfulness_score']} |"
        f" Relevancy: {scores['relevancy_score']}"
    )
    print(f"💡 Notes   -> {scores['reasoning']}\n")

  # Print Final Evaluation Table
  num_items = len(dataset)
  print("==================================================")
  print("         📈 TECHNOVA EVALUATION MATRIX RESULTS     ")
  print("==================================================")
  print(f"  • Total Test Cases:         {num_items}")
  print(f"  • Hit Rate @ 2:             {total_hits / num_items:.2%}")
  print(f"  • Mean Reciprocal Rank:     {total_mrr / num_items:.2f}")
  print(f"  • Average Faithfulness:     {total_faithfulness / num_items:.2f} / 1.0")
  print(f"  • Average Answer Relevancy: {total_relevancy / num_items:.2f} / 1.0")
  print("==================================================\n")


if __name__ == "__main__":
  run_full_evaluation()