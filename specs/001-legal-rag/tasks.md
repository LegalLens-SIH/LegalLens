---

description: "Task list for Legal RAG (legally grounded knowledge layer)"
---

# Tasks: Legal RAG (Legally Grounded Knowledge Layer)

**Input**: Design documents from `specs/001-legal-rag/` (spec.md, plan.md, research.md, data-model.md, contracts/legal-basis-api.md, quickstart.md)

**Prerequisites**: spec.md ✅, plan.md ✅ (both already committed)

**Tests**: Included throughout — constitution Principle VII and the spec's own Success
Criteria (SC-001–SC-009) require test + benchmark-style acceptance for this feature,
matching this project's existing discipline (never optional here).

**Organization**: This feature is a data pipeline before it is a set of independent
user-facing increments — retrieval cannot be tested, let alone shown to anyone, before
verified legal text exists. Per explicit instruction, phases below follow the required
pipeline sequence (acquire → structure → map → retrieve → test safety → optionally add
semantic search → generation explicitly excluded) rather than the fully-parallel
per-user-story default. Each task still carries a `[Story]` label mapping it back to
spec.md's user stories where applicable, so story-level traceability is preserved even
though the phases themselves are sequential, not parallel tracks.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 (LMPC-R6 retrieval, P1) · US2 (LMPC-R24 retrieval, P2) · US3
  (missing-source safety, P1) · US4 (Official display, P2) · US5 (compliance-status
  immutability, P1) · US6 (graceful degradation, P1) — from spec.md
- Every implementation task names its exact file path, per plan.md's Project Structure

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Scaffolding only — no legal content, no retrieval logic yet.

- [ ] T001 Create the empty `backend/legal_corpus/` directory with a `.gitkeep` placeholder (mirrors this project's existing convention for not-yet-populated data directories, e.g. `training/rejected_images/.gitkeep`)
- [ ] T002 [P] Create `backend/models/legal.py` with empty/stub Pydantic classes for `LegalSourceDocument`, `LegalProvision`, `LegalProvisionRuleLink`, `LegalBasisResult` per data-model.md's field tables — fields only, no logic yet
- [ ] T003 [P] Add a `LEGAL_RAG_ENABLED` flag to `.env.example` (default `false`), following the exact existing pattern of `GEMINI_ENABLED`/`OCR_PREPROCESSING_ENABLED` — this is the toggle User Story 6's graceful-degradation tests exercise

**Checkpoint**: Empty scaffolding exists; nothing is wired up yet; existing test suite (289/1) is unaffected by this phase alone.

---

## Phase 2: Legal-Source Acquisition & Verification (BLOCKING — required before any phase below)

**Purpose**: Establish the actual, verified legal text this entire feature depends on.
**This phase is not a coding task** — it is a human/process verification task per
constitution Principle III and plan.md's Assumptions ("verified source" requires
deliberate checking against an authoritative publication, not automated scraping).
**No task in this phase may be executed by generating, paraphrasing, or guessing
legal text** — every provision must be transcribed from and checked against an
actual authoritative source before T005 or anything downstream can begin.

⚠️ **CRITICAL**: Nothing in Phase 3 onward may proceed with placeholder, sample, or
"TODO"-style legal text standing in for the real thing. A task in this phase is only
complete when its output is real, sourced, and checkable against a citation — never
when it merely satisfies a schema shape.

- [ ] T004 Acquire the authoritative text of Legal Metrology (Packaged Commodities) Rules, 2011, Rule 6 (all sub-rules corresponding to the 8 `LMPC-R6-MANDATORY-DECLARATIONS` fields: manufacturer/packer/importer details, country of origin, generic name, net quantity, month/year of manufacture, MRP, unit sale price, consumer care details) from an authoritative source (official Gazette notification or a recognized legal database), recording the exact citation for each sub-rule
- [ ] T005 Identify and acquire any verified amendments/notifications affecting Rule 6 that are still in force, recording which text is original vs. amended and which is currently effective (spec Edge Cases, FR-003)
- [ ] T006 Acquire the authoritative text of Rule 24 (all sub-rules corresponding to the 3 `LMPC-R24-WHOLESALE-DECLARATIONS` fields: name and address of manufacturer/packer, identity of commodity, total number of retail packages or net quantity), recording exact citations, per the spec's stated priority (Rule 6 first, Rule 24 second)
- [ ] T007 Identify and acquire any verified amendments/notifications affecting Rule 24 that are still in force
- [ ] T008 For every acquired provision (T004–T007), record its verification note (FR-001) — where/how it was checked against the authoritative source — and a version/acquisition-date identifier (FR-007, FR-028); reject and re-acquire any provision whose source cannot be confirmed (plan.md research.md §5, Edge Cases)

