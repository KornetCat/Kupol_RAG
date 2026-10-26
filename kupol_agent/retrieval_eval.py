"""Source-labelled, local retrieval evaluation. Never sends data or equipment commands."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .retrieval import DocumentIndex, HybridRetriever, rank_texts


def _measure(hits, relevant, limit):
    seen = set()
    found = []
    for index, hit in enumerate(hits[:limit], 1):
        source = hit.get("source_id", hit.get("episode_id", hit["id"]))
        if source in relevant and source not in seen:
            found.append((source, index))
        seen.add(source)
    return {"hit_at_k": bool(found), "recall_at_k": len(found) / len(relevant) if relevant else 0,
            "reciprocal_rank": 1 / found[0][1] if found else 0,
            "found": [source for source, _ in found],
            "source_ids": [row.get("source_id", row["id"]) for row in hits[:limit]],
            "categories": dict(Counter(row.get("category", "unknown") for row in hits[:limit]))}


def evaluate(*, dataset=None, knowledge=None, limit=5):
    package = Path(__file__).resolve().parent
    labels = json.loads(Path(dataset or package / "data/retrieval_eval_queries.json").read_text(encoding="utf-8"))
    index = DocumentIndex([Path(knowledge or package / "knowledge")])
    retriever = HybridRetriever(index, None)
    # Freeze the former raw-text BM25 behavior: no synthetic episode synopsis or structured metadata score.
    legacy = [row for row in index.documents if not row.get("synopsis")]
    rows = []
    for case in labels["items"]:
        expected = set(case["relevant_source_ids"])
        variants = {
            "legacy_bm25": rank_texts(legacy, case["query"], limit),
            "expanded_without_live_context": retriever.retrieve(case["query"], "retrieval_eval", limit),
            "contextual": retriever.retrieve(case["query"], "retrieval_eval", limit,
                telemetry=case.get("telemetry"), faults=case.get("faults")),
        }
        rows.append({"id": case["id"], "split": case["split"], "query": case["query"],
                     "expected_source_ids": sorted(expected),
                     "results": {name: _measure(hits, expected, limit) for name, hits in variants.items()}})
    summary = {}
    for split in ("all", "development", "heldout"):
        selected = [row for row in rows if split == "all" or row["split"] == split]
        if not selected:
            continue
        summary[split] = {name: {"queries": len(selected),
            "hit_at_k": sum(row["results"][name]["hit_at_k"] for row in selected) / len(selected),
            "recall_at_k": sum(row["results"][name]["recall_at_k"] for row in selected) / len(selected),
            "mrr_at_k": sum(row["results"][name]["reciprocal_rank"] for row in selected) / len(selected)}
            for name in rows[0]["results"]}
    return {"schema_version": 1, "k": limit, "source_revision": labels["source_revision"],
            "label_status": labels["label_status"], "scope": labels["scope"],
            "summary": summary, "queries": rows, "external_model_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--knowledge", type=Path)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("output/retrieval_eval.json"))
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be positive")
    report = evaluate(dataset=args.dataset, knowledge=args.knowledge, limit=args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.out), "summary": report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
