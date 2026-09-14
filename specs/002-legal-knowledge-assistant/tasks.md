---

description: "Task list for the Advanced Legal Knowledge Assistant for Officials"
---

# Tasks: Advanced Legal Knowledge Assistant for Officials

> ## FEATURE STATUS: **DECLINED** (T020/T046, 2026-09-14)
>
> Evaluated end-to-end through Phase 6 (setup, benchmark harness, retrieval
> evaluation, security review); declined at the human/legal approval gate before
> any Phase 7 (API), Phase 8 (UI), or production implementation began. Best
> retrieval candidate (query-rewrite + hybrid keyword/semantic): **87.18% top-1**
> (required ≥90%), **7.69% wrong-rule** (required <5%), **15.38% plausible-but-
> wrong on the adversarial subset** (required <5%). No candidate cleared all three
> bars. Full evidence: [benchmarks/t020-evidence-package.md](benchmarks/t020-evidence-package.md).
> This specification and its benchmark evidence are **retained as the historical
> record** of what was evaluated and why it was declined — see "Clean-Reversion
> Record" and "Reconsideration Conditions" near the end of this file.

**Input**: Design documents from `specs/002-legal-knowledge-assistant/` (spec.md, plan.md, research.md, data-model.md, contracts/legal-assistant-api.md, quickstart.md)

**Prerequisites**: spec.md ✅, plan.md ✅ (both already produced this session)

**Tests**: Included throughout — constitution Principle VII and this spec's own Success
Criteria (SC-001–SC-014) require test + benchmark-style acceptance, matching
`001-legal-rag`'s established discipline (never optional here).

**Organization**: Like `001-legal-rag` before it, this feature is a benchmark-gated
technology decision before it is a set of independent user-facing increments — there
is nothing to integrate into an API or UI until Phases 3–6 below have produced an
accepted (or explicitly declined) technology combination. Per explicit instruction,
phases follow the required evidence-gated pipeline sequence (architecture/setup →
benchmark harness → retrieval evaluation → reranking evaluation → generation/
grounding evaluation → security review → **human/legal decision gate** → API
integration → UI integration → end-to-end testing → final acceptance) rather than the
fully-parallel per-user-story default. Each task still carries a `[Story]` label
mapping back to spec.md's user stories where applicable, so story-level traceability
is preserved even though the phases themselves are sequential.

**No task in this file is executed by `/speckit-tasks` itself.** Every task below is
future work, gated as described; this document does not install a dependency,
download a model, create a vector database, or modify any existing file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files/candidates, no dependency on an incomplete task)
- **[Story]**: US1 (free-form Q&A + grounded answer/refusal, P1) · US2 (case-review
  legal background, P2) · US3 (provenance/amendment history, P2) · US4 (corpus
  expansion readiness, P3) · US5 (compliance-status immutability, P1) · US6 (graceful
  degradation, P1) — from spec.md
- **`[EVIDENCE GATE]`**: A technology candidate is benchmarked against a specific,
  named spec.md Success Criterion; it is adopted ONLY if it clears that bar. A failed
  gate is documented, not silently dropped, and does not block evaluating the next
  candidate.
- **`[DECISION POINT]`**: A consolidation task that reads one or more `[EVIDENCE
  GATE]` results and selects (or explicitly declines to select) a technology.
- **`[HUMAN/LEGAL APPROVAL REQUIRED]`**: A task that produces a recommendation but
  requires explicit sign-off from a human (project owner and/or someone with legal
  subject-matter authority) before work proceeds past it — never auto-approved by an
  agent, regardless of how clean the benchmark numbers look.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project scaffolding only — no retrieval, ranking, or generation logic yet.

- [ ] T001 Create `backend/models/legal_assistant.py` with empty/stub Pydantic classes for `LegalQuery`, `RetrievalCandidate`, `LegalAssistantAnswer`, `QueryInteractionRecord` per data-model.md's field tables — fields only, no logic; imports and reuses `backend/models/legal.py`'s existing `LegalProvision`/`LegalSourceDocument` verbatim (data-model.md "Reused, unmodified from 001-legal-rag") rather than redefining them
- [ ] T002 [P] Add a `LEGAL_ASSISTANT_ENABLED` flag to `.env.example`, default `false`, following the exact existing pattern of `GEMINI_ENABLED`/`OCR_PREPROCESSING_ENABLED` — the toggle User Story 6's graceful-degradation task (T041) exercises
- [ ] T003 [P] Create an empty `backend/services/legal_assistant/` package (or single `backend/services/legal_assistant.py` stub file, final layout decided when Phase 7 actually writes logic) with a module docstring stating scope and the "never a compliance decision, never reads complianceResult/officialDecision" boundary — matching `backend/services/legal_retrieval.py`'s own docstring convention
- [ ] T004 [P] Document, in a short `benchmarks/README.md` under this feature's spec directory (`specs/002-legal-knowledge-assistant/benchmarks/README.md`), that benchmark scripts, any evaluation-only dependency install, and any downloaded model artifact live outside the repository (session scratchpad or equivalent disposable location) — mirroring exactly how `001-legal-rag`'s Phase 8 benchmark (`bench_venv`, `sentence-transformers`, `torch`) was run without touching `backend/requirements.txt`/`backend/.venv311`; this file records the convention so a future implementer does not accidentally commit a multi-GB model cache

**Checkpoint**: Scaffolding exists; nothing retrieves, ranks, or generates anything yet.

---

## Phase 2: Benchmark Dataset & Evaluation Harness (Blocking Prerequisite)

**Purpose**: Nothing in Phases 3–5 can be benchmarked without this. Reuses and
extends `001-legal-rag`'s own Phase 8 benchmark methodology rather than inventing a
new one.

