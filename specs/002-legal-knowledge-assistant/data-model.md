# Phase 1 Data Model: Advanced Legal Knowledge Assistant for Officials

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Entities below are extracted directly from the spec's "Key Entities" section, with
concrete fields derived from the spec's functional requirements. This is a design
artifact, not an implementation — no schema is created by this document, and no field
below implies a specific retrieval/generation technology (those remain open per
[research.md](research.md)).

## Reused, unmodified from `001-legal-rag` (`backend/models/legal.py`)

This feature's citations are built entirely from `001-legal-rag`'s existing models,
embedded verbatim — not redefined, not forked:

- **`LegalSourceDocument`** — `source_id`, `title`, `citation`, `version`,
  `acquired_at`, `is_amendment`, `amends_source_id`, `verification_note`.
- **`LegalProvision`** — `provision_id`, `rule_sub_rule_clause`, `text`,
  `effective_status`, `is_currently_effective`, embedded `source`.

Every citation this feature produces (spec FR-009, FR-019) is one of these existing
objects, unchanged in shape. This is the concrete mechanism behind spec Assumptions'
"reuses `001-legal-rag`'s citation data shape" and Constitution Check's Principle V
row.

## LegalQuery

One free-form question submitted by an Official (spec FR-004, User Stories 1–2).

| Field | Type | Notes |
|---|---|---|
| `query_id` | string | Stable identifier for this query |
| `question_text` | string | The Official's free-form natural-language question, verbatim |
| `asked_by` | string (FK → user) | The Official who asked (FR-001) |
| `asked_at` | datetime | Timestamp |
| `case_context` | string \| null (FK → productRevisions.revisionId) | Present only when asked from within a case-review context (User Story 2); `null` for standalone research use |

**Validation rules**: `case_context`, when present, is used **only** to associate the
`LegalQuery` with a case for display/audit purposes (spec FR-028) — it is never read
by the retrieval/generation step to influence the answer (spec FR-024, the
constitutional non-negotiable: retrieval is always over the verified legal corpus
only, never over case-specific compliance data). This is a structural, not
conventional, separation: nothing in this data model gives the retrieval/generation
step a code path to `complianceResult`/`overall_status`/`compliance_score`.

## RetrievalCandidate (internal, non-persisted)

One provision surfaced by the retrieval step as potentially relevant to a
`LegalQuery` (spec FR-005, FR-006). An intermediate computation artifact — never
itself shown to the Official as a final answer, and not stored beyond the lifetime of
answering one query (unless a benchmark harness records it for evaluation purposes,
per research.md §1).

| Field | Type | Notes |
|---|---|---|
| `provision_id` | string (FK → LegalProvision) | Which corpus provision this candidate is |
| `relevance_signal` | number | Retrieval-technique-specific score (embedding similarity, hybrid score, reranker score — the specific meaning is technology-dependent, resolved at `/speckit-tasks`, not fixed here) |
| `retrieval_stage` | string | Which stage produced/promoted this candidate (e.g. initial retrieval vs. reranked) — supports the auditability spec FR-006 requires, without fixing how many stages exist |

## LegalAssistantAnswer

The output of processing one `LegalQuery` (spec FR-009–FR-017). This is a response
shape, computed on request; whether it is also persisted (beyond the audit-oriented
`QueryInteractionRecord` below) is an implementation decision not fixed here.

| Field | Type | Notes |
|---|---|---|
| `query_id` | string (FK → LegalQuery) | Which query this answers |
| `status` | enum: `answered` \| `insufficient_evidence` | (FR-015 — the two, and only two, first-class outcomes) |
| `answer_text` | string \| null | Generated explanation; `null` when `status = insufficient_evidence`; always structurally distinct from `citations[].text` (FR-011) |
| `citations` | list of `LegalProvision` (with embedded `LegalSourceDocument`) | Empty when `status = insufficient_evidence`; one or more when `status = answered` (FR-009) |
| `ambiguous_candidates` | list of `LegalProvision` \| null | Populated only for the explicit-disambiguation path (FR-017) — distinct candidates presented as separate options rather than one silently-chosen answer; `null` in the normal single-answer case |
| `generated_by` | string \| null | Identifies what produced `answer_text`, when present (FR-014 — must be visibly labeled as AI-generated) |

