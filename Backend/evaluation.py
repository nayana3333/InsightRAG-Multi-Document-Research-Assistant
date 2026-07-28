import json
import re
from statistics import mean

from langchain_core.messages import HumanMessage


def evaluate_retrieval(chatbot, cases: list[dict], k: int = 4) -> dict:
    """Evaluate retrieval without spending LLM tokens."""
    results = []
    for case in cases:
        documents = chatbot.retrieve(case["question"], k=k)
        retrieved_pages = [int(document.metadata.get("page", 0)) + 1 for document in documents]
        relevant_pages = set(case["relevantPages"])
        relevant_files = set(case.get("relevantFiles", []))
        ranks = [
            rank
            for rank, document in enumerate(documents, start=1)
            if int(document.metadata.get("page", 0)) + 1 in relevant_pages
            and (
                not relevant_files
                or document.metadata.get("fileName", "") in relevant_files
            )
        ]
        reciprocal_rank = 1 / min(ranks) if ranks else 0
        top_relevance = documents[0].metadata.get("relevance", 0) if documents else 0
        results.append(
            {
                "question": case["question"],
                "relevantPages": sorted(relevant_pages),
                "relevantFiles": sorted(relevant_files),
                "retrievedPages": retrieved_pages,
                "retrievedFiles": [
                    document.metadata.get("fileName", "Document") for document in documents
                ],
                "hit": bool(ranks),
                "reciprocalRank": round(reciprocal_rank, 4),
                "topRelevance": round(float(top_relevance), 4),
            }
        )

    return {
        "caseCount": len(results),
        "k": k,
        "retrievalHitRate": round(mean(item["hit"] for item in results), 4),
        "meanReciprocalRank": round(mean(item["reciprocalRank"] for item in results), 4),
        "averageTopRelevance": round(mean(item["topRelevance"] for item in results), 4),
        "cases": results,
    }


_JUDGE_SYSTEM_PROMPT = (
    "You are a strict evaluator of retrieval-augmented answers. Score the answer "
    "against the supplied evidence only, never your own knowledge. Respond with a "
    "single JSON object and nothing else, in exactly this form: "
    '{"faithfulness": <0-1>, "answerRelevancy": <0-1>, "reasoning": "<one sentence>"}. '
    "faithfulness: the fraction of the answer's claims that are directly supported by "
    "the evidence (1 = fully grounded, 0 = fabricated or unsupported). "
    "answerRelevancy: how directly the answer addresses the question asked "
    "(1 = fully relevant, 0 = off-topic or non-answer)."
)


def _judge_messages(question: str, context_text: str, answer: str, expected_answer: str | None) -> list[dict]:
    reference_clause = (
        f"\n\nA reference answer for comparison (do not require an exact match): {expected_answer}"
        if expected_answer
        else ""
    )
    return [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\nEvidence:\n{context_text}\n\n"
                f"Answer to evaluate: {answer}{reference_clause}"
            ),
        },
    ]


def _parse_judge_response(raw_text: str) -> dict:
    """Parses the judge's JSON reply, tolerating extra prose around the JSON object.

    A single malformed judge reply must not abort a whole evaluation run, so
    failures degrade to a zero-scored, flagged result instead of raising.
    """
    candidates = [raw_text]
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if "faithfulness" not in parsed or "answerRelevancy" not in parsed:
            continue
        try:
            faithfulness = max(0.0, min(1.0, float(parsed["faithfulness"])))
            answer_relevancy = max(0.0, min(1.0, float(parsed["answerRelevancy"])))
        except (TypeError, ValueError):
            continue
        return {
            "faithfulness": faithfulness,
            "answerRelevancy": answer_relevancy,
            "reasoning": str(parsed.get("reasoning", ""))[:500],
            "parseError": False,
        }

    return {
        "faithfulness": 0.0,
        "answerRelevancy": 0.0,
        "reasoning": "Judge response could not be parsed as the expected JSON object.",
        "parseError": True,
        "rawResponse": raw_text[:500],
    }


def judge_answer(
    chatbot,
    question: str,
    answer: str,
    context: list,
    expected_answer: str | None = None,
) -> dict:
    """Scores a generated answer for faithfulness and relevancy via LLM-as-judge."""
    context_text = "\n\n".join(
        f"[Source {index}] {document.page_content}" for index, document in enumerate(context, start=1)
    ) or "No evidence was retrieved."
    messages = _judge_messages(question, context_text, answer, expected_answer)
    try:
        raw_text = chatbot._complete(messages)
    except Exception as error:
        return {
            "faithfulness": 0.0,
            "answerRelevancy": 0.0,
            "reasoning": f"Judge call failed: {error}",
            "parseError": True,
        }
    return _parse_judge_response(raw_text)


def evaluate_generation(chatbot, cases: list[dict], k: int = 4) -> dict:
    """LLM-as-judge generation-quality evaluation (faithfulness + answer relevancy).

    Spends LLM tokens: one completion to generate an answer and one to judge it,
    per case. Each case is answered independently (no shared conversation state)
    so results aren't contaminated by earlier cases in the same run.
    """
    results = []
    for case in cases:
        question = case["question"]
        context = chatbot.retrieve(question, k=k)
        request_messages = chatbot._request_messages(context, [HumanMessage(content=question)])
        try:
            answer = chatbot._complete(request_messages)
        except Exception as error:
            results.append(
                {
                    "question": question,
                    "answer": None,
                    "faithfulness": 0.0,
                    "answerRelevancy": 0.0,
                    "reasoning": f"Answer generation failed: {error}",
                    "parseError": True,
                }
            )
            continue

        judged = judge_answer(chatbot, question, answer, context, case.get("expectedAnswer"))
        results.append(
            {
                "question": question,
                "answer": answer,
                "faithfulness": judged["faithfulness"],
                "answerRelevancy": judged["answerRelevancy"],
                "reasoning": judged["reasoning"],
                "parseError": judged["parseError"],
            }
        )

    return {
        "caseCount": len(results),
        "averageFaithfulness": round(mean(item["faithfulness"] for item in results), 4),
        "averageAnswerRelevancy": round(mean(item["answerRelevancy"] for item in results), 4),
        "cases": results,
    }
