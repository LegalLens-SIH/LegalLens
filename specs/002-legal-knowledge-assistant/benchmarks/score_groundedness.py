"""T007: Groundedness evaluation harness for the Advanced Legal Knowledge
Assistant benchmark (specs/002-legal-knowledge-assistant/tasks.md), distinct
from score_retrieval.py (T006) - this scores GENERATED TEXT against its cited
source, never retrieval accuracy, and must not be conflated with it.

Method (documented honestly as a heuristic, not a claim of perfect automated
judgment - a genuinely reliable groundedness check ultimately needs human legal
review, which is exactly why T020/T046 are human/legal approval gates and this
harness's output is evidence FOR that review, not a replacement for it):

For each (answer_text, citations) pair, flags three concrete, checkable failure
signatures rather than attempting full open-ended NLI-style entailment:
  1. NUMERIC HALLUCINATION: any number in answer_text (a price, a percentage, a
     rule number written as digits) that does not appear in the concatenated
     text of the cited provisions - numbers are exactly the class of "sounds
     right" fabrication most dangerous in a legal-citation context.
  2. CLAUSE MISATTRIBUTION: any "Rule N" / "Rule N(x)" pattern mentioned in
     answer_text that is not among the cited provisions' own rule_sub_rule_clause
     values - catches a generated answer citing a DIFFERENT clause than the one
     actually attached as evidence.
  3. ZERO-CITATION CLAIM: an answer_text that is non-empty but citations is
     empty - the single invariant data-model.md's LegalAssistantAnswer
     validation rule (rule 2) makes a hard requirement, checked here as a
     belt-and-suspenders runtime signal too.

A citation-only answer (no generated answer_text) trivially passes with zero
findings - there is no generated claim to be unsupported (T015's own point).

predictions.json shape (one entry per benchmark query id that has an answer):
{
  "model": "<candidate name>",
  "avg_latency_ms": <float>,
  "results": [
    {"id": "<query id>", "answer_text": "...", "citations": [{"rule_sub_rule_clause": "...", "text": "..."}]}
  ]
}

Usage: python score_groundedness.py <predictions.json>
"""
import json
import re
import sys
from pathlib import Path

NUMBER_PATTERN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*%?\b")
CLAUSE_PATTERN = re.compile(r"Rule\s+\d+(?:\([^)]+\))*", re.IGNORECASE)


def _normalize_number(tok: str) -> str:
    return tok.replace(",", "").strip().rstrip("%").strip()


def score_one(answer_text: str, citations: list[dict]) -> dict:
    if not answer_text:
        # Citation-only / refusal - nothing generated, nothing to hallucinate.
        return {"grounded": True, "findings": [], "note": "no generated text (citation-only or refusal)"}

    cited_text = " ".join(c.get("text", "") for c in citations)
    cited_clauses = {c.get("rule_sub_rule_clause", "").strip() for c in citations}

    findings = []

    if not citations:
        findings.append("ZERO_CITATION_CLAIM: answer_text is non-empty but citations is empty")

    answer_numbers = {_normalize_number(m.group()) for m in NUMBER_PATTERN.finditer(answer_text)}
    cited_numbers = {_normalize_number(m.group()) for m in NUMBER_PATTERN.finditer(cited_text)}
    unsupported_numbers = {n for n in answer_numbers if n and n not in cited_numbers}
    if unsupported_numbers:
        findings.append(f"NUMERIC_HALLUCINATION: number(s) {sorted(unsupported_numbers)} in answer not found in any cited provision's text")

    answer_clauses = {m.group().strip() for m in CLAUSE_PATTERN.finditer(answer_text)}
    unsupported_clauses = {c for c in answer_clauses if c not in cited_clauses}
    if unsupported_clauses:
        findings.append(f"CLAUSE_MISATTRIBUTION: clause reference(s) {sorted(unsupported_clauses)} in answer not among cited provisions {sorted(cited_clauses)}")

    return {"grounded": len(findings) == 0, "findings": findings}


def score(predictions_path: Path) -> dict:
    preds = json.loads(predictions_path.read_text(encoding="utf-8"))
    per_query = []
    for r in preds["results"]:
        result = score_one(r.get("answer_text"), r.get("citations", []))
        per_query.append({"id": r["id"], **result})

    n = len(per_query)
    grounded_count = sum(1 for pq in per_query if pq["grounded"])
    headline = {
        "model": preds.get("model"),
        "avg_latency_ms": preds.get("avg_latency_ms"),
        "total_answers": n,
        "groundedness_rate": round(grounded_count / n, 4) if n else None,
        "ungrounded_count": n - grounded_count,
    }
    return {"headline": headline, "per_query": per_query, "ungrounded_cases": [pq for pq in per_query if not pq["grounded"]]}


if __name__ == "__main__":
    predictions_path = Path(sys.argv[1])
    result = score(predictions_path)
    out_path = predictions_path.parent / f"grounded_{predictions_path.stem}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["headline"], indent=2))
    if result["ungrounded_cases"]:
        print("\nUngrounded cases:")
        for c in result["ungrounded_cases"]:
            print(f"  {c['id']}: {c['findings']}")
    print(f"\nWrote {out_path}")
