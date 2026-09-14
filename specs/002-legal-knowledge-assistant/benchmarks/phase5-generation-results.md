# Phase 5 Generation / Grounding Evaluation Result (T013–T016)

**Feature**: [../spec.md](../spec.md) | **Tasks**: [../tasks.md](../tasks.md)

## T013–T016 `[EVIDENCE GATE]` / `[DECISION POINT]` — **N/A, not executed for their intended purpose**

**Reason**: tasks.md's Phase 5 "Depends on" line and this session's explicit
instruction both condition T013–T015 on "a retrieval pipeline exist[ing]" (i.e.
Phase 3/4 having accepted a specific retrieval+ranking technique to generate
answers from). Phase 3 (`phase3-retrieval-results.md`) declined — no candidate
cleared SC-001/SC-003/SC-009 — and Phase 4 was not run (`phase4-reranking-
results.md`). There is therefore no accepted retrieval pipeline for a generation
step to be evaluated against; running a groundedness benchmark on top of a
retrieval stage that itself does not reliably return the correct provision would
not produce meaningful evidence about generation quality — a wrong retrieved
passage cannot be "grounded" or "ungrounded" in any way that matters, the answer
built from it is already unreliable at the retrieval stage.

**T007's groundedness harness (`score_groundedness.py`) was built and is ready**,
but was not exercised against a real generation candidate in this run, for the
reason above. This is recorded as a limitation of this specific run's evidence
package, not a decision that generation would necessarily fail if evaluated later
— see `research.md` §4's own note that citation-only remains a fully legitimate,
always-available outcome regardless of the retrieval question. If retrieval is
ever resolved in a future iteration (per `phase3-retrieval-results.md`'s
reconsideration note), T013–T016 should be executed then, against whatever
retrieval pipeline that iteration accepts.

**Outcome**: T013 (Gemini reuse), T014 (hosted Hugging Face generation), and T015
(citation-only) were not evaluated. No generation model or hosted API was called.
T016 has no result to select between. The provisional, structurally-always-safe
answer, if this feature is ever revisited, remains T015 (citation-only) as the
zero-risk floor — never assumed superior to a properly benchmarked generation
step, but never worse than "no answer" either.
