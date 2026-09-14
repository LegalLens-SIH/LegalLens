# Quickstart: Validating Legal RAG (once implemented)

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

This is a validation guide for the feature once built — not a setup guide for today
(nothing in this feature exists yet; this document describes how its P1 user stories
would be proven end-to-end). It intentionally does not include implementation code.

## Prerequisites (once implemented)

- Existing project setup: `backend/.venv311` active, MongoDB running, backend serving
  on port 8000 — identical to today's prerequisites, unchanged by this feature.
- Legal corpus acquired and verified for at least `LMPC-R6-MANDATORY-DECLARATIONS`
  (spec's stated priority) — a Phase 1 implementation prerequisite, not something this
  quickstart performs.
- No new environment variable is assumed here beyond what a future task defines
  (e.g. a `LEGAL_RAG_ENABLED` flag, matching the existing `GEMINI_ENABLED`/
  `OCR_PREPROCESSING_ENABLED` toggle convention already used in this project).

## Scenario 1 — Retrieve legal basis for an LMPC-R6 finding (User Story 1, P1)

1. Run a self-check or scan that produces a finding under `LMPC-R6-MANDATORY-DECLARATIONS`
   on `maximum_retail_price_mrp` (any existing fixture that already produces this
   finding today works unchanged).
2. Call `GET /api/compliance/legal-basis?rule_id=LMPC-R6-MANDATORY-DECLARATIONS&field=maximum_retail_price_mrp`.
3. **Expected**: `status: "found"`, at least one provision with a non-empty
   `rule_sub_rule_clause`, `text`, and `source` citation.
4. Repeat for the other 7 `LMPC-R6` fields.

## Scenario 2 — Retrieve legal basis for an LMPC-R24 finding (User Story 2, P2)

1. Run a scan against the `wholesale_package` validation profile producing a finding
   under `LMPC-R24-WHOLESALE-DECLARATIONS`.
2. Call the same endpoint with `rule_id=LMPC-R24-WHOLESALE-DECLARATIONS`.
3. **Expected**: same quality bar as Scenario 1.

## Scenario 3 — Missing/unverified source (User Story 3, P1)

1. Call the endpoint with a `rule_id` known to have no corpus coverage (e.g.
   `LMGEN-R12-VERIFICATION-INTERVALS`, out of required scope).
2. **Expected**: `status: "not_available"`, `provisions: []` — never a citation, never
   generated text.

## Scenario 4 — Compliance status is unaffected (User Story 5, P1)

1. Record a finding's `status`/`compliance_score` before any legal-basis call.
2. Call the legal-basis endpoint (success and, separately, a forced failure case).
3. **Expected**: re-fetch the same finding — `status`/`compliance_score` are
   byte-identical to step 1 in both cases.

## Scenario 5 — Existing behavior preserved when unavailable (User Story 6, P1)

1. Disable the legal-basis feature entirely (however the implementation-time toggle
   works).
2. Run the full existing test suite.
3. **Expected**: 289 passed, 1 skipped — identical to the pre-feature baseline, zero
   test modified.
4. Manually walk through one Manufacturer self-check and one Official case-review +
   decision, confirming no visible change.

## Scenario 6 — Official sees legal basis during case review (User Story 4, P2)

1. As an Official, open `case-details.html` for a case with a finding that has an
   available legal basis.
2. **Expected**: legal basis is visible, visually distinct from the finding's status
   and from the decision-recording controls (`log-case-action.html`), and has no
   control that writes it back into the finding or the decision.
