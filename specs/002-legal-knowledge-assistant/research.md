# Phase 0 Research: Advanced Legal Knowledge Assistant for Officials

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Every decision below follows the format: what remains open, why it is not resolved
here, what evidence already exists, and exactly what a future benchmark task must
measure before a technology is adopted. **No embedding model, vector database,
reranker, or generation model is selected in this document** — per spec FR-005,
FR-006, FR-032 and the explicit instruction governing this plan, technology choice is
deferred to a dedicated, benchmark-gated task, the same discipline
`001-legal-rag`'s own Phase 8 used before declining semantic retrieval for its
different (exact-key) use case.

## 0. Why `001-legal-rag`'s Phase 8 conclusion does not settle this feature's question

`001-legal-rag`'s Phase 8 benchmark (`specs/001-legal-rag/tasks.md`, "Phase 8
Evaluation Outcome") ran a 36-query benchmark — including natural-language,
adversarial-confusable, generic, multi-topic, and no-result query styles — against
the same 11-provision corpus this feature will also query, using
`BAAI/bge-small-en-v1.5` and `BAAI/bge-m3` for pure embedding-similarity retrieval.
Results: 78.1%/81.25% top-1 accuracy, 15.6% wrong-rule rate for both models (versus
that spec's own ≥90%/<5% bars) — declined for `001-legal-rag`'s exact-key retrieval
because deterministic lookup already achieves 100% for the only query shape that
mechanism ever receives.

**This feature is different in exactly one respect that matters here**: unlike
`001-legal-rag`, this feature's real, required query shape *is* free-form natural
language (spec FR-004) — there is no exact-key deterministic alternative to fall back
to. That single fact is why this feature exists at all (spec "Relationship to
001-legal-rag"). But it does **not** mean the corpus has gotten any easier to
retrieve from correctly — it is the *same* 11 provisions, with the *same* documented
near-duplicate cross-rule vocabulary (`LMPC-R6` vs `LMPC-R24` "manufacturer name and
address," "net quantity") that caused Phase 8's wrong-rule failures. **The Phase 8
numbers are therefore treated as this feature's own starting empirical baseline for
naive single-embedding-model retrieval — a baseline already known to fall short of
this feature's own SC-001 (≥90% top-1) and SC-003 (<5% wrong-rule) bars.** A future
benchmark task must not re-run only what Phase 8 already ran and call it sufficient;
it must evaluate what *additional* technique(s), if any, close that gap.

## 1. Retrieval technique evaluation methodology (embedding model + beyond)

**What is decided here**: the evaluation methodology and candidate technique list.
**What is NOT decided here**: which technique(s) are adopted.

Given §0's evidence, a future benchmark task MUST evaluate more than a single
embedding model's raw similarity ranking, because that specific approach already has
direct evidence of falling short at this corpus's size. Candidate techniques to
benchmark (none assumed superior):

- **Pure dense-embedding retrieval** (what Phase 8 already measured) — retained as
  the baseline every other technique must beat, not re-litigated from scratch.
- **Hybrid keyword + semantic retrieval** — this corpus's provisions carry exact,
  known `linked_rule_id`/`linked_field` metadata (`001-legal-rag`'s existing corpus
  schema); a technique that combines lexical/keyword signal (e.g. matching "wholesale"
  vs "retail" package language, which Phase 8's own confusable-query failures suggest
  pure embedding similarity under-weights) with semantic similarity is a genuine,
  distinct candidate, not a restatement of the pure-embedding baseline.
- **Reranking of top-N candidates** — `001-legal-rag` research.md §3 already
  identified `BAAI/bge-reranker-v2-m3` as a candidate for a different feature; here,
  reranking is worth evaluating specifically because Phase 8's own data shows the
  *correct* answer is frequently present in the top-3 (top-3 accuracy 90.6%/93.75%)
  even when top-1 is wrong — exactly the situation a reranker is designed to fix. Not
  assumed necessary until benchmarked against this feature's own query set.
- **Query rewriting/expansion** (e.g. expanding "wholesale package" queries with
  corpus-known synonyms before embedding) — a lower-infrastructure-cost technique
  worth measuring before reaching for a heavier model or a reranker.
- **A larger/different embedding model** — evaluate on its own merits (e.g. a newer
  MTEB-leaderboard model at the time of the benchmark task), never chosen by
  reputation alone; `001-legal-rag`'s own experience (bge-m3, ~17x bge-small's
  parameters, produced the *same* 15.6% wrong-rule rate) is direct evidence that
  "bigger model" alone is not guaranteed to fix this corpus's specific
  near-duplicate-vocabulary failure mode.

**Benchmark requirement for this feature specifically** (beyond what Phase 8 already
did): the benchmark query set MUST be at least as large and adversarially rigorous as
`001-legal-rag`'s 36-query set (spec SC-009 sets a *stricter* <5% plausible-wrong bar
for the adversarial subset than Phase 8's general wrong-rule measurement), and MUST
evaluate every candidate technique against the same query set for a fair comparison —
exactly the controlled-benchmark discipline Phase 8 itself modeled.

**Decision deferred to**: a Phase 0-equivalent benchmark task at `/speckit-tasks`
time, before any retrieval code is written.

## 2. Vector storage / index (if semantic retrieval is adopted at all)

**Decision**: Not made here — contingent entirely on §1's outcome. If §1's benchmark
finds no retrieval technique clears the spec's acceptance bar (SC-001/SC-003/SC-009),
this question does not need to be answered at all (see §7, the explicit
"skip this feature" outcome).

**If** a semantic-retrieval technique is adopted, the storage question is genuinely
open in a way it was not for `001-legal-rag`:

- **In-process, file-backed similarity search** (what `001-legal-rag` research.md §1
  chose for its own much-smaller need) — still viable at this corpus's *current*
  scale (11 provisions) regardless of which technique from §1 is chosen, and remains
  the lowest-infrastructure-cost option.
- **An externally-hosted managed vector database** (e.g. Qdrant Cloud, evaluated and
  rejected only *for Phase 1 scope* by `001-legal-rag` research.md §1) — spec FR-032
  explicitly permits this here if benchmark-justified. A justification would need to
  rest on something beyond raw corpus size (which alone does not justify it at 11
  provisions) — e.g. if §1's benchmark shows hybrid/metadata-filtered retrieval
  meaningfully outperforms pure similarity search and a managed service's filtering
  capability is materially simpler to operate correctly than an in-process
  equivalent, or if corpus growth (spec User Story 4) is concretely planned on a
  timeline that makes building in-process infrastructure now wasted work.

**Decision deferred to**: the same benchmark task as §1, only evaluated if §1
concludes semantic retrieval is adopted at all.

## 3. Reranking

**Decision**: Not made here — see §1's candidate-technique list. Only evaluated if
§1's own benchmark shows top-1 alone is insufficient but top-N recall is
strong enough (Phase 8's own top-3 numbers, 90.6%/93.75%, suggest this is plausible)
that reranking specifically — not a different embedding model, not hybrid retrieval —
is the most likely fix. `BAAI/bge-reranker-v2-m3` (the candidate `001-legal-rag`
research.md §3 already surveyed for a different feature) is one option to evaluate,
not the presumed answer.

## 4. Generation model (grounded explanation)

**Decision**: Not made here.

Unlike `001-legal-rag` (where a generation step was explicitly optional and deferred
past Phase 1 entirely — research.md §4), this feature's spec treats a generation step
as **required** (spec Assumptions: free-form Q&A without any synthesized explanation
would be of materially lower value than raw matched provisions alone). This makes the
generation-model decision load-bearing here in a way it never was for `001-legal-rag`,
and it must be held to the same groundedness bar (spec FR-013, SC-007) with zero
tolerance for unsupported claims.

Candidates to evaluate (none assumed final):

- **Reuse the existing Gemini integration** (`backend/ocr/gemini_service.py`) — the
  option `001-legal-rag` research.md §4 already identified as lowest-new-infrastructure
  for a hypothetical generation step: the async-client/timeout/graceful-fallback
  pattern already exists and is proven in this codebase. Reusing it here would still
  require its own groundedness benchmark (FR-013) before acceptance — "already
  integrated" is not the same as "already proven grounded for this task."
- **A hosted Hugging Face Inference API model** — avoids adding a second AI-vendor
  trust surface if that is a project goal; not self-hosted (self-hosting a
  generation-capable model on this CPU-only 16GB machine remains unrealistic, the
  same conclusion `001-legal-rag` research.md §4 reached).
- **No generation at all, presenting only matched provision text + citations** —
  explicitly kept on the table as a fallback outcome if no generation candidate
  clears the groundedness bar (SC-007) reliably; this would still deliver the
  feature's core "find the right provision for a free-form question" value even
  without synthesized explanation, at a strictly higher trust bar than any generation
  step.

**Decision deferred to**: a dedicated groundedness benchmark (distinct from §1's
retrieval-accuracy benchmark) measuring SC-007 directly — every generated explanation
in the benchmark set checked for unsupported claims against its cited source text.

## 5. Confidence / refusal threshold mechanics (spec FR-015–FR-017)

**Decision**: Deferred to implementation-time empirical tuning, not fixed here —
directly continuing `001-legal-rag` research.md §6's own precedent ("a
configurable, environment-driven threshold is this project's established pattern...
the specific numeric default requires empirical validation against real corpus/query
pairs").

**New evidence specific to this feature**: `001-legal-rag`'s Phase 8 benchmark already
demonstrated, empirically, that **no single fixed similarity threshold cleanly
separates confident-correct from confident-wrong at this corpus's size** — the
threshold sweep in that benchmark showed an out-of-scope query ("penalty under the
Companies Act") scoring *higher* than several genuinely correct answers scored for
their own correct provision. This is a direct, concrete warning against assuming a
simple similarity-score cutoff will satisfy spec FR-016 (prefer refusal over a
plausible-wrong answer) for this feature. Whatever refusal mechanism is designed here
must be benchmarked against SC-008 (refusal correctness) and SC-009 (adversarial
plausible-wrong rate) specifically, not assumed to work by analogy to a threshold that
already failed this exact test once.

## 6. Audit logging (spec FR-028)

**Decision**: Reuse the existing `history`-collection pattern already used for
Manufacturer/Official actions (`backend/api/auth.py`'s `db.history`,
`backend/api/manufacturer.py`/`backend/api/official.py`'s `history` entries on
submit/decision actions) — a `Query Interaction Record` (data-model.md) is written the
same way, not a new logging subsystem.

**Rationale**: Constitution Principle VIII (Surgical, Scoped Changes) — this project
already has one established pattern for "who did what, when," and introducing a
second, parallel logging mechanism for this feature alone would be an unjustified
complexity increase.

**Alternatives considered**: A dedicated `legal_assistant_queries` collection —
plausible if query volume or retention requirements diverge materially from the
existing `history` collection's shape; not adopted here without a concrete reason,
consistent with the constitution's "complexity must be justified by a concrete,
stated need — not by anticipated future use."

## 7. The explicit "skip this feature" outcome

**Decision**: Genuinely on the table, not a formality. If §1's benchmark — run against
a query set at least as rigorous as `001-legal-rag`'s own Phase 8 set, per §0's
evidence that the naive-embedding baseline already falls short at this corpus's size —
finds that **no** combination of retrieval technique, reranking, or query
reformulation clears spec SC-001/SC-003/SC-009 at the corpus's current scale, this
feature should be declined at that gate, exactly as `001-legal-rag`'s Phase 8 was,
with the same evidence-based, clean-reversion discipline (constitution Principle XII).
This would not be a failure of this planning process — it would be exactly the outcome
the benchmark-before-adopt discipline exists to catch honestly rather than shipping an
assistant known to produce plausible-wrong legal citations.

**A materially relevant mitigating factor absent from `001-legal-rag`'s Phase 8
scope**: this feature's spec explicitly anticipates corpus growth (User Story 4) and a
required generation step (§4) that could itself perform disambiguation using context
the pure-retrieval Phase 8 benchmark never had available (e.g. a generation step
asked to choose between two retrieved candidates, given both, rather than a bare
top-1 similarity ranking) — this is exactly the kind of technique §1's benchmark must
test before concluding the feature is not viable, not a reason to skip testing it.

## Summary of what remains genuinely open (by design — all resolved by a future benchmark task, not by this plan)

- Which retrieval technique(s) (§1) — pure embedding, hybrid, reranking, query
  rewriting, or some combination — if any, clear this feature's own accuracy bar at
  this corpus's current scale.
- Whether a vector index is introduced at all, and if so, in-process vs.
  externally-hosted (§2) — contingent on §1.
- Whether reranking is adopted (§3) — contingent on §1's top-N recall findings.
- Which generation model, if any, is used for the required explanation step, and
  whether it reliably clears the groundedness bar (§4).
- The exact refusal-confidence mechanism and its threshold(s) (§5) — with §5's own
  warning that a naive fixed-threshold approach already has counter-evidence from
  `001-legal-rag`'s Phase 8 benchmark.
- The exact numeric latency target (spec SC-011).
- Whether this feature is adopted at all, versus declined at the benchmark gate (§7).

## §1/§7 Outcome (2026-09-14) — resolved by the actual benchmark run

**§7's "skip this feature" outcome occurred.** A 45-query benchmark (tasks.md
Phase 2–3, T005–T011) evaluated four retrieval candidates against this section's
own methodology: pure dense embedding (`bge-small-en-v1.5`, `bge-m3`), a hybrid
BM25+semantic candidate (RRF-fused), and query rewriting layered on the hybrid.
Best result (query-rewrite + hybrid): 87.18% top-1 (need ≥90%), 7.69% wrong-rule
(need <5%), 15.38% plausible-but-wrong on the adversarial subset (need <5%). No
candidate cleared all three. **Reranking (§3) and the generation model decision
(§4) were consequently never reached** — there was no accepted retrieval pipeline
to rerank or to generate answers from. Full evidence:
`../002-legal-knowledge-assistant/benchmarks/` (dataset, harnesses, all four
candidates' results, `phase3-retrieval-results.md`, `t020-evidence-package.md`).

**Feature 002 was declined** (`tasks.md` T020/T046) on this evidence, with §7's own
reconsideration framing carried into `tasks.md`'s closing "Reconsideration
Conditions" section — corpus growth that reduces `LMPC-R6`/`LMPC-R24` vocabulary
overlap, a genuinely new disambiguation technique (e.g. generation-assisted, never
tested here since Phase 5 was never reached), or a business case justifying
higher-effort investment despite the current accuracy gap.
