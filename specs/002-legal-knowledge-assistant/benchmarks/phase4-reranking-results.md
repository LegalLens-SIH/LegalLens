# Phase 4 Reranking Evaluation Result (T012)

**Feature**: [../spec.md](../spec.md) | **Tasks**: [../tasks.md](../tasks.md)

## T012 `[EVIDENCE GATE]` — **N/A, not executed**

**Reason**: tasks.md's Phase 4 gate and this session's explicit instruction both
condition T012 on Phase 3 (T011) having produced "a qualifying base retrieval
candidate whose remaining weakness matches the reranker gate" (strong top-3 recall,
weak top-1 alone). Phase 3 (`phase3-retrieval-results.md`) declined — **no
candidate cleared SC-001/SC-003/SC-009 at all**, so there is no qualifying base
candidate to rerank.

Reranking is designed to fix a top-1-vs-top-3 ranking gap on an otherwise-viable
candidate. It is not designed to, and evaluating it here would not credibly
address, T010's actual remaining failure mode: wrong-rule (7.69%) and adversarial
class-B (15.38%) rates that **did not move at all** between T009 (before query
rewriting) and T010 (after) — a symptom of a categorical confusion between two
rules' near-duplicate vocabulary, not a ranking-order problem within an otherwise-
correct candidate set. Running T012 here would risk producing a number that looks
superficially better without addressing the actual, already-identified failure
mode — exactly the kind of unjustified technology adoption this feature's
benchmark-first discipline exists to prevent.

**Outcome**: T012 marked N/A. No reranking model was downloaded, installed, or
evaluated.
