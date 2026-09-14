"""T006: Retrieval-accuracy evaluation harness for the Advanced Legal Knowledge
Assistant benchmark (specs/002-legal-knowledge-assistant/tasks.md).

Candidate-agnostic by design: it does NOT itself embed/rank anything (that is
each candidate's own, separately-run code, per research.md's evaluation
methodology). It scores a `predictions.json` file (produced by whichever
candidate technique - pure embedding, hybrid, query-rewriting, reranked) against
benchmark_dataset.json, computing exactly the metrics spec.md's Success Criteria
name: top-1 accuracy (SC-001), top-3 recall (SC-002), wrong-rule rate (SC-003),
wrong-field rate (SC-004), and the adversarial A/B/C classification (SC-009).

predictions.json shape (one entry per benchmark query id):
{
  "model": "<candidate name>",
  "avg_latency_ms": <float>,
  "results": [
    {"id": "<query id>", "ranked_top5": [{"provision_id": ..., "rule_id": ..., "field": ..., "score": ...}, ...]}
  ]
}

Usage: python score_retrieval.py <predictions.json>
Reusable, unmodified, across every retrieval candidate (T008/T009/T010/T012).
"""
import json
import sys
from pathlib import Path

DATA_PATH = Path(__file__).parent / "benchmark_dataset.json"
THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def score(predictions_path: Path) -> dict:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    queries = {q["id"]: q for q in data["queries"]}
    preds = json.loads(predictions_path.read_text(encoding="utf-8"))

    per_query = []
    for r in preds["results"]:
        q = queries[r["id"]]
        expected_ids = {e["provision_id"] for e in q["expected"]}
        expected_rules = {e["rule_id"] for e in q["expected"]}
        top5 = r["ranked_top5"]
        top1 = top5[0] if top5 else {"provision_id": None, "rule_id": None, "score": 0.0}
        top3_ids = {t["provision_id"] for t in top5[:3]}

        if expected_ids:
            top1_correct = top1["provision_id"] in expected_ids
            top3_hit = bool(expected_ids & top3_ids)
            top1_wrong_rule = top1["rule_id"] not in expected_rules
            top1_wrong_field = (not top1_wrong_rule) and (top1["provision_id"] not in expected_ids)
            # SC-009 class: A=correct, B=plausible-but-wrong (confident top1 that is wrong),
            # C=correct refusal (not applicable here - refusal only scored on no-result queries).
            sc009_class = "A" if top1_correct else "B"
        else:
            top1_correct = None
            top3_hit = None
            top1_wrong_rule = False
            top1_wrong_field = False
            # For a no-result query, a "confident" top1 above a plausibility bar is
            # itself the SC-009 class-B failure mode; class C = the system correctly
            # abstains. Scored per-threshold below, not as a single fixed class here.
            sc009_class = None

        per_query.append({
            "id": r["id"], "style": q["style"], "query_text": q["query_text"],
            "expected_ids": sorted(expected_ids), "top1_id": top1["provision_id"],
            "top1_score": top1.get("score"), "top1_correct": top1_correct, "top3_hit": top3_hit,
            "top1_wrong_rule": top1_wrong_rule, "top1_wrong_field": top1_wrong_field,
            "sc009_class": sc009_class,
        })

    applicable = [pq for pq in per_query if pq["expected_ids"]]
    no_result = [pq for pq in per_query if not pq["expected_ids"]]
    n = len(applicable)

    headline = {
        "model": preds.get("model"),
        "avg_latency_ms": preds.get("avg_latency_ms"),
        "applicable_queries": n,
        "no_result_queries": len(no_result),
        "top1_accuracy": round(sum(1 for pq in applicable if pq["top1_correct"]) / n, 4) if n else None,
        "top3_recall": round(sum(1 for pq in applicable if pq["top3_hit"]) / n, 4) if n else None,
        "wrong_rule_rate": round(sum(1 for pq in applicable if pq["top1_wrong_rule"]) / n, 4) if n else None,
        "wrong_field_rate": round(sum(1 for pq in applicable if pq["top1_wrong_field"]) / n, 4) if n else None,
    }

    # Adversarial-subset-specific SC-009 class-B rate (the stricter bar).
    adversarial = [pq for pq in applicable if pq["style"].startswith("adversarial")]
    if adversarial:
        headline["adversarial_class_b_rate"] = round(sum(1 for pq in adversarial if pq["sc009_class"] == "B") / len(adversarial), 4)

    # Threshold sweep for no-result (refusal) behavior - same discipline
    # 001-legal-rag's Phase 8 benchmark used: there is no single "confidence"
    # signal until a candidate is actually run, so this sweeps a plausible
    # cosine-similarity-shaped score range and reports both directions of error.
    by_threshold = {}
    for thr in THRESHOLDS:
        correctly_abstained = sum(1 for pq in no_result if (pq["top1_score"] or 0) < thr)
        correct_and_kept = sum(1 for pq in applicable if pq["top1_correct"] and (pq["top1_score"] or 0) >= thr)
        correct_but_withheld = sum(1 for pq in applicable if pq["top1_correct"] and (pq["top1_score"] or 0) < thr)
        by_threshold[str(thr)] = {
            "no_result_correctly_abstained": correctly_abstained, "no_result_total": len(no_result),
            "correct_top1_retained_above_threshold": correct_and_kept,
            "correct_top1_wrongly_withheld_below_threshold": correct_but_withheld,
        }

    by_style = {}
    for pq in applicable:
        by_style.setdefault(pq["style"], []).append(pq["top1_correct"])
    style_summary = {style: f"{sum(vals)}/{len(vals)}" for style, vals in by_style.items()}

    return {
        "headline": headline, "by_style": style_summary, "by_threshold": by_threshold,
        "per_query": per_query,
        "wrong_cases": [pq for pq in applicable if pq["top1_wrong_rule"] or pq["top1_wrong_field"]],
    }


if __name__ == "__main__":
    predictions_path = Path(sys.argv[1])
    result = score(predictions_path)
    out_path = predictions_path.parent / f"scored_{predictions_path.stem}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["headline"], indent=2))
    print("\nBy style:", json.dumps(result["by_style"], indent=2))
    print(f"\nWrote {out_path}")