**Validation rules** (the structural expression of constitution Principle III,
extending `001-legal-rag`'s own `LegalBasisResult` invariant with a
generation-specific rule this feature newly requires):

1. If `status = insufficient_evidence`, then `answer_text` MUST be `null` and
   `citations` MUST be empty — there is no code path that allows an
   insufficient-evidence result to simultaneously carry generated text or a citation
   (mirrors `001-legal-rag`'s `LegalBasisResult` invariant exactly).
2. If `status = answered`, then `citations` MUST be non-empty — an answer can never be
   presented as confident with zero grounding (FR-009, FR-010).
3. If `answer_text` is non-null, every factual/legal claim within it MUST be
   supported by at least one entry in `citations` (FR-013, SC-007) — this is a
   content-level invariant a schema alone cannot fully enforce, which is exactly why
   research.md §4 requires a dedicated groundedness benchmark before any generation
   technology is accepted, not merely a shape check.
4. `ambiguous_candidates`, when non-null, contains entries from *different*
   `rule_id`s or fields than `citations` — it exists specifically for the FR-017
   cross-rule-ambiguity case, not as a generic "here are some other matches" list.

## QueryInteractionRecord

An audit-log-style record of one `LegalQuery` and its `LegalAssistantAnswer` (spec
FR-028). Reuses this project's existing `history`-collection pattern (research.md §6)
— not a new logging subsystem.

| Field | Type | Notes |
|---|---|---|
| `query_id` | string (FK → LegalQuery) | |
| `asked_by` | string (FK → user) | Echoed for audit-query convenience |
| `asked_at` | datetime | |
| `answer_status` | enum: `answered` \| `insufficient_evidence` | |
| `cited_provision_ids` | list of string | Which provisions were cited, if any — enough to answer "who asked what, when, and what was cited" (spec FR-028) without duplicating full answer text into an audit trail |

**Validation rules**: Informational only — this record is never read by
`ComplianceEngine`, the Official Review Workflow's decision logic, or any other
component as an input to a decision (constitution Principle I/II). It exists to
answer an audit question after the fact, not to drive behavior.

## Benchmark Query Set (test/benchmark-time construct, not a runtime data model entity)

Not a persisted or request/response entity — a **test fixture concept**, analogous to
`001-legal-rag`'s own Phase 8 benchmark dataset (`specs/001-legal-rag/tasks.md`,
"Phase 8 Evaluation Outcome"). Each entry: a `question_text`, an `expected_provisions`
list (possibly empty, for a correct-refusal case), and an `adversarial_class` tag
(confusable / generic / multi-topic / no-result / amendment-history, mirroring
research.md §1's evaluation requirement). Used exclusively by the future benchmark
task (research.md §1, §4) to classify every outcome as (A) correct retrieval, (B)
plausible-but-wrong retrieval, or (C) correct refusal, per spec SC-009. Not modeled
further here because its exact shape depends on which retrieval technique(s)
research.md §1 ultimately evaluates.

## Relationship to existing LegalLense data (read-only; nothing below is modified by this feature)

- `001-legal-rag`'s `LegalProvision`/`LegalSourceDocument`/corpus file — read only,
  the entire retrieval and citation surface this feature operates over. Never
  modified, never forked into a second corpus.
- `ComplianceEngine`'s `RuleResult`/`FieldResult` (`backend/models/compliance.py`) —
  **not read at all** by this feature's retrieval/generation path (spec FR-024) —
  contrast with `001-legal-rag`, which reads a finding's `rule_id`/field (but never
  its status/score) to perform its lookup. This feature's `LegalQuery.case_context`
  is display/audit metadata only, never an input to retrieval.
- `productRevisions` (MongoDB, existing) — read only, solely to resolve
  `case_context` display metadata (e.g. which product/case a query was asked from);
  never written to by this feature.
- `officialDecision`/`decisionHistory` (MongoDB, existing, part of
  `productRevisions`) — never read, never written, by any component in this feature
  (spec FR-003, FR-024, FR-026).
- `history` (MongoDB, existing collection) — written to, additively, for
  `QueryInteractionRecord` entries (research.md §6) — the only write this feature
  performs anywhere in the existing data model.
