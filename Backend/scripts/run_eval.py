"""Runs the offline RAG evaluation harness against the checked-in gold dataset
(eval_data/gold_eval_set.json + eval_data/sample_corpus.pdf).

Retrieval evaluation (Hit-Rate@k, MRR, top relevance) needs no network access
or API key. Generation evaluation (LLM-as-judge faithfulness/answer relevancy)
calls OpenRouter twice per case and needs OPENROUTER_API_KEY configured, so
it's opt-in.

Usage:
    python scripts/run_eval.py
    python scripts/run_eval.py --with-generation
    python scripts/run_eval.py --with-generation --out evaluation-results.json
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from charbot import RAGChatbot, delete_vector_index  # noqa: E402
from evaluation import evaluate_generation, evaluate_retrieval  # noqa: E402

EVAL_DATA_DIR = BACKEND_DIR / "eval_data"
SCRATCH_CHAT_ID = "eval_sample_corpus"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold-set", type=Path, default=EVAL_DATA_DIR / "gold_eval_set.json"
    )
    parser.add_argument(
        "--with-generation",
        action="store_true",
        help="Also run LLM-as-judge generation-quality scoring (needs OPENROUTER_API_KEY).",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional path to write a JSON report.")
    args = parser.parse_args()

    gold_set = json.loads(args.gold_set.read_text(encoding="utf-8"))
    pdf_path = EVAL_DATA_DIR / gold_set["corpus"]
    k = gold_set.get("k", 4)
    cases = gold_set["cases"]

    print(f"Building RAG pipeline for {pdf_path.name} ({len(cases)} eval cases)...")
    chatbot = RAGChatbot([str(pdf_path)], SCRATCH_CHAT_ID, [pdf_path.name])

    report: dict = {}
    try:
        retrieval_metrics = evaluate_retrieval(chatbot, cases, k=k)
        report["retrieval"] = retrieval_metrics
        print(
            f"\nRetrieval: hit-rate={retrieval_metrics['retrievalHitRate']:.2%} "
            f"MRR={retrieval_metrics['meanReciprocalRank']:.3f} "
            f"avg-top-relevance={retrieval_metrics['averageTopRelevance']:.3f} "
            f"({retrieval_metrics['caseCount']} cases)"
        )

        if args.with_generation:
            print("\nRunning generation-quality evaluation (calls the configured LLM)...")
            generation_metrics = evaluate_generation(chatbot, cases, k=k)
            report["generation"] = generation_metrics
            print(
                f"Generation: avg-faithfulness={generation_metrics['averageFaithfulness']:.2f} "
                f"avg-answer-relevancy={generation_metrics['averageAnswerRelevancy']:.2f}"
            )
    finally:
        delete_vector_index(SCRATCH_CHAT_ID)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote report to {args.out}")


if __name__ == "__main__":
    main()
