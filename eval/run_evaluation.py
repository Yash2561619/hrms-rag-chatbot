"""Automated RAG Evaluation Matrix for HR Assistant.

Evaluates:
  Tier 1: Search Retrieval (Hit Rate @ 5, Mean Reciprocal Rank - MRR)
  Tier 2: LLM Generation (Faithfulness, Answer Relevancy, Groundedness)

Location: eval/run_evaluation.py
"""

import json
import logging
import os
import re
import sys

# Ensure project root is accessible in Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

from app.services.rag_service import (
    hybrid_retrieve,
    math_rrf_rerank,
    multi_query_expansion,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("RAG_EVAL")

# Initialize Gemini Client
api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
gemini_client = genai.Client(api_key=api_key) if api_key else None


def load_benchmark_dataset(file_path="eval/benchmark_data.json"):
    """Loads the benchmark test dataset safely."""
    if not os.path.isabs(file_path):
        file_path = os.path.join(PROJECT_ROOT, file_path)

    if not os.path.exists(file_path):
        logger.error(f"BENCHMARK_FILE_NOT_FOUND | path={file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_retrieval(top_docs, expected_document, expected_section=None):
    """Tier 1: Evaluates Search Retrieval (Hit Rate & MRR)."""
    hit = 0
    mrr = 0.0

    for rank, doc in enumerate(top_docs, start=1):
        source = str(doc.metadata.get("source", doc.metadata.get("file_name", ""))).lower()
        content = doc.page_content.lower()
        target_doc = str(expected_document).lower().replace(".pdf", "")

        # Match filename or document title
        if target_doc in source or target_doc in content:
            if expected_section is None or str(expected_section).lower() in content:
                hit = 1
                mrr = 1.0 / rank
                break

    return hit, mrr


def evaluate_generation_with_llm_judge(question, retrieved_context, generated_answer, ground_truth):
    """Tier 2: Evaluates LLM Generation Quality (Faithfulness & Answer Relevancy)."""
    if not gemini_client:
        return {"faithfulness_score": 0.0, "relevancy_score": 0.0, "reasoning": "Gemini client not initialized"}

    judge_prompt = f"""You are an impartial AI evaluator scoring an HR RAG system response on a scale from 0.0 to 1.0.

Question: {question}
Retrieved Context: {retrieved_context}
Generated Answer: {generated_answer}
Ground Truth Reference: {ground_truth}

Evaluation Criteria:
- faithfulness_score: (0.0 to 1.0) Is the generated answer 100% grounded in the retrieved context without hallucinations?
- relevancy_score: (0.0 to 1.0) Does the answer directly, accurately, and concisely resolve the question based on ground truth?

Output ONLY a single valid JSON object:
{{
  "faithfulness_score": <float>,
  "relevancy_score": <float>,
  "reasoning": "<1 sentence explanation>"
}}"""

    try:
        res = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=judge_prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        if res and res.text:
            cleaned_text = re.sub(r"```(?:json)?|```", "", res.text).strip()
            return json.loads(cleaned_text)
    except Exception as e:
        logger.error(f"EVAL_LLM_JUDGE_ERROR | {e}")

    return {"faithfulness_score": 0.0, "relevancy_score": 0.0, "reasoning": "Judge execution failed"}


def run_full_evaluation():
    dataset = load_benchmark_dataset()
    num_items = len(dataset)

    if num_items == 0:
        print("❌ No test cases found in eval/benchmark_data.json.")
        return

    print(f"\n🚀 STARTING RAG EVALUATION MATRIX ({num_items} Test Cases)...\n")

    total_hits = 0
    total_mrr = 0.0
    total_faithfulness = 0.0
    total_relevancy = 0.0

    for item in dataset:
        q_id = item.get("id", 1)
        q = item["question"]
        ground_truth = item.get("ground_truth", "")
        expected_doc = item.get("source_document", "")
        expected_sec = item.get("expected_section")

        print(f"------------ Test Case #{q_id} ------------")
        print(f"❓ Question: {q}")

        # 1. Query Expansion & Hybrid Retrieval (Matches Production top_k=8)
        expanded_queries = multi_query_expansion(q, gemini_client)
        dense_docs, sparse_docs, all_retrieved = hybrid_retrieve(expanded_queries, top_k=8)

        # 2. Math RRF Re-ranking (Matches Production top_k=5)
        context, citation_footer, top_docs = math_rrf_rerank(dense_docs, sparse_docs, top_k=5)

        # 3. Evaluate Tier 1: Search Retrieval Performance
        hit, mrr = evaluate_retrieval(top_docs, expected_doc, expected_sec)
        total_hits += hit
        total_mrr += mrr
        print(f"🎯 Retrieval -> Hit: {hit} | MRR: {mrr:.2f}")

        # 4. Generate Answer via Gemini (Production Card Format)
        prompt = f"""You are an AI HR Assistant. Answer the employee's question using ONLY the provided Policy Excerpts.

FORMAT RULES:
1. Provide a clean, concise card response.
2. Structure the answer EXACTLY as follows:
📋 *Policy Information*
━━━━━━━━━━━━━━━━━━━
📌 *Policy Details:*
• *Category:* Direct factual detail from policy.

3. Complete every sentence cleanly without extra summaries.

---
POLICY EXCERPTS:
{context}

---
EMPLOYEE QUESTION:
{q}

Response:"""

        try:
            resp = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=500,
                    top_p=0.85,
                ),
            )
            generated_answer = resp.text.strip() if resp and resp.text else "Information not available in policy"
        except Exception as e:
            logger.error(f"GEN_ERROR | {e}")
            generated_answer = "Information not available in policy"

        # 5. Evaluate Tier 2: LLM Quality Scores via Judge
        scores = evaluate_generation_with_llm_judge(q, context, generated_answer, ground_truth)
        faithfulness = float(scores.get("faithfulness_score", 0.0))
        relevancy = float(scores.get("relevancy_score", 0.0))

        total_faithfulness += faithfulness
        total_relevancy += relevancy

        print(f"🤖 Answer: {generated_answer.replace(chr(10), ' ')[:100]}...")
        print(f"📊 Scores  -> Faithfulness: {faithfulness:.2f} | Relevancy: {relevancy:.2f}")
        print(f"💡 Notes   -> {scores.get('reasoning', '')}\n")

    # Final Summary Matrix
    print("==================================================")
    print("         📈 RAG EVALUATION MATRIX RESULTS         ")
    print("==================================================")
    print(f"  • Total Test Cases:         {num_items}")
    print(f"  • Hit Rate @ 5:             {total_hits / num_items:.2%}")
    print(f"  • Mean Reciprocal Rank:     {total_mrr / num_items:.2f}")
    print(f"  • Average Faithfulness:     {total_faithfulness / num_items:.2f} / 1.0")
    print(f"  • Average Answer Relevancy: {total_relevancy / num_items:.2f} / 1.0")
    print("==================================================\n")


if __name__ == "__main__":
    run_full_evaluation()