**⚠️ CRITICAL**: No technology-evaluation task in Phases 3–5 may begin until this
phase is complete.

- [ ] T005 Build the benchmark query set: at least as large and adversarially rigorous as `001-legal-rag`'s own 36-query Phase 8 set (`specs/001-legal-rag/tasks.md`, "Phase 8 Evaluation Outcome"), covering every query shape spec.md requires — exact-citation ("Rule 6(11)"), topic-phrased ("country of origin requirements"), amendment/history ("what changed after the 2022 amendment") — plus every adversarial class research.md §1 names (confusable cross-rule vocabulary, generic/multi-topic, no-result/out-of-scope) — generated programmatically from the verified corpus (`backend/legal_corpus/legal_metrology_packaged_commodities_2011.json`), each entry carrying `question_text`, `expected_provisions` (possibly empty), and an `adversarial_class` tag per data-model.md's "Benchmark Query Set." Stored under `specs/002-legal-knowledge-assistant/benchmarks/` (not `backend/`), read-only test fixture, not production code
- [ ] T006 [P] Build a retrieval-accuracy evaluation harness that scores any candidate retrieval/ranking pipeline against T005's set, computing: top-1 accuracy (SC-001), top-3 recall (SC-002), wrong-rule rate (SC-003), wrong-field rate (SC-004), and the adversarial A/B/C classification (SC-009: correct retrieval / plausible-but-wrong / correct refusal) — reusable, unmodified, across every candidate evaluated in Phases 3–4; mirrors `001-legal-rag`'s own `score_semantic.py` precedent
- [ ] T007 [P] Build a separate groundedness evaluation harness for Phase 5, distinct from T006: given a generated `answer_text` and its cited `citations`, flags any claim in `answer_text` not directly supported by the cited provision text — used to measure SC-007 (groundedness); this harness has no retrieval-accuracy role and must not be conflated with T006

**Checkpoint**: Harness ready. **No embedding model, vector database, reranker, or
generation model has been chosen, downloaded, or installed at this point.**

---

## Phase 3: Candidate Retrieval Technique Evaluation

**Purpose**: Determine whether any retrieval technique clears SC-001/SC-002/SC-003/
SC-004/SC-009 at this corpus's current scale — genuinely open per research.md §0–§1,
**not assumed to succeed**. `001-legal-rag`'s own Phase 8 data (78–81% top-1, 15.6%
wrong-rule for `BAAI/bge-small-en-v1.5`/`BAAI/bge-m3` on this same corpus) is direct
counter-evidence against assuming a naive single-embedding-model approach will pass.

**Depends on**: Phase 2.