**Checkpoint**: A written, human-verifiable record of real Rule 6 and Rule 24 legal text exists, each unit citable and versioned. **No task below may begin until at least T004+T005 (Rule 6) are complete** — Rule 24 (T006–T007) may lag behind per the stated priority, provided Phase 3 onward simply has no Rule 24 content to structure yet (not an error — see spec User Story 2, Acceptance Scenario 2, "not available" is the correct behavior for unacquired coverage).

---

## Phase 3: Structure Verified Provisions & Provenance

**Purpose**: Turn Phase 2's verified text into the structured shape data-model.md defines.
**Depends on**: Phase 2 (only for whichever rule's text is actually verified so far).

- [ ] T009 Create `backend/legal_corpus/legal_metrology_packaged_commodities_2011.json` (or the format finalized here) encoding each `LegalSourceDocument` from T004–T007 with all fields data-model.md requires as non-empty: `source_id`, `title`, `citation`, `version`, `acquired_at`, `is_amendment`, `verification_note` — "`source_id`, `citation`, `version`, `verification_note` are all required and non-empty — a LegalSourceDocument with any of these missing cannot be referenced by a LegalProvision" (data-model.md)
- [ ] T010 Encode each verified Rule 6 provision from T004–T005 as a `LegalProvision` record with `provision_id`, `source_id`, `rule_sub_rule_clause` (exact sub-rule reference, e.g. "Rule 6(1)(e)"), `text` (verbatim), `effective_status`, `is_currently_effective` — "`text` must be non-empty and must trace to a source_id that exists" and "exactly one LegalProvision in a supersedes_provision_id chain may have is_currently_effective = true at a time" (data-model.md)
- [ ] T011 Encode each verified Rule 24 provision from T006–T007 the same way, if acquired by this point (else this task is deferred, not skipped-as-failed)
- [ ] T012 [P] Implement a corpus-loading function in `backend/services/legal_retrieval.py` that reads T009's file and validates every record against T010/T011's required-field rules at load time, refusing to load (loud failure, not silent skip) any record missing a required field
- [ ] T013 [P] `backend/services/test_legal_retrieval.py`: test that loading a corpus file with a missing `verification_note`, `citation`, or `text` field is rejected, not silently accepted (proves T012's validation actually enforces data-model.md's rules)

**Checkpoint**: Verified legal text from Phase 2 is now machine-readable and structurally validated — still not linked to any LegalLense rule_id, still not retrievable by anything.

---

## Phase 4: Map Provisions to Existing LegalLense Rule IDs and Fields

**Purpose**: Build the deterministic join between the legal corpus and `ComplianceEngine`'s existing vocabulary — this is what makes rule_id-first retrieval (FR-009) possible.
**Depends on**: Phase 3.

- [ ] T014 For each structured Rule 6 provision (T010), create a `LegalProvisionRuleLink` record with `rule_id = "LMPC-R6-MANDATORY-DECLARATIONS"` and the exact `field` name matching the existing field-key vocabulary already used by `backend/services/compliance_engine.py`/`backend/services/structured_extraction.py` (e.g. `maximum_retail_price_mrp`, `country_of_origin`, ...) — "rule_id values are drawn from the existing, already-defined set... this data model does not define a new rule_id vocabulary; it reuses the existing one verbatim" (data-model.md)
- [ ] T015 For each structured Rule 24 provision (T011, if available), create the corresponding `LegalProvisionRuleLink` with `rule_id = "LMPC-R24-WHOLESALE-DECLARATIONS"` and matching field name
- [ ] T016 [P] [US1] `backend/services/test_legal_retrieval.py`: test that every one of the 8 `LMPC-R6` fields has at least one `LegalProvisionRuleLink` once T014 is complete for the fields acquired so far (a coverage-completeness check, not a retrieval-quality check)
- [ ] T017 [P] [US2] Same coverage-completeness test for the 3 `LMPC-R24` fields, tolerant of partial acquisition per the stated priority

**Checkpoint**: The corpus is now fully linked to real, existing `rule_id`s/fields — deterministic lookup is now possible in principle, though no lookup function exists yet.

---

## Phase 5: Implement Deterministic Legal-Basis Retrieval

**Purpose**: The first user-facing capability — deterministic `rule_id`(+field) → `LegalBasisResult`, no semantic search involved yet (FR-009).
**Depends on**: Phase 4.

- [ ] T018 [US1] [US2] Implement `lookup_by_rule_id(rule_id, field=None)` in `backend/services/legal_retrieval.py`: exact match against `LegalProvisionRuleLink` records loaded in Phase 3/4, returning matched `LegalProvision`(s) with embedded `LegalSourceDocument` citation, or an empty match
- [ ] T019 [US1] [US2] [US3] Implement `get_legal_basis(rule_id, field=None) -> LegalBasisResult` in `backend/services/legal_retrieval.py`: calls T018; on a match, returns `status="found"`, `retrieval_method="deterministic_link"`, `confidence=null`, populated `provisions`; on no match, returns `status="not_available"`, `provisions=[]`, `explanation=null` — "if status != found, provisions MUST be empty and explanation MUST be null — there is no code path that allows a 'not available' ... result to simultaneously carry provision text or a citation" (data-model.md, the single most important invariant)
- [ ] T020 [US1] [US2] Create `backend/api/legal_basis.py`: `GET /api/compliance/legal-basis?rule_id=...&field=...` per contracts/legal-basis-api.md, calling `get_legal_basis`, returning `422` for a `rule_id` not in the existing rule_id vocabulary, `200` with `status="not_available"` for a known rule_id with no corpus coverage (never a `5xx` for a legitimate "not available" outcome)
- [ ] T021 Register the new router in `backend/main.py`: one `from backend.api.legal_basis import router as legal_basis_router` import and one `app.include_router(legal_basis_router)` line, in the same place/style as every existing router registration (identical pattern to the `official_router` addition)
- [ ] T022 [P] [US1] `backend/api/test_legal_basis.py`: test `GET /api/compliance/legal-basis?rule_id=LMPC-R6-MANDATORY-DECLARATIONS&field=maximum_retail_price_mrp` returns `status="found"` with a non-empty `rule_sub_rule_clause`, `text`, and source citation, once T004/T005/T009/T010/T014 are complete for that field
- [ ] T023 [P] [US2] `backend/api/test_legal_basis.py`: same test shape for an `LMPC-R24` field, once its corpus coverage exists

**Checkpoint**: Deterministic retrieval works end-to-end for whatever corpus coverage exists — this is the feature's MVP. User Stories 1 and 2 are independently testable from here (per whichever rule's corpus work landed first).

