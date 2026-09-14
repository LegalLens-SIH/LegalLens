# Quickstart: Validating the Legal Knowledge Assistant (once implemented)

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

This is a validation guide for the feature once built — not a setup guide for today
(nothing in this feature exists yet; this document describes how its P1 user stories
would be proven end-to-end, matching `001-legal-rag` quickstart.md's own format). It
intentionally does not include implementation code.

## Prerequisites (once implemented)

- Existing project setup: `backend/.venv311` active, MongoDB running, backend serving
  on port 8000 — identical to today's prerequisites, unchanged by this feature.
- `001-legal-rag`'s verified legal corpus already in place and unmodified
  (`backend/legal_corpus/legal_metrology_packaged_commodities_2011.json`) — this
  feature reuses it as-is; no separate acquisition step of its own.
- research.md's Phase 0 benchmark (§1, §4) already run and concluded with a specific
  technology combination that cleared spec SC-001/SC-003/SC-009 — if it did not (see
  research.md §7), this feature does not proceed to implementation at all, and this
  quickstart does not apply.
- An Official test account (the same `official_user`-role mechanism
  `001-legal-rag`'s own tests and Official Review Workflow already use).

## Scenario 1 — Official asks a free-form legal question and gets a grounded answer (User Story 1, P1)

1. As an Official, submit a free-form question with a known correct answer in the
   verified corpus (e.g. "What does Rule 6(11) require regarding unit sale price?").
2. Call `POST /api/official/legal-assistant/ask` with that question, no
   `case_context`.
3. **Expected**: `status: "answered"`, `answer_text` present and labeled as
   AI-generated, `citations` non-empty with the correct `rule_sub_rule_clause`
   ("Rule 6(11)") and full source provenance, and every claim in `answer_text`
   traceable to the cited provision's `text`.

## Scenario 2 — Insufficient legal basis is surfaced safely (User Story 1, P1)

1. Submit a question with no adequate basis in the verified corpus (e.g. a question
   about a rule_id outside `LMPC-R6`/`LMPC-R24`, or an entirely unrelated topic).
2. **Expected**: `status: "insufficient_evidence"`, `answer_text: null`,
   `citations: []` — never a citation, never generated legal-sounding text presented
   as fact.

## Scenario 3 — Confusable/adversarial query is handled safely (User Story 1, P1)

1. Submit a question from `001-legal-rag`'s own Phase 8 adversarial set or an
   equivalent (e.g. "Whose name and address must appear on a wholesale package?" —
   the `LMPC-R6`-vs-`LMPC-R24` near-duplicate class research.md §0 discusses).
2. **Expected**: either the correct rule_id's provision is returned confidently, or
   `ambiguous_candidates` explicitly presents the distinct options — never a
   confident, wrong-rule citation presented as the sole answer (spec FR-017, SC-009).

## Scenario 4 — Legal background from within a case review, without altering the case (User Story 2, P2)

1. As an Official, open an existing case in the Official Review Workflow and record
   its `overall_status`/`compliance_score`/`officialDecision` (if any) before asking
   the assistant anything.
2. From within that case-review context, ask the assistant an arbitrary legal
   question (`case_context` set to the case's `revisionId`).
3. **Expected**: a grounded answer or safe refusal exactly as in Scenarios 1–2,
   displayed visually and structurally distinct from the case's compliance findings,
   `001-legal-rag`'s deterministic Legal Basis panel, and the decision-recording
   controls.
4. Re-check the case's `overall_status`/`compliance_score`/`officialDecision` —
   **expected**: byte-identical to step 1.

## Scenario 5 — Amendment/version history is represented correctly (User Story 3, P2)

1. Ask "What changed in Rule 6 after the 2022 amendment?" or an equivalent question
   about a provision the corpus records as amended (e.g. `unit_sale_price`'s Rule
   6(11), or `maximum_retail_price_mrp`'s Rule 6(1)(e)).
2. **Expected**: the answer is grounded in the currently-effective version by
   default; if the amendment itself was asked about, original and amended text are
   presented distinctly, never blended into one undifferentiated answer.
3. Inspect the citation's provenance — **expected**: source identity, version,
   amendment relationship (`is_amendment`/`amends_source_id`) all visible.

## Scenario 6 — Compliance status is unaffected by any assistant interaction (User Story 5, P1)

1. Record a case's `overall_status`/`compliance_score`/`officialDecision` before any
   assistant call.
2. Call the assistant endpoint (a successful answer, a forced refusal, and a forced
   error/timeout — three separate calls).
3. **Expected**: re-fetch the same case after each — all three fields byte-identical
   to step 1 in all three cases.

## Scenario 7 — Existing behavior preserved when the assistant is unavailable (User Story 6, P1)

1. Disable this feature entirely (however the implementation-time toggle works) or
   simply don't register its router.
2. Run the full existing test suite.
3. **Expected**: 324 passed, 1 skipped — the current post-`001-legal-rag` baseline,
   identical, zero test modified.
4. Manually walk through one Manufacturer self-check and one Official case-review +
   decision (including viewing `001-legal-rag`'s Legal Basis panel), confirming no
   visible change.

## Scenario 8 — Retrieval-quality benchmark gate (Success Criteria SC-001–SC-010, SC-012)

1. Run the Phase 0 benchmark query set (research.md §1) — at least as large and
   adversarially rigorous as `001-legal-rag`'s own 36-query Phase 8 set — against the
   final implemented retrieval+generation pipeline.
2. **Expected**: ≥90% top-1 accuracy (SC-001), ≥97% top-3 recall (SC-002), <5%
   wrong-rule (SC-003), <5% wrong-field (SC-004), 100% citation correctness (SC-005),
   100% provenance correctness (SC-006), 100% groundedness (SC-007), ≥95% correct
   refusal on the no-coverage subset (SC-008), <5% plausible-but-wrong on the
   dedicated adversarial subset (SC-009), ≥95% repeatability across repeated
   submissions of the same question (SC-012).
3. **If any bar is not met**: per research.md §7, this is a legitimate "do not ship
   this configuration" outcome — either iterate on research.md §1's technique
   candidates, or decline the feature at this gate, exactly as `001-legal-rag`'s
   Phase 8 was declined for a different technology at a different (but analogous)
   gate.