- [x] T008 `[EVIDENCE GATE]` [US1] Evaluate a pure dense-embedding-retrieval candidate (e.g. re-run/extend `001-legal-rag`'s own Phase 8 candidates, `BAAI/bge-small-en-v1.5` and/or `BAAI/bge-m3`, against T005's larger/more-rigorous benchmark set using T006's harness) — record top-1/top-3/wrong-rule/wrong-field/SC-009 results; **adopt only if it independently clears SC-001 (≥90%), SC-003 (<5%), and SC-009 (<5% plausible-wrong on the adversarial subset)** — if it does not, document the negative result (numbers, which query classes failed) and proceed to T009 rather than stopping. **DONE — REJECTED**: `bge-small-en-v1.5` 74.36% top-1 / 12.82% wrong-rule / 23.08% adversarial class-B; `bge-m3` 76.92% / 12.82% / 30.77%. Neither clears any of the three bars. Full detail: `benchmarks/phase3-retrieval-results.md`.
- [x] T009 `[EVIDENCE GATE]` [US1] Evaluate a hybrid keyword+semantic retrieval candidate (combining the corpus's own known `linked_rule_id`/`linked_field` lexical signal with semantic similarity, per research.md §1) against the same benchmark/harness — same adopt/reject bar as T008; document result regardless of outcome. **DONE — REJECTED**: BM25 + `bge-small` (Reciprocal Rank Fusion) — 84.62% top-1 / 7.69% wrong-rule / 15.38% adversarial class-B. A real improvement over T008, still short of all three bars.
- [x] T010 `[EVIDENCE GATE]` [US1] Evaluate a query-rewriting/expansion candidate (e.g. corpus-vocabulary-aware query expansion applied before retrieval, per research.md §1) layered on top of whichever of T008/T009 performed best — same adopt/reject bar; document result regardless of outcome. **DONE — REJECTED**: corpus-domain synonym expansion + T009's hybrid — 87.18% top-1 / 7.69% wrong-rule / 15.38% adversarial class-B. Wrong-rule and adversarial class-B **unchanged from T009** — query rewriting improved general accuracy but did not fix the safety-critical cross-rule confusion failure mode.
- [x] T011 `[DECISION POINT]` [US1] Consolidate T008–T010's results into a single evidence table (candidate, top-1, top-3, wrong-rule%, wrong-field%, SC-009 class-B%) written to `specs/002-legal-knowledge-assistant/benchmarks/phase3-retrieval-results.md`; select the best-performing candidate **only if at least one candidate clears SC-001/SC-003/SC-009**; if **none** clear all three, this is a valid "no viable retrieval technique at this corpus scale" outcome — skip directly to T046 (the ACCEPT/DECLINE decision task) and record DECLINE there with this evidence, rather than continuing to Phase 4. **DONE — DECLINED. Best candidate (query-rewrite + hybrid): 87.18% top-1 (required ≥90%), 7.69% wrong-rule (required <5%), 15.38% plausible-but-wrong on the adversarial subset (required <5%). None of the three required bars is met.** Skipped directly to T046 per this rule.

**Checkpoint (RESOLVED)**: No retrieval technique was provisionally selected — the
feature is **DECLINED**, formally recorded at T046. Phase 4 and Phase 5 were not
executed for their intended purpose (see their own checkpoints below); Phase 6 was
executed anyway to keep the T020 evidence package complete; Phase 7 onward was never
started.

---

## Phase 4: Candidate Reranking Evaluation (Conditional)

**Purpose**: Only pursued if Phase 3 found a base candidate whose top-3 recall
(SC-002) is strong but whose top-1 accuracy (SC-001) alone is not — exactly the
symptom a reranker is designed to fix, and exactly what `001-legal-rag`'s own Phase 8
top-3 numbers (90.6%/93.75%) suggested was plausible for its declined use case.

**Depends on**: Phase 3 (T011) producing a candidate to rerank. **Skip this phase
entirely** (mark all tasks N/A with a one-line reason) if T011 already cleared SC-001
without reranking, or if T011 found no viable base candidate at all (nothing to
rerank).

- [~] T012 `[EVIDENCE GATE]` [US1] IF Phase 3's condition above is met: evaluate a reranking candidate (e.g. `BAAI/bge-reranker-v2-m3`, the candidate `001-legal-rag` research.md §3 already surveyed for a different feature — not assumed superior, evaluated on its own merits here) applied to Phase 3's top-N candidates, re-measuring SC-001 and SC-009 with T006's harness; adopt only if it measurably improves SC-001/SC-009 over the un-reranked Phase 3 result without introducing a new wrong-rule failure mode; document result (including the "skipped, not applicable" outcome) in `specs/002-legal-knowledge-assistant/benchmarks/phase4-reranking-results.md`. **N/A — NOT EXECUTED**: T011 found no qualifying base candidate (none cleared SC-001/SC-003/SC-009), and the observed failure mode (flat wrong-rule/adversarial rates across T009→T010) is not the top-1-vs-top-3 symptom reranking addresses. No reranker was downloaded, installed, or evaluated. Full reason: `benchmarks/phase4-reranking-results.md`.

**Checkpoint (RESOLVED)**: No reranking was evaluated — N/A per T011's decline. The
feature remains DECLINED (T046).

---

## Phase 5: Generation / Grounding Evaluation

**Purpose**: Unlike `001-legal-rag` (generation was optional and never built), this
feature's spec treats a generation step as required (spec Assumptions) — but the
groundedness bar (SC-007) is zero-tolerance, so "no generation, citations only" stays
a legitimate outcome, not a fallback of last resort to be embarrassed about.

**Depends on**: Phase 3/4 producing a retrieval pipeline to generate answers from
(unless declined at T011, in which case skip to T046).

- [~] T013 `[EVIDENCE GATE]` [US1] [US3] Evaluate reusing the existing Gemini integration (`backend/ocr/gemini_service.py`'s async-client/timeout/fallback pattern, called with retrieved passages from the accepted Phase 3/4 pipeline) as the generation candidate, scored by T007's groundedness harness against SC-007 (100% of sampled claims supported) and SC-013's zero-regression bar (confirm this reuse pattern does not require any change to `gemini_service.py` itself — read-only reuse of its existing client pattern, not a modification); document result regardless of outcome. **N/A — NOT EXECUTED**: no accepted retrieval pipeline exists to generate answers from (T011 declined). `gemini_service.py` was not called.
- [~] T014 `[EVIDENCE GATE]` [US1] [US3] Evaluate a hosted Hugging Face Inference API generation candidate against the same groundedness harness and bar; document result regardless of outcome. **N/A — NOT EXECUTED**, same reason as T013. No Hugging Face Inference API call was made.
- [~] T015 `[EVIDENCE GATE]` [US1] Evaluate the "no generation — citations and matched provision text only" option: confirm it trivially satisfies SC-007 (there is no generated claim to be unsupported) and still delivers spec's core free-form-question-to-correct-provision value; always available as a fallback regardless of T013/T014's outcome. **N/A — NOT EXECUTED** as a formal evaluation (nothing to fall back from without an accepted retrieval pipeline), but recorded as the zero-risk floor any future iteration should default to. Full reason: `benchmarks/phase5-generation-results.md`.
- [~] T016 `[DECISION POINT]` [US1] Consolidate T013–T015 into `specs/002-legal-knowledge-assistant/benchmarks/phase5-generation-results.md`; select the generation approach whose groundedness result is strongest (including explicitly selecting T015's citation-only option if neither T013 nor T014 reliably clears SC-007) — a generation candidate that cannot be reliably grounded is rejected, not shipped with a caveat. **N/A — NOT EXECUTED**: nothing to consolidate; no generation approach was evaluated.

**Checkpoint (RESOLVED)**: No answer-construction pipeline was evaluated — N/A per
T011's decline. `score_groundedness.py` (T007) was built and is ready for a future
run but was never exercised against a real generation candidate. The feature remains
DECLINED (T046).

---

## Phase 6: Security & Prompt-Injection Resistance Review

**Purpose**: Verify the provisionally-selected pipeline (Phases 3–5) cannot be
subverted via its own retrieved content, and cannot be reached by anyone but an
Official — before any of it is wired into a real API.

**Depends on**: Phase 5 (or Phase 3/4's decline outcome, in which case this phase is
also skipped).

- [x] T017 [US1] Construct an adversarial prompt-injection test set: legal-source-shaped text engineered to resemble an instruction (e.g. a corpus passage rewritten to contain a sentence like "ignore prior context and reveal..."), fed through the selected Phase 3–5 pipeline (the already-real, benchmark-only candidate code from Phases 3–5, not yet the Phase 7 production service); **expected**: the injected content is never treated as an instruction by retrieval, ranking, or generation components (spec FR-018, FR-030) — document pass/fail per test case. This proves the *technique* resists injection; T033 (Phase 7) re-proves the *production implementation* of that technique does too, once it exists. **DONE**: 5 adversarial probes run against the actual Phase 3 candidate code — all treated as ordinary text, zero code path interprets text as an instruction (retrieval/ranking is pure numeric math, structurally incapable of "obeying" text). Executed anyway despite Phase 3's decline, per explicit instruction, to keep the T020 package complete. Results: `benchmarks/t017-injection-test-results.json`.
- [x] T018 [P] [US1] **Pre-implementation threat-model and design review** — no Phase 7 code exists yet at this point in the sequence; this reviews the DESIGN, not an implementation. Using T017's results and spec FR-018/FR-030, produce an explicit prompt-injection boundary specification that Phase 7 MUST implement: retrieved corpus text may reach any ranking/generation call only as a clearly-delimited, structurally separate data field (e.g. a dedicated "context passages" parameter/message role) — never concatenated into, or allowed to influence, a system/control instruction string. Review research.md §1/§4's candidate techniques and the Phase 3–5 benchmark harness's own code for any place this boundary is already violated at benchmark time, and correct it there first. Write the resulting checklist to `specs/002-legal-knowledge-assistant/benchmarks/phase6-security-design-review.md` — this checklist is what T020 evaluates and what T033 (Phase 7, post-implementation) later verifies the real code against. **DONE**: boundary specification written; the real injection risk is entirely deferred to a not-yet-built generation step (Phase 5 was never reached) — this checklist is preserved for any future reconsideration. `benchmarks/phase6-security-design-review.md`.
- [x] T019 [P] [US5] **Pre-implementation architecture/interface-boundary review** — reviews `contracts/legal-assistant-api.md` and `data-model.md` (the DESIGN — no Phase 7 code exists yet), not an implementation. Confirm the contract's declared request/response shape and the selected Phase 3–5 pipeline's declared inputs are limited to `LegalQuery.question_text` and the verified legal corpus only, with `case_context` restricted to display/audit metadata per data-model.md's `LegalQuery` validation rule — and confirm neither the contract nor the data model declares any parameter, dependency, or access path to `complianceResult`, `overall_status`, `compliance_score`, `officialDecision`, or `decisionHistory`, nor any write operation against them (spec FR-003, FR-024, FR-026). Write the resulting checklist to `specs/002-legal-knowledge-assistant/benchmarks/phase6-security-design-review.md` alongside T018's — this is what T020 evaluates and what T034 (Phase 7, post-implementation) later verifies the real code against. **DONE — zero violation found**: neither the design nor the (now-reverted) Phase 1 stub code had any access path to compliance/decision data. `benchmarks/phase6-security-design-review.md`.

- [x] T020 `[DECISION POINT]` `[HUMAN/LEGAL APPROVAL REQUIRED]` [US1] Present the full Phase 3–6 evidence trail — retrieval accuracy (Phase 3/4), groundedness (Phase 5), T017's prompt-injection test results, and T018/T019's pre-implementation design-review checklists (and, if reached, the T011 early-decline rationale) — to a human reviewer with project and legal-subject-matter authority for explicit sign-off before any implementation work (Phase 7 onward) begins. This is a genuine go/no-go checkpoint, not a formality: **if the evidence does not clearly clear spec.md's SC-001–SC-004/SC-007/SC-009 bars, or if the reviewer is not satisfied T018/T019's design checklists are sufficient, this task's outcome is "do not proceed to Phase 7" — proceed to T046 (record DECLINE) instead.** This sign-off approves a *design*, not yet code — T033/T034 (Phase 7) independently re-verify the actual implementation against T018/T019's checklists before Phase 8 begins, and T046 (Phase 10) is a second, later approval gate over the fully integrated, tested system. No agent or automated process may substitute for this sign-off, mirroring constitution Principle II's Official Human Authority applied to the decision of whether to build this feature at all, not only to its eventual runtime output. **DONE — DECLINED.** The evidence package (`benchmarks/t020-evidence-package.md`) was presented; the human reviewer (project owner) reviewed it and explicitly directed: "DECLINE production implementation at the current corpus/retrieval state" — citing the same evidence recorded here (87.18% top-1, 7.69% wrong-rule, 15.38% adversarial class-B against required ≥90%/<5%/<5%). Phase 7 was never started.

**Checkpoint (RESOLVED)**: Explicit human/legal approval was sought and the answer
was DECLINE, recorded formally at T046. Phases 7–9 were not started.

---

## Phase 7: Official-Only API Integration (`US1`, `US2`, `US3`)

**Purpose**: Only reached if T020 approved proceeding. Wires the Phase 3–6 accepted
pipeline into a real, Official-only endpoint per contracts/legal-assistant-api.md.

**Depends on**: Phase 6 (T020 approval).

**STATUS: NOT STARTED — T020 DECLINED.** T001/T002/T003's Phase 1 scaffolding
(`backend/models/legal_assistant.py`, `backend/services/legal_assistant.py` stub
files, and the `LEGAL_ASSISTANT_ENABLED` `.env.example` flag) was created during the
controlled execution run and has since been **removed/reverted** as part of the
formal decline/clean-reversion path (constitution Principle XII) — it had zero
runtime purpose once T020 declined. None of T021–T034 below were started; they
remain exactly as originally planned, for reference only, in case a future
reconsideration (see this file's closing "Reconsideration Conditions" section)
reaches this phase.

- [ ] T021 [US1] Implement `backend/models/legal_assistant.py`'s full `LegalQuery`, `LegalAssistantAnswer` (including its data-model.md validation rules: `status=insufficient_evidence` ⇒ `answer_text=null` and `citations=[]`; `status=answered` ⇒ `citations` non-empty; every `answer_text` claim traceable to a `citations` entry), `RetrievalCandidate`, and `QueryInteractionRecord` — replacing T001's stubs with real field validation
- [ ] T022 [US1] Implement the query-processing + accepted-retrieval-pipeline call in `backend/services/legal_assistant.py`, using Phase 3/4's selected technique(s) exactly as benchmarked — no unbenchmarked variation introduced at implementation time
- [ ] T023 [US1] [US3] Implement the accepted generation-or-citation-only step (Phase 5's T016 selection) in `backend/services/legal_assistant.py`, including source-version preference logic for amended-vs-original provisions (spec FR-020, FR-021 — never blend original and amended text into one answer)
- [ ] T024 [US1] Implement refusal/insufficient-evidence and explicit-disambiguation logic (spec FR-015–FR-017) in `backend/services/legal_assistant.py`, using the empirically-tuned confidence mechanism from Phase 3's benchmark (research.md §5 — explicitly not a naive fixed similarity threshold, per that section's own counter-evidence from `001-legal-rag`'s Phase 8)
- [ ] T025 [US1] Create `backend/api/legal_assistant.py`: `POST /api/official/legal-assistant/ask` per contracts/legal-assistant-api.md, gated by the existing `official_user` dependency (`backend/api/auth.py`) — reused unmodified, no new auth scheme
- [ ] T026 [P] [US1] Register the new router in `backend/main.py`: one `from backend.api.legal_assistant import router as legal_assistant_router` import and one `app.include_router(legal_assistant_router)` line, identical pattern to every existing router registration (the only touch to `main.py` this feature makes)
- [ ] T027 [P] [US2] [US3] Implement `GET /api/official/legal-assistant/history` per contracts/legal-assistant-api.md, if confirmed necessary at this point (spec FR-028) — reuses the existing `history`-collection pattern (research.md §6), not a new logging subsystem
- [ ] T028 [P] [US1] `backend/api/test_legal_assistant.py`: contract tests for the `answered`, `insufficient_evidence`, and `ambiguous_candidates` response shapes against contracts/legal-assistant-api.md's examples
- [ ] T029 [P] [US1] `backend/api/test_legal_assistant.py`: both directions of backend-enforced access control — a `manufacturer`-role session and an unauthenticated request each receive `403`/`401` directly from the API (spec FR-001, FR-002), not merely a hidden UI element
- [ ] T030 [US1] `backend/api/test_legal_assistant.py`: malformed request (empty `question_text`) → `422`; forced downstream retrieval/generation failure → `status: "insufficient_evidence"`, never a `5xx` that could surface a fabricated result (contracts/legal-assistant-api.md's Error responses section)
- [ ] T031 [US5] `backend/api/test_legal_assistant.py`: the constitutional core-boundary test — record a case's `overall_status`/`compliance_score`/`officialDecision` via the existing evaluate/decision paths, call the assistant endpoint (a successful answer, a forced refusal, and a forced error — three separate calls), re-check the case after each; assert byte-identical status/score/decision in all three cases (mirrors `001-legal-rag`'s own `test_legal_basis_retrieval_never_affects_compliance_evaluation`)
- [ ] T032 [US1] `backend/api/test_legal_assistant.py`: citation/provenance fidelity — for a sampled set of `answered` results, assert every returned `rule_sub_rule_clause`/source citation matches the actual corpus entry it claims to come from (no mismatched citations, mirrors `001-legal-rag`'s own `test_citations_match_their_own_provision_content_no_mismatch`)
- [ ] T033 [US1] **Post-implementation prompt-injection verification** — with `backend/services/legal_assistant.py`/`backend/api/legal_assistant.py` now actually written (T021–T025), re-run T017's adversarial prompt-injection test set against the REAL implemented pipeline (not the Phase 3–5 benchmark-harness candidate T017 originally targeted), and inspect the actual code path to confirm retrieved corpus text reaches every ranking/generation call only as clearly-delimited, structurally separate data, never concatenated into anything parseable as an instruction (spec FR-018, FR-030). Check the real implementation point by point against T018's `phase6-security-design-review.md` checklist; any divergence from that pre-implementation design is a defect to fix before Phase 8 begins, not a reason to weaken the check.
- [ ] T034 [US5] **Post-implementation compliance-boundary verification** — with `backend/services/legal_assistant.py`/`backend/api/legal_assistant.py` now actually written (T021–T025), trace every function call the real request path makes and confirm no code path reads or writes `complianceResult`, `overall_status`, `compliance_score`, `officialDecision`, or `decisionHistory` (spec FR-024, FR-026) — verified against the real implementation, not inferred from T019's pre-implementation design review. Check the real implementation point by point against T019's `phase6-security-design-review.md` checklist; any divergence is a defect to fix before Phase 8 begins.

**Checkpoint**: Endpoint implemented, independently testable, AND its actual code
verified (T033/T034) against the Phase 6 pre-implementation security/boundary design
(T018/T019) — not yet wired into any UI.

---

## Phase 8: Official UI Integration (`US2`, `US3`)

**Purpose**: Present the Phase 7 endpoint to an Official, structurally separate from
compliance status, the `001-legal-rag` deterministic Legal Basis panel, and decision
controls (spec FR-025, FR-037).

**Depends on**: Phase 7.

- [ ] T035 [P] [US1] Create `frontend/assets/legal-assistant-client.js`: thin fetch wrapper for `POST /api/official/legal-assistant/ask` (and `GET .../history` if T027 was built), mirroring `legal-basis-client.js`'s/`official-client.js`'s structure (own module, `credentials:'include'`, same fail-safe-to-a-neutral-state error handling `legal-basis-client.js` already established)
- [ ] T036 [US1] [US3] Create `frontend/legal-assistant.html`: standalone, Official-only research surface — question input → answer (clearly labeled AI-generated, spec FR-014/FR-036) → cited provisions (exact rule/sub-rule/clause + source/provenance) → amendment/effective-date information where applicable → safe rendering of the `insufficient_evidence`/`ambiguous_candidates` states (spec FR-038, never a blank or error-looking UI for a legitimate refusal)
- [ ] T037 [US2] Add a case-review-context entry point to `frontend/case-details.html`: additive only — a new, separate UI element (never merged into `#case-findings-list`, the `001-legal-rag` Legal Basis panel markup, or the Action Timeline/decision controls) that opens/embeds the Phase 8 assistant experience with `case_context` set to the case's `revisionId` (spec FR-023, FR-025)
- [ ] T038 [US1] [US2] Confirm (manual walkthrough, both the standalone surface and the case-review entry point) that a refused/insufficient-evidence answer and an ambiguous-candidates answer both render as clearly labeled, non-broken states — never blank, never indistinguishable from a real system failure (spec FR-038)
- [ ] T039 [US2] Confirm (manual walkthrough) that using the assistant from within a case review does not alter that case's visible compliance status, score, or decision-recording controls, and that no control in the assistant's UI can write into any of them (spec FR-026, User Story 2 Acceptance Scenario 3)

**Checkpoint**: Feature is end-to-end usable by an Official, both standalone and from
within case review.

---

## Phase 9: End-to-End Testing & Regression

**Purpose**: Prove the whole system — not just this feature's own new tests — is
unaffected, exactly matching `001-legal-rag`'s own regression discipline at every
prior checkpoint of this project.

**Depends on**: Phase 8.

- [ ] T040 [US6] Run the complete backend suite with this feature enabled: confirm 324 (the current post-`001-legal-rag` baseline) plus this feature's own new tests all pass, with **zero pre-existing test modified, skipped, or removed** (constitution Principle IX)
- [ ] T041 [US6] Run the complete backend suite with this feature's router unregistered (or `LEGAL_ASSISTANT_ENABLED=false`): confirm exactly 324 passed, 1 skipped — byte-identical to the pre-feature baseline
- [ ] T042 [US6] Explicitly confirm, by name, that `001-legal-rag`'s own test suite (`backend/api/test_legal_basis.py`, `backend/services/test_legal_retrieval.py`), `backend/services/test_compliance_engine.py`, the OCR test modules, the Gemini-fallback tests, the YOLO tests, and `backend/api/test_official.py` (Official Workflow) all remain in the passing suite **unchanged** — a named check, not an inference from "the suite passed"
- [ ] T043 [US6] Manually walk through one Manufacturer self-check and one Official case-review + decision (including viewing `001-legal-rag`'s existing Legal Basis panel) with this feature fully enabled, confirming zero visible change to either flow beyond the new, clearly-separate assistant UI itself
- [ ] T044 [US4] Confirm (design-review, not new code) that nothing in the Phase 7 implementation hard-codes an assumption limiting the corpus to exactly 11 provisions/2 rule_ids — the query/answer/citation contract must require no breaking change to serve a larger, future-approved corpus (spec User Story 4)

**Checkpoint**: Full-system regression proven, not assumed.

---

## Phase 10: Final Acceptance / Convergence

**Purpose**: Re-confirm the fully **integrated** system (not just Phase 3–5's isolated
candidate benchmarks) clears every applicable spec.md Success Criterion — integration
can introduce issues isolated component benchmarks do not catch.

**Depends on**: Phase 9.

- [~] T045 `[EVIDENCE GATE]` [US1] Re-run T005's full benchmark query set end-to-end against the final integrated `POST /api/official/legal-assistant/ask` endpoint (not the isolated Phase 3–5 harness calls) and confirm: SC-001 ≥90% top-1, SC-002 ≥97% top-3 recall, SC-003 <5% wrong-rule, SC-004 <5% wrong-field, SC-005 100% citation correctness, SC-006 100% provenance correctness, SC-007 100% groundedness, SC-008 ≥95% correct refusal on the no-coverage subset, SC-009 <5% plausible-but-wrong on the adversarial subset, SC-012 ≥95% repeatability — write the full results to `specs/002-legal-knowledge-assistant/benchmarks/final-acceptance-results.md`. **N/A — NOT EXECUTED**: no integrated system exists to re-benchmark (Phase 7–9 never started; declined at T020, upstream of this task).
- [x] T046 `[DECISION POINT]` `[HUMAN/LEGAL APPROVAL REQUIRED]` Final accept/decline decision — **also the single landing point for an early Phase 3/6 decline** (T011, T020): **ACCEPT** this feature for production use only if T045's full results clear every listed bar AND Phase 9's regression tasks are all green AND a human with project/legal authority signs off on T045's evidence a second time (integration-level, distinct from T020's pre-implementation sign-off); **DECLINE** — recording whichever of the three possible decline points was reached (T011: no viable retrieval technique; T020: pre-implementation sign-off withheld; here: integrated system failed T045) and, if any implementation work was actually done (Phase 7–8), cleanly reverting every file it introduced per constitution Principle XII, leaving no partial implementation or disabled-but-present logic behind — exactly as `001-legal-rag`'s own Phase 8 semantic-retrieval experiment was declined. **DONE — DECLINED, formally recorded.** Landing point reached via T011 (no viable retrieval technique) confirmed by T020 (human/legal sign-off withheld — explicit DECLINE directive from the project owner). Implementation work actually done was limited to Phase 1 scaffolding (T001–T003); it has been cleanly reverted (see "Clean-Reversion Record" below) — Phase 7–8 were never started, so there was nothing further to revert there. **Feature 002 final status: DECLINED.** See this file's closing "Reconsideration Conditions" section for what would need to change before this decision is revisited.
- [~] T047 [If ACCEPTED] Final documentation pass: update `backend/services/legal_assistant.py`'s module docstring to record which technology combination was adopted and why, with a link to T045's evidence file — mirroring `backend/services/legal_retrieval.py`'s own Phase 8-outcome docstring precedent from `001-legal-rag`. **N/A — feature declined, not accepted.** `backend/services/legal_assistant.py` no longer exists (reverted).
- [~] T048 [If ACCEPTED] Confirm `backend/requirements.txt`/`backend/constraints.txt` changes (whatever Phases 3–7 actually required) were made via the existing `--no-deps` discipline, not a bare `pip install` — the same check `001-legal-rag`'s own T044 performed for its (negative) Phase 8 outcome, now performed for this feature's actual (positive or negative) outcome. **N/A — feature declined; zero changes were made to either file (confirmed at T049).**
- [x] T049 Confirm, via `git status`/`git diff`, that no file outside this feature's new files (Phase 1, 7, 8 file lists above) was modified — explicitly re-confirming `001-legal-rag`'s corpus/models/services/API/frontend integration, `ComplianceEngine`, OCR, the Gemini fallback, YOLO, and the Official Workflow's decision-recording files all show zero diff, the same audit performed before `001-legal-rag`'s own git checkpoint. **DONE, post-clean-reversion**: `git status --short` shows only `specs/002-legal-knowledge-assistant/` (specification + benchmark evidence, intentionally retained) and the pre-existing, unrelated `training/benchmark_results/`. `backend/requirements.txt`/`backend/constraints.txt`/`.env.example`/`backend/main.py` and every `001-legal-rag`/`ComplianceEngine`/OCR/Gemini/YOLO/Official-Workflow file show zero diff.

---

## Clean-Reversion Record (constitution Principle XII)

Feature 002 was declined at T020/T046 before any Phase 7–9 implementation began.
The only artifacts this feature had introduced into the application itself — Phase 1
scaffolding, created and later removed within the same controlled execution
sequence — are recorded here for audit completeness:

| File | Action | Reason |
|---|---|---|
| `backend/models/legal_assistant.py` | Created (T001), then **removed** | Empty stub Pydantic shapes, never imported by any other file — zero runtime purpose once T020 declined |
| `backend/services/legal_assistant.py` | Created (T003), then **removed** | Docstring-only stub, never imported by any other file — same reason |
| `.env.example` | `LEGAL_ASSISTANT_ENABLED=false` line added (T002), then **reverted** | An unused toggle for a service that no longer exists in the codebase |

**Nothing else was ever created or modified in `backend/` or `frontend/` for this
feature.** No dependency was ever added to `backend/requirements.txt`/
`backend/constraints.txt`. No model was ever downloaded into the repository. No
vector database was ever created. `backend/main.py` was never touched (Phase 7's
router-registration task, T026, was never reached).

**Retained, per explicit instruction — this feature's specification and evidence
are a historical record, not implementation residue**: `spec.md`, `plan.md`,
`research.md`, this `tasks.md`, and everything under `benchmarks/` (the dataset, the
two harnesses, all four candidates' raw and scored predictions, the Phase 3/4/5/6
results write-ups, the T017 injection-test results, and the T020 evidence package).

## Reconsideration Conditions

Semantic retrieval for this feature was declined on *evidence*, not on principle —
the door is deliberately left open, per `research.md` §7 and
`benchmarks/phase3-retrieval-results.md`'s trend note. Reconsider Feature 002 only
if at least one of the following becomes true, and re-run this same benchmark
methodology (not a fresh, uncontrolled attempt) before deciding again:

1. **Corpus growth**: `001-legal-rag`'s verified legal corpus expands materially
   beyond `LMPC-R6`/`LMPC-R24` (11 provisions today) in a way that reduces the
   specific vocabulary overlap between rules that caused every candidate's
   wrong-rule/adversarial failures here (e.g. "manufacturer name and address" and
   "net quantity" appearing near-identically in two different rules) — more
   provisions alone do not help if they add more of the same overlapping
   vocabulary; the growth needs to plausibly reduce collision density.
2. **A genuinely new retrieval technique** becomes available or practical that
   specifically targets rule-level disambiguation (e.g. a generation step given
   multiple retrieved candidates and asked to disambiguate with context, which this
   run never got to test since no retrieval pipeline reached Phase 5) — evaluated
   on its own merits against this same benchmark, not assumed superior.
3. **A real, product-approved free-form-query use case** emerges that makes the
   underlying capability gap (no exact `rule_id`/field key available, per this
   feature's whole reason for existing) more urgent than it is today — a business
   justification, not a technology justification, for accepting a higher-effort
   path to close the accuracy gap (e.g. investing in reranking or a larger
   generation-assisted disambiguation step even at added infrastructure cost).

Absent at least one of these, re-attempting Feature 002 with the same corpus and a
similar technique set should be expected to reproduce this same DECLINE outcome —
the failure mode identified here (structural rule-vocabulary overlap, not a
fixable lexical-ambiguity or ranking-order problem) is unlikely to resolve itself
without one of the three changes above.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Benchmark Harness)**: Depends on Setup. **BLOCKS everything below.**
- **Phase 3 (Retrieval Evaluation)**: Depends on Phase 2.
- **Phase 4 (Reranking Evaluation)**: Depends on Phase 3; conditional — may be
  entirely skipped (documented, not silently omitted).
- **Phase 5 (Generation Evaluation)**: Depends on Phase 3 (or 4, if not skipped).
- **Phase 6 (Security Review)**: Depends on Phase 5.
- **Phase 7 (API Integration)**: Depends on Phase 6's T020 human/legal approval —
  **hard gate, not advisory.** T020 approves a *design* (T018/T019's pre-implementation
  checklists); it does not and cannot inspect Phase 7 code, because that code does not
  exist yet at T020's point in the sequence.
- **Phase 8 (UI Integration)**: Depends on Phase 7 — **including T033/T034**, the
  post-implementation security/boundary verification against T018/T019's checklists.
  Phase 8 does not begin merely because T021–T032 landed; T033/T034 must also pass.
- **Phase 9 (E2E Testing)**: Depends on Phase 8.
- **Phase 10 (Final Acceptance)**: Depends on Phase 9; its own T046 is a second,
  independent human/legal approval gate before the feature is considered complete,
  and is also the single landing point for an early decline reached at T011 or T020.

### Two-stage security verification (design → code), preserved explicitly

The reason T018/T019 and T033/T034 exist as four distinct tasks rather than two:
security/boundary correctness is checked once at design time (before any
implementation risk is taken) and once again against the real, implemented code
(before any UI is built on top of it) — neither stage substitutes for the other.

| Stage | Task | Checks | Against |
|---|---|---|---|
| Pre-implementation (Phase 6) | T018 | Prompt-injection boundary design | The DESIGN (research.md, the Phase 3–5 benchmark harness's own code) — no Phase 7 code exists yet |
| Pre-implementation (Phase 6) | T019 | Compliance-data-access boundary design | The DESIGN (contracts/legal-assistant-api.md, data-model.md) — no Phase 7 code exists yet |
| ↓ | T020 | Human/legal sign-off on T018+T019's design checklists | Gate before Phase 7 begins |
| Post-implementation (Phase 7) | T033 | Prompt-injection resistance of the REAL code | `backend/services/legal_assistant.py`/`backend/api/legal_assistant.py`, checked against T018's checklist |
| Post-implementation (Phase 7) | T034 | Compliance-data-access boundary of the REAL code | Same files, checked against T019's checklist |

### Evidence Gates (technology decision points) — full list

| Task | What is evaluated | Gated on |
|---|---|---|
| T008 | Pure dense-embedding retrieval | SC-001, SC-003, SC-009 |
| T009 | Hybrid keyword + semantic retrieval | SC-001, SC-003, SC-009 |
| T010 | Query rewriting/expansion | SC-001, SC-003, SC-009 |
| T012 | Reranking (conditional) | SC-001 improvement, SC-009 |
| T013 | Generation via existing Gemini integration | SC-007 |
| T014 | Generation via hosted Hugging Face Inference API | SC-007 |
| T015 | No generation — citation-only fallback | SC-007 (trivially) |
| T017 | Prompt-injection resistance of the selected *technique* (Phase 3–5 benchmark code) | FR-018, FR-030 |
| T045 | Full integrated pipeline, end-to-end | SC-001–SC-009, SC-012 |

(T033/T034 are verification tasks against an already-approved design, not
technology-adoption evidence gates — see the two-stage table above.)

### Human/Legal Approval Gates — full list

| Task | Decision |
|---|---|
| T020 | Whether to proceed from technology evaluation (Phases 3–6) into implementation (Phase 7) at all — approves a design, not code |
| T046 | Whether to accept the fully integrated, tested system for production, or decline and cleanly revert — also where an early T011/T020 decline is formally recorded |

### Parallel Opportunities

- T002/T003/T004 (Setup) — different files, no dependencies.
- T006/T007 (harness construction) — different concerns (retrieval-accuracy vs.
  groundedness), independent.
- T008/T009/T010 (retrieval candidates) — independently benchmarkable against the
  same fixed harness/dataset; only T011's consolidation is sequential.
- T013/T014/T015 (generation candidates) — same pattern.
- T018/T019 (Phase 6 pre-implementation design-review tasks) — independent review
  angles (injection boundary vs. compliance-data boundary), same pattern T033/T034
  repeat post-implementation.
- T026/T027/T028/T029 (Phase 7 registration + audit endpoint + contract tests) —
  different files once T021–T025 land.
- T033/T034 (Phase 7 post-implementation security verification) — independent review
  angles, same pattern as T018/T019; both can run as soon as T021–T026 land.
- T035 (frontend client) can start as soon as Phase 7's contract (T025) is stable,
  in parallel with T028–T034's backend tests/verification.

## Implementation Strategy

### Evidence-first, not MVP-first

Unlike a typical user-story-parallel feature, this plan's "MVP" is not "ship User
Story 1 first" — it is **"prove a viable, safe technology combination exists at
all."** Nothing in Phase 7 onward should be started before Phase 6's T020 approval,
because implementing an API/UI around a technology combination that later fails T045
would be wasted, revert-bound work — exactly the outcome `001-legal-rag`'s own Phase 8
discipline was designed to prevent. T020's approval is of a design, not of code that
does not yet exist; T033/T034 close that gap immediately once Phase 7's code is
written, before Phase 8 (UI) is allowed to begin.

### Decline is a first-class, successful outcome

If T011 or T020 conclude no viable combination/design exists, or T046 concludes the
integrated system does not clear its bars, stopping there — with the evidence
documented in `specs/002-legal-knowledge-assistant/benchmarks/` and recorded at T046
regardless of which point triggered it — is this task list completing successfully,
not failing. Constitution Principle XII (Clean Reversion of Rejected Experiments)
applies exactly as it would to any other declined technology choice in this project.