---

## Phase 6: Test Correctness, Provenance, and Safety (before anything else is added)

**Purpose**: Prove the safety invariants — this phase is not optional polish, it is
required before this feature is considered done for its MVP scope, per constitution
Principle III and spec User Stories 3/5/6.
**Depends on**: Phase 5.

- [ ] T024 [US3] `backend/api/test_legal_basis.py`: `rule_id` with zero corpus coverage (e.g. `LMGEN-R12-VERIFICATION-INTERVALS`, explicitly out of required scope) → `status="not_available"`, `provisions=[]` — never an error, never a guess (spec Acceptance Scenario, User Story 3)
- [ ] T025 [US3] `backend/api/test_legal_basis.py`: malformed/unknown `rule_id` not in the existing vocabulary at all → `422`, distinct from the "known rule, no coverage" case above (contracts/legal-basis-api.md)
- [ ] T026 [Source correctness, SC-001] `backend/services/test_legal_retrieval.py`: for every `LegalProvision` in the corpus, assert its `source_id` resolves to a real `LegalSourceDocument` with non-empty `citation`/`version`/`verification_note` — a corpus-wide provenance audit, not a single-record test
- [ ] T027 [Citation correctness, SC-003] `backend/api/test_legal_basis.py`: for a sampled set of `found` results, assert the returned `rule_sub_rule_clause` matches what T004–T007's acquisition actually recorded for that field (catches a mismatched-citation bug, not just a missing one)
- [ ] T028 [Wrong-rule retrieval, SC-005] `backend/services/test_legal_retrieval.py`: assert `lookup_by_rule_id` never returns a `LegalProvision` linked to a different `rule_id` than requested — construct a corpus fixture with two different rule_ids' provisions present and confirm no cross-contamination
- [ ] T029 [US5] `backend/api/test_legal_basis.py`: record a finding's `overall_status`/`compliance_score` (via the existing `/api/compliance/evaluate` or a self-check fixture), call the legal-basis endpoint (success case and a forced "not available" case), re-check the finding — assert byte-identical status/score in both cases (spec User Story 5, the constitutional core-boundary test)
- [ ] T030 [US6] With `LEGAL_RAG_ENABLED=false` (T003's flag) or the router unregistered, run the full existing backend test suite and assert it still reports 289 passed, 1 skipped, with zero test modified — the literal regression gate spec SC-008 and User Story 6 require
- [ ] T031 [US6] Manually walk through one Manufacturer self-check and one Official case-review + decision with the legal-basis feature disabled, confirming no visible change from pre-feature behavior (spec User Story 6, Acceptance Scenarios 1–2)

**Checkpoint**: The feature's MVP (deterministic retrieval for whatever corpus coverage exists, provably safe, provably non-regressive) is complete and independently verifiable. Everything from here is an extension, not a prerequisite for calling the MVP done.

---

## Phase 7: Official & Manufacturer Display Integration [US4]

**Purpose**: Make the (already-safe, already-tested) legal basis visible where a
human actually reviews it. Deliberately sequenced after Phase 6, not before — per
spec User Story 4's own stated reasoning: "showing an unsafe or unverified result
would be worse than not showing one at all."
**Depends on**: Phase 6.

- [ ] T032 [P] [US4] Create `frontend/assets/legal-basis-client.js`: thin fetch wrapper for `GET /api/compliance/legal-basis`, mirroring `official-client.js`'s structure (own module, `credentials:'include'`, same error-handling shape)
- [ ] T033 [US4] Add a read-only "Legal Basis" section to `frontend/case-details.html`'s compliance-findings rendering: visible per-finding when available, visually distinct from the finding's own status chip and from the decision-recording controls (`log-case-action.html` remains untouched — legal basis is never part of the decision form) — FR-017, FR-018, FR-019
- [ ] T034 [P] [US4] Add the equivalent read-only "Legal Basis" section to `frontend/self-check-report.html`'s checklist rendering, additive to (never replacing) the existing explanation text — FR-021, FR-023
- [ ] T035 [US4] Confirm (manual walkthrough) that a finding with no available legal basis renders identically to today on both pages — no broken section, no error state (FR-020, FR-023)

**Checkpoint**: User Story 4 complete — an Official or Manufacturer can now see legal basis where it exists, with zero effect on findings that have none.

---

## Phase 8: Semantic Retrieval — Separately Benchmarked Capability (NOT required for MVP)

**Purpose**: Only pursued once deterministic retrieval (Phases 5–7) is complete,
tested, and in use. This phase introduces the embedding-based fallback path
research.md §1–§2 discusses, and per research.md is genuinely optional at current
corpus scale — do not start this phase merely because it appears next in this file.
**Depends on**: Phase 6 (safety proven) — Phase 7 is not a hard dependency.

- [x] T036 Confirm the trigger condition for this phase: deterministic linkage (Phase 5) has measurably failed to resolve a real query that a human reviewer expected to succeed, OR corpus coverage has grown enough that ambiguous multi-candidate matches are actually occurring — this phase is not started on a schedule, only on evidence. **DONE, NEGATIVE RESULT**: a controlled 36-query benchmark was run against the verified corpus (both models, `BAAI/bge-small-en-v1.5` and `BAAI/bge-m3`, per research.md §2). Neither trigger condition held, and the benchmark affirmatively showed semantic retrieval should NOT be adopted — see "Phase 8 Evaluation Outcome" below.
- [~] T037 Add the chosen embedding library (`FlagEmbedding` or `sentence-transformers`, per research.md §2) to `backend/requirements.txt`/`backend/constraints.txt` following the existing `--no-deps` pinning discipline — **DECLINED, not undertaken**: T036's evidence gate did not fire. The benchmark's `sentence-transformers`+`torch` dependency was installed only into an isolated, disposable benchmark venv outside this repository, never into `backend/requirements.txt`/`constraints.txt` or `backend/.venv311`.
- [~] T038 Implement offline/batch embedding of the corpus (FR-032 — never on the request path) in `backend/services/legal_retrieval.py` — **DECLINED, not undertaken** (depends on T037).
- [~] T039 Implement `semantic_fallback(query_text)` returning candidates with `confidence`, used only when T018's deterministic lookup returns no match — **DECLINED, not undertaken** (depends on T037/T038).
- [~] T040 [Groundedness/low-confidence, SC-004/FR-012] `backend/services/test_legal_retrieval.py`: assert a low-confidence semantic match is never returned as `status="found"` without its `confidence` value attached, and is withheld entirely below whatever threshold is chosen (research.md §6 — empirically tuned, not assumed) — **DECLINED, not undertaken**: there is no semantic-fallback code path for this test to cover.
- [x] T041 Run a dedicated retrieval-precision benchmark against a representative sample of query/expected-provision pairs, gated on spec SC-002 (≥90% top-result precision) before this phase is considered accepted — same benchmark-before-accept discipline as this project's existing OCR accuracy work, not a code-review-only sign-off. **DONE**: the benchmark ran (see below); both candidate models FAILED the SC-002 gate (78.1%/81.25% top-1, vs. required ≥90%) and the SC-005 wrong-rule gate (15.6% both, vs. required <5%) — this is the evidence, not an assumption, behind declining Phase 8.

### Phase 8 Evaluation Outcome (evidence-based, not schedule-based)

**Decision: semantic retrieval evaluated and DECLINED for production adoption.** Deterministic `(rule_id + field)` lookup remains the sole retrieval mechanism.

- **Benchmark**: 36 queries (11 `exact_key`, 11 `natural_language`, 14 adversarial — confusable/generic/multi/no-result) built programmatically from all 11 verified corpus provisions across `LMPC-R6`/`LMPC-R24`.
- **Deterministic baseline**: 100% top-1 on the only query shape it ever receives in production (`exact_key`); 0% wrong-rule; ~0.01ms latency. The other 25 freeform queries are structurally out of its scope (FR-009), not failures.
- **`BAAI/bge-small-en-v1.5`**: 78.1% top-1 (needed ≥90%), 15.6% wrong-rule (needed <5%), ~41ms/query.
- **`BAAI/bge-m3`**: 81.25% top-1, 15.6% wrong-rule (unchanged from bge-small — the failure is corpus-structural, not model-quality), ~469ms/query, ~2.3GB download.
- **Why it fails at this corpus size**: `LMPC-R6` and `LMPC-R24` legitimately share vocabulary across genuinely different rules ("manufacturer name/address," "net quantity") — both models confuse these near-duplicate clauses at the same rate. No similarity threshold separates confident-correct from confident-wrong: an out-of-scope query ("penalty under the Companies Act") scored higher than several genuinely correct matches scored for their own correct provision.
- **Why it adds no value even where it's most accurate**: every real caller in this system (`frontend/assets/legal-basis-client.js` → `backend/api/legal_basis.py`) already supplies an exact `rule_id`+`field` from an existing `ComplianceEngine` finding. There is no freeform-query use case in this product for semantic search to fill.
- **Constitutional relevance**: a wrong-rule/wrong-field semantic result still carries a fully-formed, correctly-cited-looking answer — from the wrong provision. This is a worse failure mode than "not available" (Principle III) and one deterministic retrieval structurally cannot produce.
- **Reconsider when**: corpus coverage grows well beyond `LMPC-R6`/`LMPC-R24` (reducing near-duplicate-clause density), or a genuine freeform-query use case is introduced — re-run this same benchmark methodology against the changed corpus/use case before deciding again, rather than assuming scale alone resolves the wrong-rule-rate problem found here.
- No dependency was added to `backend/requirements.txt`/`backend/constraints.txt`/`backend/.venv311`; the benchmark's `torch`/`sentence-transformers` install is confined to a disposable venv outside the repository.

---

## Excluded From This Task List: LLM Generation Step

**Not included, by design** — per research.md §4 and spec FR-015, a grounded
explanation is optional, and no task above produces one. Do not add a generation task
to this file without first amending plan.md/research.md with an explicit justification
(what gap the explanation fills that citation-only output does not) and re-running the
Constitution Check, per the constitution's Governance section. If ever justified, it
returns as its own new phase, after Phase 8, never before it.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [x] T042 [P] Update `backend/ocr/README.md`-style documentation: a short `backend/services/legal_retrieval.py` module docstring explaining scope and the "never a compliance decision" boundary, matching this project's existing module-docstring convention. **DONE**: the module docstring now also records the Phase 8 evaluation outcome (declined, evidence-based) and the reconsideration conditions, so the "why deterministic-only" answer lives with the code, not only in this file.
- [x] T043 Full regression pass: run the complete backend suite with the feature enabled and confirm 289 + (this feature's new tests) pass, zero pre-existing test modified. **DONE**: 324 passed, 1 skipped (289 original + 35 Legal RAG tests, all colocated per this project's convention; the baseline grew from 289 to 318 in the immediately-prior GSR 226(E) corpus-correction session and to 324 after this session's docstring-only change added no new tests — see this session's final report for the exact count history). Zero pre-existing test modified in this session.
- [x] T044 Confirm `backend/requirements.txt`/`backend/constraints.txt` changes (if Phase 8 was reached) were made via the existing `--no-deps` discipline, not a bare `pip install`. **DONE, N/A**: Phase 8 was not reached (declined per T036/T041 above) — `backend/requirements.txt`/`backend/constraints.txt` have zero changes from this feature's semantic-retrieval evaluation. The benchmark's `torch`/`sentence-transformers` dependency was installed only into a disposable venv outside this repository (`git status` confirms both files untouched).

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Legal-Source Acquisition)**: Depends on Setup. **BLOCKS everything below** — this is the actual bottleneck of the whole feature, not a formality.
- **Phase 3 (Structuring)**: Depends on whatever Phase 2 coverage exists.
- **Phase 4 (Rule/Field Mapping)**: Depends on Phase 3.
- **Phase 5 (Deterministic Retrieval)**: Depends on Phase 4. **This is the MVP boundary** — User Stories 1, 2 become testable here.
- **Phase 6 (Safety Testing)**: Depends on Phase 5. **This is the actual "done" boundary for MVP** — User Stories 3, 5, 6 are proven here.
- **Phase 7 (Display Integration, US4)**: Depends on Phase 6, not before.
- **Phase 8 (Semantic Retrieval)**: Depends on Phase 6 (safety proven); independent of Phase 7. Evidence-gated, not schedule-gated (T036).
- **Generation step**: Excluded entirely; not on this dependency graph.
- **Phase 9 (Polish)**: Depends on whichever of Phases 5–8 were actually reached.

