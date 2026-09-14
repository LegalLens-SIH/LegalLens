# Phase 6 Security & Prompt-Injection Resistance Review (T017–T019)

**Feature**: [../spec.md](../spec.md) | **Tasks**: [../tasks.md](../tasks.md)

Executed per this session's explicit instruction regardless of Phase 3's decline
outcome (`phase3-retrieval-results.md`), so that T020's evidence package is
complete: even though this run recommends DECLINE at the retrieval stage, the
security/architecture findings below remain valid evidence for T020's decision
and for any future retrieval-technique iteration.

## T017 — Adversarial prompt-injection test set

Five injection-style probes (system-prompt extraction, role-override, "disregard
the retrieved text" instructions, a request for `officialDecision` contents, and a
code-execution-style payload) were run as query text against the actual Phase 3
candidate code (`predict_embedding_002.py`, and by construction the same result
applies to `predict_hybrid_002.py`/`predict_queryrewrite_002.py`, which only add a
BM25 term-frequency score computed the same inert way). Full results:
`t017-injection-test-results.json`.

**Finding**: every probe was treated as ordinary text — it produced only a numeric
similarity/frequency score and a resulting ranking, structurally indistinguishable
from any other query. **No code path in any Phase 3 candidate script contains an
`eval`, a format-string injection point, a system call, or any mechanism that
parses query or corpus text as an instruction.** This is not a property of careful
prompt design — it is a property of the technique class: dense embeddings and
BM25 are pure numeric functions of text; they have no capacity to "obey" text
content in the first place.

**Important scope note**: this finding covers only the retrieval/ranking stage
actually exercised in this run. Phase 5 (generation) was not reached
(`phase5-generation-results.md`) — a real prompt-injection risk exists **only** at
a future generation step (an LLM call that receives retrieved passages as part of
its input), which this run did not build or test. T018's design requirement below
is written for that future step, not for the retrieval stage, which is already
structurally immune.

## T018 — Pre-implementation threat-model and design review

**Required boundary for any future generation step** (binds Phase 7's T023
implementation and T033's post-implementation re-verification, if this feature is
ever revisited past today's decline):

1. Retrieved corpus passages MUST be passed to any generation call as a clearly-
   delimited, structurally separate data field (e.g. a dedicated "context
   passages" array/message role in whatever generation API is eventually used) —
   never concatenated into, or allowed to influence, a system/control instruction
   string.
2. The generation call's own system/instruction prompt MUST be static, defined in
   code, and never constructed by concatenating any part of a retrieved passage
   or the Official's own question text into an instruction-bearing position.
3. Output from generation MUST be treated as plain text for display — never
   evaluated, executed, or used to construct a further query/instruction without
   going through the same citation/groundedness checks (spec FR-013, SC-007) every
   other generated answer goes through.
4. This checklist applies regardless of which generation candidate (T013 Gemini
   reuse, T014 hosted HF, or none/T015) is eventually evaluated — it is a
   technology-independent architectural requirement, not tied to one vendor.

**Corpus/benchmark-code review**: the Phase 3–5 benchmark harness's own code
(`score_retrieval.py`, `score_groundedness.py`, the three `predict_*.py`
candidate scripts) was reviewed and contains no violation of this boundary — none
of it has a generation step at all in this run (Phase 5 was not reached), so there
is nothing to correct at this stage.

## T019 — Pre-implementation architecture/interface-boundary review

Reviewed `contracts/legal-assistant-api.md` and `data-model.md` (the design — no
Phase 7 code exists; T021–T034 were not started this run, consistent with the
hard T020 gate):

- `LegalQuery`'s only fields are `query_id`, `question_text`, `asked_by`,
  `asked_at`, `case_context` — `case_context` is explicitly documented in
  data-model.md as "used only to associate the LegalQuery with a case for
  display/audit purposes... never read by the retrieval/generation step to
  influence the answer." **Confirmed**: no field, parameter, or declared
  dependency anywhere in `contracts/legal-assistant-api.md` or `data-model.md`
  provides a path to `complianceResult`, `overall_status`, `compliance_score`,
  `officialDecision`, or `decisionHistory`.
- The contract (`contracts/legal-assistant-api.md`) defines exactly one write
  surface anywhere in this feature's design: the additive `QueryInteractionRecord`
  audit entry (reusing the existing `history` collection pattern) — no write
  operation against any compliance or decision field is declared anywhere.
- **Actual code confirmation, this run**: `backend/models/legal_assistant.py`
  (T001, created this run) imports only `backend.models.legal.LegalProvision` —
  it does not import, reference, or construct any `ComplianceEngine` type, and
  `backend/services/legal_assistant.py` (T003, created this run) contains only a
  module docstring, zero logic, zero imports beyond `from __future__ import
  annotations`. Both are unreachable — neither is imported by `backend/main.py` or
  any other file — so they have zero runtime effect regardless.

**Result**: T018 and T019's design checklists are both satisfied by the current
design and by everything actually built in Phase 1 of this run. This finding is
independent of Phase 3's retrieval decline — it would remain valid evidence
whether or not a future retrieval iteration succeeds.
