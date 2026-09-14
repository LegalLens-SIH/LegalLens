# LegalLense Constitution

## Core Principles

### I. Deterministic Compliance Authority
The deterministic `ComplianceEngine` (`backend/services/compliance_engine.py`) is the
**sole** authority for compliance status (`PASS`/`FAIL`/`COMPLIANT`/`NON_COMPLIANT`/
`NEEDS_MANUAL_VERIFICATION`/`REVIEW_REQUIRED`) and `compliance_score`. No other
component — RAG, the Gemini vision-AI fallback, YOLO, OCR, structured extraction, or
any future AI/ML feature — may write, override, or imply these values. A field may be
*supplied* by an AI fallback (e.g. Gemini resolving a low-confidence field), but the
*decision* about what that field means for compliance is always computed by this one
engine, never by the component that supplied the value.

### II. Official Human Authority
The Official (Legal Metrology Inspector) is the final human decision-maker. Every
AI-assisted or AI-sourced output — Gemini field suggestions, RAG-retrieved legal
citations, structured-extraction values — is advisory only and is never itself a
decision. RAG in particular **must never** determine `PASS`/`FAIL`/`REVIEW_REQUIRED`
or any other compliance/decision status, and must never write to `officialDecision` or
any field an Official's review is authoritative over.

### III. No Hallucinated Legal Requirements
Any legal citation, rule reference, or explanation surfaced by RAG or any generative
component MUST be grounded in retrieved, verified source text with traceable
provenance (source document, section/rule number, retrieval chunk ID). Generating or
paraphrasing a legal requirement that cannot be traced to a verified source is
prohibited outright — a low-confidence or empty retrieval result must be surfaced as
"not found," never filled in with a plausible-sounding guess.

### IV. Structural Separation of AI Output from Authoritative Decisions
AI-generated fields and values (Gemini fallback values, RAG explanations/citations,
structured-extraction confidence) MUST be stored, labeled, and rendered as
structurally distinct fields from `ComplianceEngine` output and Official decision
data — e.g. a sibling field, never merged into or silently blended with
`complianceResult` or `officialDecision`. A user reading a report must always be able
to tell, without inference, which parts are AI-assisted and which are the
authoritative engine/human decision.

### V. Evidence Provenance
Every field value derived from OCR or AI must retain traceable evidence (region IDs,
bounding boxes, source text, confidence) end-to-end from detection through to the
report UI. Evidence links must never be dropped, fabricated, or approximated — an
unlinkable value is rendered as unlinkable, not silently connected to the nearest
plausible region.

### VI. Accuracy Over Performance
Where accuracy and performance/latency trade off against each other, accuracy wins by
default. This mirrors the project's existing discipline of a frozen deterministic
accuracy baseline and benchmark-gated Gemini-assisted improvements — a faster but
less accurate change is not an improvement.

### VII. Test + Real-Image Benchmark Discipline for Production Accuracy Changes
Any change affecting OCR, extraction, or compliance accuracy requires, before
acceptance: (1) the automated test suite passing, (2) a real-image benchmark run
against the project's fixture set, and (3) an explicit regression/safety review. A
change that only "looks correct" in code review or on synthetic input is not
sufficient.

### VIII. Surgical, Scoped Changes
Changes are scoped strictly to their stated purpose. Unrelated modifications —
however small or however "while I'm in there" — must not be bundled into the same
change, commit, or task. Where unrelated pre-existing changes are already present in
the working tree, they are identified and excluded explicitly, never swept in by a
wholesale `git add`.

### IX. Test Integrity
Tests must never be weakened, skipped, or removed merely to make a change pass. A
failing test identifies a real problem to be fixed in the implementation; the test
itself is not the thing to change unless the test was factually wrong about intended
behavior — and that determination is made explicitly, never as a side effect of
"getting to green."

### X. Architectural Conservatism
The existing architecture — the OCR pipeline, the deterministic `ComplianceEngine`,
the Gemini fallback, the evidence system, the Manufacturer and Official Review
workflows — is preserved unless a change to it is explicitly approved. No silent
rewrites, no "improving" a working component as a side effect of an unrelated task.

### XI. Secrets Hygiene
API keys, credentials, and other secrets must never be committed to the repository.
`.env` remains gitignored at all times; only `.env.example` (placeholder values, no
real keys) is tracked in version control.

### XII. Clean Reversion of Rejected Experiments
Any experiment or change that is rejected — fails its benchmark, regresses accuracy,
fails safety review — is cleanly reverted, leaving no partial implementation, dead
code, or disabled-but-present logic behind.

## Development Workflow

LegalLense uses two complementary, deliberately different-weight workflows depending
on the size of the change:

**Major architectural features** (e.g. Legal RAG, a major OCR pipeline change, a major
compliance-rule-engine expansion, a significant Official Workflow enhancement) MUST
use the Spec Kit workflow:

```
SPECIFICATION → PLAN → TASKS
        ↓
AUDIT → MINIMAL IMPLEMENTATION → TEST → BENCHMARK → SAFETY REVIEW → ACCEPT/REVERT
```

Spec Kit's `/speckit-specify`, `/speckit-plan`, and `/speckit-tasks` own the
pre-implementation phase (what/why, architecture, task breakdown, written down and
durable across sessions). The project's own established execution loop —
AUDIT → MINIMAL IMPLEMENTATION → TEST → BENCHMARK → SAFETY REVIEW → ACCEPT/REVERT —
governs every individual task's actual implementation, unchanged. Spec Kit's
`/speckit-implement` step does not bypass this: each task generated by
`/speckit-tasks` is still executed under the full discipline above, not as a single
uninterrupted code-generation pass. The bundled `speckit` workflow's default
specify→plan→tasks→implement pipeline has human review gates before `plan` and before
`tasks`, but **not** before `implement` — for LegalLense, implementation of any task
touching OCR, extraction, `ComplianceEngine`, or evidence provenance always inserts an
explicit AUDIT and SAFETY REVIEW checkpoint before that task is considered started,
regardless of what the bundled workflow's default gating provides.

**Small bug fixes and surgical accuracy fixes** continue using the existing controlled
workflow directly, without a full Spec Kit specify/plan/tasks cycle:

```
AUDIT → HYPOTHESIS → MINIMAL CHANGE → TEST → BENCHMARK → SAFETY REVIEW → ACCEPT/REVERT
```

This is not a lesser standard — it is the same safety discipline at a scope that does
not warrant a written specification. The decision of which workflow applies is made
explicitly at the start of a task, not assumed.

## Governance

This constitution supersedes any other stated practice or convention when the two
conflict. Amendments require: an explicit proposal describing the change, a version
bump per semantic versioning (MAJOR: principle removed/redefined incompatibly; MINOR:
principle added or materially expanded; PATCH: wording/clarification only), and
recording of the amendment date below. Every Spec Kit `/speckit-plan` for a major
feature MUST include a Constitution Check against the principles above; any violation
must be justified in that plan's Complexity Tracking section or the plan is not
approved. Complexity (new abstractions, new dependencies, new services) must be
justified by a concrete, stated need — not by anticipated future use.

**Version**: 1.0.0 | **Ratified**: 2026-09-14 | **Last Amended**: 2026-09-14