### Parallel Opportunities

- T002, T003 (Phase 1) — different files, no dependency.
- T012, T013 (Phase 3) — implementation and its test, once T009–T011 exist for at least one field.
- T016, T017 (Phase 4) — independent coverage checks for US1 vs. US2.
- T022, T023 (Phase 5) — independent per-rule endpoint tests.
- T032, T034 (Phase 7) — different frontend files.
- Phase 8 is independent of Phase 7 entirely (may run in parallel once both depend only on Phase 6).

---

## Implementation Strategy

### MVP First

1. Phase 1 (Setup).
2. Phase 2 for Rule 6 only (T004, T005, T008) — do not wait for Rule 24.
3. Phase 3 → Phase 4 → Phase 5, scoped to Rule 6 only.
4. Phase 6 in full (safety is not scoped down, even for a one-rule MVP).
5. **STOP and VALIDATE**: User Story 1 works, is safe, and the existing suite is
   unaffected. This is a legitimate, demoable increment on its own — Rule 24, display
   integration, and semantic retrieval are all deferred past this point.

### Incremental Delivery

1. MVP (Rule 6 only, deterministic, safety-tested) → demonstrate value with zero risk to existing behavior.
2. Extend Phase 2–5 to Rule 24 → User Story 2 complete.
3. Phase 7 → User Story 4 complete (now there's something worth showing an Official).
4. Phase 8 only if T036's evidence trigger actually fires.
5. Generation step: not planned, revisited only with its own explicit justification.
