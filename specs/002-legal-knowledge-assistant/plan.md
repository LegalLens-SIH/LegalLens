# Implementation Plan: Advanced Legal Knowledge Assistant for Officials

**Branch**: `002-legal-knowledge-assistant` (directory identifier only — no git branch was created; work continues on `main` per this project's current convention, same as `001-legal-rag`) | **Date**: 2026-09-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-legal-knowledge-assistant/spec.md`

## Summary

Add a separate, Official-only, read-only free-form legal question-answering assistant
that retrieves from the same verified legal corpus `001-legal-rag` already established
(`backend/legal_corpus/legal_metrology_packaged_commodities_2011.json`, reused
verbatim — not forked), ranks candidate provisions, generates a grounded explanation,
and returns it with exact rule/sub-rule/clause citations and full source provenance —
or an explicit "insufficient legal basis" refusal when evidence is inadequate. Unlike
`001-legal-rag`, this feature's query shape is genuinely free-form natural language
with no `rule_id`/field key supplied, which is precisely the query shape
`001-legal-rag`'s own Phase 8 benchmark evaluated and found existing off-the-shelf
embedding retrieval (`BAAI/bge-small-en-v1.5`, `BAAI/bge-m3`) insufficient for at this
corpus's scale (78–81% top-1, 15.6% wrong-rule, against a spec bar of ≥90%/<5%). That
benchmark is treated here as **directly relevant prior evidence**, not a reason to
skip this feature's own dedicated benchmark — see [research.md](research.md) §1 for
why naive single-embedding-model retrieval is not assumed sufficient and what
additional techniques (hybrid retrieval, reranking, query rewriting) must be evaluated
before any technology is selected. No technology (embedding model, vector database,
reranker, generation model) is chosen in this plan — Phase 0 below defines the
evaluation methodology and defers selection to a dedicated benchmark task, mirroring
the discipline `001-legal-rag`'s own Phase 8 used before declining semantic retrieval
for its different (exact-key) use case.

## Technical Context

**Language/Version**: Python 3.11 (hard constraint — matches `backend/.venv311`, the
project's existing pinned environment; unchanged from `001-legal-rag`).

**Primary Dependencies**: FastAPI + Pydantic (existing, reused — new router follows
the exact pattern of `backend/api/official.py`/`backend/api/legal_basis.py`, no new
web framework). Retrieval/ranking/generation libraries are explicitly **NOT** selected
here — see [research.md](research.md) §1–§4 for the evaluation methodology each
candidate (embedding model, vector index, reranker, generation model) must pass before
adoption. No dependency is installed, no model is downloaded, and no vector database
instance is created by this plan.

**Storage**: The retrieval corpus is `001-legal-rag`'s existing, unchanged
`backend/legal_corpus/legal_metrology_packaged_commodities_2011.json` — this feature
does not define a second corpus file or a new corpus format (spec Assumptions). A
vector index over that corpus's provisions MAY be introduced as a *computed,
regenerable-from-source* artifact (never a second source of truth for legal text) —
whether it is in-process (as `001-legal-rag` research.md §1 chose for its own,
much-smaller-need case) or an external service (justified here specifically because
this feature's real free-form-query use case is the one `001-legal-rag` found such
infrastructure not yet justified for) is a [research.md](research.md) §2 open
question, resolved by benchmark, not by this plan. MongoDB (existing) is unaffected
except for an additive, optional `Query Interaction Record` audit trail (spec FR-028),
reusing the existing `history`-collection pattern (`backend/api/auth.py`'s
`db.history`), not a new collection design.

**Testing**: pytest (existing, hard constraint) for the backend; this feature's tests
live alongside its implementation files following this project's established
colocation convention (e.g. a future `test_legal_assistant.py` beside a future
`legal_assistant.py`, exactly as `test_legal_retrieval.py` sits beside
`legal_retrieval.py` today). A dedicated benchmark harness (Python script(s), not a
pytest test) is required before any technology is accepted — see
[research.md](research.md) §1, mirroring `001-legal-rag`'s own Phase 8 benchmark
methodology (`specs/001-legal-rag/tasks.md`'s "Phase 8 Evaluation Outcome").

**Target Platform**: The existing deployment model — manual `backend/.venv311` +
`uvicorn`, static-HTML frontend, no containerization, Windows dev machine confirmed
CPU-only (13th-gen i5-1340P, 16GB RAM, no discrete GPU) — unchanged. Unlike
`001-legal-rag` FR-030 (a preference, not a lock), this feature's own spec FR-032
explicitly permits an externally-hosted service where benchmark-justified, because a
CPU-only 16GB machine cannot realistically self-host a generation-capable model at
useful quality (the same conclusion `001-legal-rag` research.md §4 already reached for
its own, still-undecided, optional explanation step).

**Project Type**: Web application (existing FastAPI backend + static-HTML frontend) —
unchanged structure, same as `001-legal-rag`.

**Performance Goals**: Per spec SC-011 — a single query must complete within a
duration that keeps it usable as an interactive research tool during case review; no
hard numeric SLA is invented here (spec explicitly defers this to empirical,
benchmark-time tuning, matching `001-legal-rag` research.md §6's own precedent).

**Constraints**: CPU-preferred but not CPU-locked (spec FR-032); no new dependency
without the existing `--no-deps` + `backend/constraints.txt` pinning discipline; no
write path to `complianceResult`/`officialDecision`/`decisionHistory`/any product or
user record (spec FR-003, FR-024, FR-026); Official-only access reusing the existing
`official_user` auth dependency (spec FR-001); this feature's load must never degrade
the OCR pipeline or `ComplianceEngine` (spec FR-034).

**Scale/Scope**: The retrieval corpus starts at exactly `001-legal-rag`'s current
scale — 11 verified provisions across 2 rule_ids (`LMPC-R6`: 8 fields,
`LMPC-R24`: 3 fields). This is the single most important planning fact: it is the
**same corpus size `001-legal-rag`'s Phase 8 benchmark already found naive embedding
retrieval insufficient for** (see Summary above and research.md §1) — so this plan's
Phase 0 research explicitly treats "will off-the-shelf semantic retrieval alone clear
this feature's own SC-001/SC-003 bar at this corpus size" as an open, evidence-backed
risk, not a formality.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Gate | Status |
|---|---|---|
| I. Deterministic Compliance Authority | No requirement/entity/endpoint in this plan computes, reads for decision purposes, or writes `overall_status`/`compliance_score`. `LegalAssistantAnswer` (data-model.md) never carries or implies a compliance decision. | **PASS** |
| II. Official Human Authority | The one new capability (contracts/legal-assistant-api.md) is read-only, Official-only, and every answer is explicitly framed as research input, never a decision or instruction to act. | **PASS** |
| III. No Hallucinated Legal Requirements | `LegalAssistantAnswer`'s validation rule (data-model.md) structurally forbids a confident answer without a grounding citation, and forbids `provisions` being non-empty when `status = insufficient_evidence` — mirroring `001-legal-rag`'s own `LegalBasisResult` invariant, extended with a generation-groundedness rule (FR-013) `001-legal-rag` did not need (it never generates). | **PASS** |
| IV. Structural Separation of AI Output from Authoritative Decisions | `answer_text`/`citations` are distinct fields, never merged into `ComplianceEngine` output, `officialDecision`, or even `001-legal-rag`'s own `LegalBasisResult` — three visibly separate concepts, not two. | **PASS** |
| V. Evidence Provenance | Every `LegalAssistantAnswer` citation embeds `001-legal-rag`'s existing `LegalProvision`/`LegalSourceDocument` shape verbatim (data-model.md) — provenance is inherited from an already-constitution-compliant model, not reinvented. | **PASS** |
| VI. Accuracy Over Performance | research.md's Phase 0 design explicitly treats accuracy (SC-001–SC-010) as the gate technology must clear, with SC-011's latency bar deliberately left non-numeric pending empirical tuning — mirroring `001-legal-rag`'s own precedent of never letting a performance target pressure an accuracy decision. | **PASS** |
| VII. Test + Benchmark Discipline | quickstart.md's scenarios require the full existing suite (324 passed, 1 skipped, the current post-`001-legal-rag` baseline) to pass unchanged with the feature disabled; Phase 0's benchmark methodology (research.md §1) is a direct continuation of `001-legal-rag`'s own Phase 8 benchmark-before-adopt discipline, not a lighter-weight substitute. | **PASS** |
| VIII. Surgical, Scoped Changes | Project Structure below adds new files only; `001-legal-rag`'s corpus, models, retrieval service, API, and frontend integration are reused read-only and are not modified by this plan (spec Non-Goals). | **PASS** |
| IX. Test Integrity | No existing test is referenced for modification anywhere in this plan. | **PASS** |
| X. Architectural Conservatism | Technology selection is explicitly deferred to a benchmark, not assumed — the plan does not introduce a vector database, reranker, or generation model as a foregone conclusion; each is only adopted if its own dedicated evidence clears the spec's bar, exactly as `001-legal-rag`'s Phase 8 semantic retrieval was only ever going to be adopted on the same basis (and, for its own use case, was not). | **PASS** |
| XI. Secrets Hygiene | Any credential a benchmark-justified externally-hosted service needs (spec FR-032) follows the existing `.env`/`.env.example` convention — no new pattern introduced, no credential committed at plan time (none exists yet). | **PASS** |
| XII. Clean Reversion | Every new file listed in Project Structure below is additive and independently removable; if the Phase 0 benchmark finds no technology combination clears the spec's acceptance bar at this corpus's current scale, this feature can be declined at that gate exactly as `001-legal-rag`'s Phase 8 was, leaving no partial implementation behind. | **PASS** |

**No violations. Complexity Tracking table below is empty by design.**

## Project Structure

### Documentation (this feature)

```text
specs/002-legal-knowledge-assistant/
├── spec.md               # Feature specification (already committed)
├── plan.md               # This file
├── research.md           # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   └── legal-assistant-api.md   # Phase 1 output
└── checklists/
    └── requirements.md   # Spec quality checklist (already committed)
```

### Source Code (repository root — real LegalLense paths, following `001-legal-rag`'s established layout exactly, not a generic placeholder)

```text
backend/
├── legal_corpus/                        # REUSED, UNCHANGED — 001-legal-rag's
│                                         #   existing corpus file; this feature adds
│                                         #   no second corpus file
├── models/
│   ├── legal.py                         # REUSED, UNCHANGED — LegalSourceDocument/
│   │                                     #   LegalProvision reused verbatim as this
│   │                                     #   feature's citation shape
│   └── legal_assistant.py               # NEW (Phase 1 candidate name; final name
│                                         #   decided at /speckit-tasks) — LegalQuery,
│                                         #   LegalAssistantAnswer, QueryInteraction-
│                                         #   Record (pydantic models, same style as
│                                         #   backend/models/legal.py)
├── services/
│   ├── legal_retrieval.py               # REUSED, UNCHANGED — 001-legal-rag's
│   │                                     #   deterministic lookup; NOT extended or
│   │                                     #   forked by this feature (spec Non-Goals)
│   └── legal_assistant.py               # NEW (Phase 1 candidate name) — query
│       + test_legal_assistant.py        #   processing, retrieval-technology
│                                         #   integration (per research.md's benchmark
│                                         #   outcome), grounded-answer construction,
│                                         #   refusal logic; colocated tests per this
│                                         #   project's convention
└── api/
    ├── legal_assistant.py               # NEW (Phase 1 candidate name) — the
    │   + test_legal_assistant.py        #   Official-only assistant router
    │                                     #   (contracts/legal-assistant-api.md)
    └── main.py                          # MODIFIED (future task) — one new import +
                                          #   one new app.include_router(...) line,
                                          #   identical pattern to every existing
                                          #   router registration; NOT performed by
                                          #   this plan

frontend/
├── assets/
│   └── legal-assistant-client.js        # NEW (future task) — thin fetch wrapper,
│                                         #   mirrors legal-basis-client.js's/
│                                         #   official-client.js's structure
└── legal-assistant.html                 # NEW (future task) — standalone,
                                          #   Official-only research surface (spec
                                          #   FR-023); case-review-context entry point
                                          #   (User Story 2) is additive UI on
                                          #   case-details.html, added in that same
                                          #   future task, never merged into its
                                          #   existing findings/decision markup
```

**Structure Decision**: Follows `001-legal-rag`'s established structure exactly —
`backend/models|services|api/` file-per-concern, `frontend/assets/` for the fetch
client, colocated tests. The one deliberate difference from `001-legal-rag`'s own
layout: this feature's models/services/API files are **new, separate files**
(`legal_assistant.py`, not additions to `legal.py`/`legal_retrieval.py`/
`legal_basis.py`), because `001-legal-rag`'s existing files are explicitly reused
read-only (spec Non-Goals) — this feature must never become a reason to modify
already-shipped, checkpointed code. File names above are Phase 1 candidates, subject
to final confirmation at `/speckit-tasks`.

## Complexity Tracking

*No entries — Constitution Check above reports zero violations. This plan
deliberately defers every complexity decision (which embedding model, whether a
vector database is justified, whether/which reranker, which generation model) to a
benchmark gate rather than assuming any of them — see research.md. Nothing here is a
complexity increase adopted without evidence.*

## Post-Design Constitution Re-Check

*Re-evaluated after Phase 1 design (data-model.md, contracts/, quickstart.md) above.*

No new violation introduced by the Phase 1 design. `LegalAssistantAnswer`'s
validation rule (data-model.md) is, if anything, a *stronger* enforcement of
Principle III than the pre-design gate anticipated — the "insufficient evidence
implies no citations, and every citation implies a supporting retrieved passage" rule
is now a concrete, testable data-shape invariant, not just a stated principle. The
Phase 1 contract (contracts/legal-assistant-api.md) confirms no write endpoint exists
anywhere in this feature. Gate re-confirmed: **PASS, unchanged.**
