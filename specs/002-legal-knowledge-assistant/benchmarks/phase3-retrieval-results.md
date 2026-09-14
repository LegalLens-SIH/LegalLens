# Phase 3 Retrieval Evaluation Results (T008–T011)

**Feature**: [../spec.md](../spec.md) | **Tasks**: [../tasks.md](../tasks.md)

Benchmark: `benchmark_dataset.json` — 45 queries (39 with an expected answer, 6
correct-refusal/no-result), built by `build_benchmark_dataset.py` from the verified
corpus (`backend/legal_corpus/legal_metrology_packaged_commodities_2011.json`, 11
provisions, unmodified). Scored by `score_retrieval.py` against spec.md's SC-001
(≥90% top-1), SC-002 (≥97% top-3), SC-003 (<5% wrong-rule), SC-004 (<5% wrong-field),
SC-009 (<5% plausible-but-wrong on the adversarial subset).

Environment: `sentence-transformers`/`torch` installed only in a disposable
scratchpad venv (reused from `001-legal-rag`'s own Phase 8 benchmark run earlier
this session — no new install, no new download). Zero change to
`backend/requirements.txt`/`backend/constraints.txt`/`backend/.venv311`.

## T008 — Pure dense-embedding retrieval `[EVIDENCE GATE]`

| Model | Top-1 | Top-3 | Wrong-rule | Wrong-field | Adversarial class-B | Avg latency |
|---|---|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | 74.36% | 92.31% | 12.82% | 12.82% | 23.08% | 23.0 ms |
| `BAAI/bge-m3` | 76.92% | 87.18% | 12.82% | 10.26% | 30.77% | 152.4 ms |

**Result: REJECTED.** Neither model clears SC-001 (≥90%), SC-003 (<5%), or SC-009
(<5%). Consistent with, and on this larger/harder benchmark modestly worse than,
`001-legal-rag`'s own Phase 8 finding on the same corpus. Full per-query detail:
`scored_predictions_t008_bge_small.json`, `scored_predictions_t008_bge_m3.json`.

## T009 — Hybrid keyword (BM25) + semantic (bge-small) retrieval, RRF-fused `[EVIDENCE GATE]`

| Candidate | Top-1 | Top-3 | Wrong-rule | Wrong-field | Adversarial class-B | Avg latency |
|---|---|---|---|---|---|---|
| BM25 + `bge-small` (Reciprocal Rank Fusion) | 84.62% | 92.31% | 7.69% | 7.69% | 15.38% | 23.3 ms |

**Result: REJECTED, but a real improvement.** Top-1 accuracy up 10 points over the
best T008 model; wrong-rule rate roughly halved (12.82% → 7.69%). Still does not
clear SC-001 (needs ≥90%, got 84.62%), SC-003 (needs <5%, got 7.69%), or SC-009
(needs <5%, got 15.38%). The corpus's own `linked_rule_id`-derived lexical context
("retail" vs "wholesale") measurably helps disambiguate the exact confusable-query
class `001-legal-rag`'s Phase 8 first identified — but not enough. Full detail:
`scored_predictions_t009_hybrid.json`.

## T010 — Query rewriting/expansion, layered on T009's hybrid retrieval `[EVIDENCE GATE]`

| Candidate | Top-1 | Top-3 | Wrong-rule | Wrong-field | Adversarial class-B | Avg latency |
|---|---|---|---|---|---|---|
| Corpus-domain synonym expansion + T009's hybrid | 87.18% | 94.87% | 7.69% | 5.13% | 15.38% | 25.9 ms |

14 of 45 queries were actually rewritten (the rest had no matching synonym
pattern). **Result: REJECTED.** Small further improvement in top-1 (84.62% →
87.18%, still short of the 90% bar) and wrong-field (7.69% → 5.13%). **Critically,
wrong-rule rate (7.69%) and the adversarial class-B rate (15.38%) are UNCHANGED
from T009** — query rewriting improved general accuracy but did not fix the
specific cross-rule confusable-query failure mode, which is exactly the safety-
critical metric (SC-009: a plausible-but-wrong legal citation) this evaluation
treats as the most serious failure class. Full detail:
`scored_predictions_t010_queryrewrite.json`.

## T011 — Consolidated decision `[DECISION POINT]`

| Candidate | Top-1 (≥90% req.) | Wrong-rule (<5% req.) | Adversarial class-B (<5% req.) | Clears all three? |
|---|---|---|---|---|
| T008 `bge-small-en-v1.5` | 74.36% ✗ | 12.82% ✗ | 23.08% ✗ | **No** |
| T008 `bge-m3` | 76.92% ✗ | 12.82% ✗ | 30.77% ✗ | **No** |
| T009 hybrid (BM25+bge-small, RRF) | 84.62% ✗ | 7.69% ✗ | 15.38% ✗ | **No** |
| T010 query-rewrite + hybrid | 87.18% ✗ | 7.69% ✗ | 15.38% ✗ | **No** |

**No candidate clears all three required bars (SC-001, SC-003, SC-009).** Per
tasks.md T011's own explicit rule, this is a valid "no viable retrieval technique
at this corpus scale" outcome — **DECLINE**, recorded formally at T046. Per this
same session's explicit instruction, Phase 4 (T012) and Phase 5 (T013–T016) are
not executed for their intended purpose (there is no accepted retrieval pipeline
to rerank or to generate answers from) — see `phase4-reranking-results.md` and
`phase5-generation-results.md` for how each was marked N/A with reasons. Phase 6
(T017–T019) was still executed per explicit instruction, to keep T020's evidence
package complete regardless of this outcome.

**Trend observed, for the record (not itself sufficient evidence to reverse this
decision)**: each successive technique closed part of the gap (top-1: 74–77% →
84.62% → 87.18%; wrong-field: 12.82% → 7.69% → 5.13%) but **wrong-rule and
adversarial class-B rates plateaued at T009 and did not move with T010's query
rewriting** — suggesting the remaining failure mode is not primarily a lexical-
ambiguity problem query rewriting can fix, but a structural retrieval-ranking
limitation at this corpus's current size/vocabulary overlap. This is exactly the
kind of finding `research.md` §7 anticipated a benchmark might produce, and is
itself useful evidence for any future retrieval-technique iteration (e.g.
reconsidering after real corpus growth, per `research.md` §7's own stated
reconsideration condition) — but is not evidence for adopting a candidate that has
not, in fact, cleared the bar today.
