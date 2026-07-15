from statistics import mean


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
