# T020 Evidence Package — Human/Legal Approval Gate

**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md) | **Tasks**: [../tasks.md](../tasks.md)

**Prepared**: this controlled execution run (Phases 1–2 fully executed; Phase 3
executed and DECLINED; Phase 4 marked N/A; Phase 5 marked N/A; Phase 6 executed
regardless, per explicit instruction, to keep this package complete).

**This document does not approve or reject anything.** Per tasks.md T020, only a
human reviewer with project and legal-subject-matter authority may do that. This
is the evidence for that person to review.

---

## 1. Retrieval benchmark results (Phase 3, T008–T011)

Full detail: [phase3-retrieval-results.md](phase3-retrieval-results.md).

| Candidate | Top-1 (need ≥90%) | Wrong-rule (need <5%) | Adversarial class-B (need <5%) | Verdict |
|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` (pure embedding) | 74.36% | 12.82% | 23.08% | REJECTED |
| `BAAI/bge-m3` (pure embedding) | 76.92% | 12.82% | 30.77% | REJECTED |
| Hybrid BM25 + `bge-small` (RRF) | 84.62% | 7.69% | 15.38% | REJECTED |
| Query-rewrite + hybrid | 87.18% | 7.69% | 15.38% | REJECTED |

**No candidate cleared all three required bars.** Wrong-rule and adversarial
class-B rates plateaued after T009 and did not improve with T010's query
rewriting — the remaining failure mode looks structural (corpus vocabulary
overlap between `LMPC-R6` and `LMPC-R24`), not something the techniques tried
here can fix.

## 2. Selected/declined retrieval approach

**Declined.** No retrieval technique is recommended for adoption at this
corpus's current scale (11 provisions, 2 rule_ids). This directly parallels
`001-legal-rag`'s own Phase 8 conclusion, now confirmed independently on a
larger (45- vs 36-query), purpose-built benchmark for this feature's own
free-form-query use case — the earlier finding was not a fluke of that
benchmark's specific query set.

## 3. Reranking result (Phase 4, T012)

**N/A — not executed.** Full reason: [phase4-reranking-results.md](phase4-reranking-results.md).
No candidate from Phase 3 qualified as a base to rerank, and the observed failure
mode (flat wrong-rule/adversarial rates across techniques) is not the top-1-vs-
top-3 symptom reranking addresses.

## 4. Generation result (Phase 5, T013–T016)

**N/A — not executed.** Full reason: [phase5-generation-results.md](phase5-generation-results.md).
No accepted retrieval pipeline exists to generate answers from. The groundedness
harness (T007, `score_groundedness.py`) was built and is ready for a future run.
No Gemini call, no Hugging Face Inference API call, no generation model of any
kind was invoked in this run.

## 5. Citation/provenance results

Not separately measured this run (SC-005/SC-006 require an `answered` result to
check a citation against; Phase 3's candidates were scored for *retrieval*
correctness, which is what SC-005/SC-006 build on). Every citation any accepted
future pipeline would return continues to come from `001-legal-rag`'s existing,
unmodified `LegalProvision`/`LegalSourceDocument` shapes (data-model.md) — the
provenance data itself was not touched or degraded by anything in this run.

## 6. Refusal results

The benchmark's 6 no-result queries (genuinely uncovered topics — expiry dates,
verification intervals, the Companies Act, standard package sizes, the Consumer
Protection Act, import duty) are included in every Phase 3 candidate's scoring;
`score_retrieval.py`'s threshold-sweep output (in each `scored_predictions_*.json`)
shows no single similarity threshold cleanly separates these from genuine matches
— the same warning `research.md` §5 already carried from `001-legal-rag`'s Phase 8,
now reconfirmed on this feature's own larger benchmark.

## 7. Prompt-injection review (Phase 6, T017–T018)

Full detail: [phase6-security-design-review.md](phase6-security-design-review.md),
raw results: [t017-injection-test-results.json](t017-injection-test-results.json).

Five adversarial probes (system-prompt extraction, role override, "disregard the
retrieved text" instructions, an `officialDecision`-exfiltration attempt, a
code-execution payload) run against the actual Phase 3 candidate code. **Finding**:
the retrieval/ranking stage is structurally immune — dense embeddings and BM25
are pure numeric functions with no code path that parses text as an instruction.
The real injection risk surface is entirely in a not-yet-built generation step
(Phase 5, not reached); T018 records the exact boundary design that step would be
required to implement if this feature is ever revisited.

## 8. Compliance-boundary review (Phase 6, T019)

Full detail: [phase6-security-design-review.md](phase6-security-design-review.md).
Confirmed against both the design (`contracts/legal-assistant-api.md`,
`data-model.md`) and the actual code created this run
(`backend/models/legal_assistant.py`, `backend/services/legal_assistant.py`):
**zero** declared or actual access path to `complianceResult`, `overall_status`,
`compliance_score`, `officialDecision`, or `decisionHistory` anywhere in this
feature. Both new files are unreachable (not imported by `backend/main.py` or
anything else) and contain zero business logic.

## 9. Resource/latency results

| Candidate | Avg query latency | Model load / setup |
|---|---|---|
| `bge-small-en-v1.5` | 23.0 ms | 6.5 s (already-cached weights) |
| `bge-m3` | 152.4 ms | 10.3 s (already-cached weights) |
| Hybrid (BM25 + bge-small, RRF) | 23.3 ms | ~7 s |
| Query-rewrite + hybrid | 25.9 ms | ~7 s |
| `001-legal-rag` deterministic lookup (reference point) | ~0.013 ms | none |

Every candidate tested remains well within an interactive-latency budget (spec
SC-011's soft bar) — **latency was never the limiting factor for any candidate;
accuracy/safety was**, consistent with constitution Principle VI (Accuracy Over
Performance).

## 10. Recommendation to proceed or decline

**Recommendation: DECLINE this feature for production implementation at this
time.** No retrieval technique evaluated — pure embedding (two models), hybrid
keyword+semantic, or query-rewriting on top of the hybrid — clears the accuracy
and safety bars spec.md itself sets (SC-001 ≥90% top-1, SC-003 <5% wrong-rule,
SC-009 <5% plausible-but-wrong). Proceeding to Phase 7 (a real, Official-facing
API and UI) on top of a retrieval mechanism with a 7.7–12.8% wrong-rule rate and
a 15–31% plausible-but-wrong rate on adversarial queries would mean shipping a
legal-citation assistant materially prone to exactly the failure mode this
feature's own spec (SC-009) and the constitution (Principle III) name as the most
serious kind of failure a legal RAG system can have.

This recommendation does not close the door permanently — `research.md` §7 and
`phase3-retrieval-results.md`'s trend note both identify concrete, evidence-based
conditions under which retrieval quality might improve (corpus growth reducing
`LMPC-R6`/`LMPC-R24` vocabulary overlap; a generation step given multiple
candidates for context-aware disambiguation, never tested this run since no
retrieval pipeline reached Phase 5). Re-running this same benchmark methodology
is the recommended path if either condition is later met — not assuming today's
numbers become obsolete on their own.

---

**No agent or automated process may treat this document as approval.** The next
action is a human decision (see final report, item 17).
