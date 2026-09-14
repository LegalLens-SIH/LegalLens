# Implementation Plan: Legal RAG (Legally Grounded Knowledge Layer)

**Branch**: `001-legal-rag` (directory identifier only — no git branch was created; work continues on `main` per this project's current convention) | **Date**: 2026-09-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-legal-rag/spec.md`

## Summary

Add a read-only legal-basis retrieval layer that, given an existing `ComplianceEngine`
finding's `rule_id` (and optional field), returns the verified legal provision(s) that
support it — with full source provenance — for display alongside the finding in the
Manufacturer and Official report surfaces. Retrieval is deterministic-`rule_id`-first
(a direct lookup against a structured, hand-verified legal corpus), with semantic
search as a documented but likely-unnecessary-at-current-scale fallback (see
[research.md](research.md) §1). No generation/explanation step is required for Phase 1
(spec FR-015 makes it optional; deferred per [research.md](research.md) §4). The
feature is purely additive: one new read-only endpoint, one new corpus data
structure, and one new report-page section — `ComplianceEngine`, OCR, the Gemini
fallback, and the Official Review Workflow are not modified.

## Technical Context

**Language/Version**: Python 3.11 (hard constraint — matches `backend/.venv311`,
the project's existing pinned environment; not a choice made by this plan).

**Primary Dependencies**: FastAPI + Pydantic (existing, reused — this feature adds a
router following the exact pattern of `backend/api/compliance.py`/`backend/api/official.py`,
no new web framework). For the semantic-fallback path: an embedding library
(`FlagEmbedding` or `sentence-transformers`, exact package TBD at task time) — see
[research.md](research.md) §2 for the model decision (`bge-small-en-v1.5`) and its
justification. **No vector-database client dependency is added in Phase 1** — see
[research.md](research.md) §1 for why an in-process similarity search is used instead
of introducing Qdrant now.

**Storage**: The legal corpus is a structured, versioned data file (format TBD at task
time — plausibly a JSON file colocated with `backend/rules/legal_metrology_rules_2011.json`,
matching that file's existing convention) — not a new database. MongoDB (existing) is
unaffected; nothing in this feature writes to it beyond, optionally, caching a
`LegalBasisResult` later (explicitly deferred, see [data-model.md](data-model.md)).

**Testing**: pytest (existing, hard constraint — this feature's tests must live
alongside its implementation files following this project's established
colocation convention, e.g. `test_legal_retrieval.py` next to `legal_retrieval.py`,
exactly as `test_compliance_engine.py` sits next to `compliance_engine.py` today).

**Target Platform**: The existing deployment model — manual `backend/.venv311` +
`uvicorn`, static-HTML frontend, no containerization, Windows dev machine confirmed
CPU-only (13th-gen i5-1340P, 16GB RAM, no discrete GPU). This is a hard constraint,
not a choice: no component introduced by this plan may require a GPU or a container
runtime that doesn't already exist in this project.

**Project Type**: Web application (existing FastAPI backend + static-HTML frontend) —
matches "Option 2" of this project's actual structure, adapted below to LegalLense's
real paths (not the generic `backend/src/` placeholder).

**Performance Goals**: Per spec SC-007 — a legal-basis lookup must not be the
dominant wait time in an Official's case-review page load. No hard millisecond SLA is
set in the spec; none is invented here either.

**Constraints**: CPU-only execution for every component (no GPU dependency,
anywhere); no new dependency introduced without following the project's existing
`--no-deps` + `backend/constraints.txt` pinning discipline; no containerization
introduced; no synchronous embedding/indexing work on the request path (embedding of
the legal corpus is a batch/offline step, per spec FR-032).

**Scale/Scope**: Required corpus coverage is 2 rule_ids
(`LMPC-R6-MANDATORY-DECLARATIONS`: 8 fields, `LMPC-R24-WHOLESALE-DECLARATIONS`: 3
fields) — on the order of a few dozen provision units at most. This scale is the
single biggest driver of the technology decisions in [research.md](research.md)
(notably: no vector database needed yet).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Gate | Status |
|---|---|---|
| I. Deterministic Compliance Authority | No requirement/entity/endpoint in this plan computes or writes `overall_status`/`compliance_score`. `LegalBasisResult` (data-model.md) is a pure read/response shape with no write path into `ComplianceEngine` output. | **PASS** |
| II. Official Human Authority | The one new endpoint (contracts/legal-basis-api.md) is `GET`-only; no write endpoint exists for legal-basis or decision content. | **PASS** |
| III. No Hallucinated Legal Requirements | `LegalBasisResult`'s validation rule (data-model.md) structurally forbids `provisions`/`explanation` being non-empty when `status != found` — this is enforced by the data shape, not just a prompt/convention. | **PASS** |
| IV. Structural Separation of AI Output | `explanation`/`explanation_source` are separate, optional fields on `LegalBasisResult`, never merged into `provisions` (verified fact) or any `ComplianceEngine` field. | **PASS** |
| V. Evidence Provenance | Every `LegalProvision` carries a mandatory `source_id` link to a `LegalSourceDocument` with mandatory `version`/`citation`/`verification_note` (data-model.md) — provenance is structurally required, not optional metadata. | **PASS** |
| VI. Accuracy Over Performance | research.md's decisions (small CPU-friendly embedding model, deferred vector DB, deferred generation) all favor correctness/simplicity over any performance optimization. | **PASS** |
| VII. Test + Benchmark Discipline | quickstart.md Scenario 5 requires the full existing suite (289/1) to pass unchanged with the feature disabled; new functionality gets its own tests per this project's colocation convention. Success Criteria (spec SC-001–SC-009) define this feature's own accuracy bar, mirroring the existing OCR benchmark discipline. | **PASS** |
| VIII. Surgical, Scoped Changes | Project Structure below adds new files only; zero existing file is modified except one router-registration line in `backend/main.py` (the exact same additive pattern already used for `official_router`/`extraction_router`). | **PASS** |
| IX. Test Integrity | No existing test is referenced for modification anywhere in this plan. | **PASS** |
| X. Architectural Conservatism | research.md §1 explicitly rejects introducing new infrastructure (vector DB, containerization) where the existing architecture already suffices at current scale. | **PASS** |
| XI. Secrets Hygiene | If a future generation-LLM decision (research.md §4) needs a credential, it follows the existing `.env`/`.env.example` convention — no new pattern introduced. Not needed at all in Phase 1 scope (no generation step). | **PASS** |
| XII. Clean Reversion | Every new file listed in Project Structure below is additive and independently removable; the one modified line in `main.py` is a single `include_router` call, trivially revertible. | **PASS** |

**No violations. Complexity Tracking table below is empty by design.**

## Project Structure

### Documentation (this feature)

```text
specs/001-legal-rag/
├── spec.md               # Feature specification (already committed)
├── plan.md               # This file
├── research.md           # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   └── legal-basis-api.md   # Phase 1 output
└── checklists/
    └── requirements.md   # Spec quality checklist (already committed)
```

### Source Code (repository root — real LegalLense paths, not a generic placeholder)

```text
backend/
├── legal_corpus/                        # NEW — structured legal-source data
│   └── legal_metrology_packaged_commodities_2011.json   # (or similar; format decided at task time)
├── models/
│   └── legal.py                         # NEW — LegalSourceDocument, LegalProvision,
│                                         #        LegalProvisionRuleLink, LegalBasisResult
│                                         #        (pydantic models, same style as
│                                         #        backend/models/compliance.py)
├── services/
│   ├── legal_retrieval.py               # NEW — deterministic rule_id/field lookup +
│   │                                     #        semantic-fallback search (in-process,
│   │                                     #        per research.md §1)
│   └── test_legal_retrieval.py          # NEW — colocated tests, this project's convention
└── api/
    ├── legal_basis.py                   # NEW — the GET /api/compliance/legal-basis
    │                                     #        router (contracts/legal-basis-api.md)
    ├── test_legal_basis.py              # NEW — colocated tests, following
    │                                     #        test_official.py's dependency-override
    │                                     #        + real-Mongo-where-relevant pattern
    └── main.py                          # MODIFIED — one new import + one new
                                          #            app.include_router(...) line,
                                          #            identical pattern to every existing
                                          #            router registration

frontend/
├── assets/
│   └── legal-basis-client.js            # NEW — thin fetch wrapper, mirrors
│                                         #        official-client.js's structure
├── case-details.html                    # MODIFIED (future increment, out of Phase 1
│                                         #  task scope per User Story priority) — adds
│                                         #  a "Legal Basis" section per spec FR-021
└── self-check-report.html               # MODIFIED (same future increment)
```

**Structure Decision**: LegalLense's existing structure is `backend/` and `frontend/`
at the repository root (confirmed fact, not a choice — there is no `src/` layout in
this project). This plan follows that structure exactly, using the same
router-per-file, model-per-concern, colocated-test conventions already established by
`backend/api/official.py` (the most recently added router, from the Official Review
Workflow) and `backend/models/compliance.py`. `backend/legal_corpus/` is a new
top-level data directory under `backend/`, chosen to sit alongside — not inside —
`backend/rules/`, since the two are conceptually distinct: `backend/rules/` is the
compliance *ruleset* (what to check), `backend/legal_corpus/` is the legal *text*
supporting those rules (why the rule exists) — keeping them separate avoids
conflating a decision-relevant file with a citation-only one, matching constitution
Principle IV's spirit even at the file-layout level.

## Complexity Tracking

*No entries — Constitution Check above reports zero violations. This plan
deliberately avoids every complexity increase (vector DB, generation LLM,
containerization) that isn't yet justified by the feature's required scope; see
research.md for the explicit reasoning behind each deferral.*

## Post-Design Constitution Re-Check

*Re-evaluated after Phase 1 design (data-model.md, contracts/, quickstart.md) above.*

No new violation introduced by the Phase 1 design. The `LegalBasisResult` shape's
validation rule (data-model.md) is, if anything, a *stronger* enforcement of Principle
III than the pre-design gate anticipated — the "not available implies empty
provisions/explanation" rule is now a concrete, testable data-shape invariant, not
just a stated principle. Gate re-confirmed: **PASS, unchanged.**
