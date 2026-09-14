# Phase 1 Data Model: Legal RAG

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Entities below are extracted directly from the spec's "Key Entities" section, with
concrete fields derived from the spec's functional requirements. This is a design
artifact, not an implementation — no schema is created by this document.

## LegalSourceDocument

A verified, authoritative legal text (spec FR-001–FR-004, FR-028).

| Field | Type | Notes |
|---|---|---|
| `source_id` | string | Stable identifier, e.g. `IN-LM-PACKAGED-COMMODITIES-RULES-2011` |
| `title` | string | e.g. "Legal Metrology (Packaged Commodities) Rules, 2011" |
| `citation` | string | Authoritative citation (Gazette reference or equivalent) |
| `version` | string | Identifies exactly which snapshot of the source this is (FR-007, FR-028) |
| `acquired_at` | date | When this source was verified/acquired |
| `is_amendment` | boolean | Distinguishes a base text from an amendment/notification |
| `amends_source_id` | string \| null | If `is_amendment`, the base source this modifies |
| `verification_note` | string | How/where this was verified — required, never empty (FR-001) |

**Validation rules**: `source_id`, `citation`, `version`, `verification_note` are all
required and non-empty — a `LegalSourceDocument` with any of these missing cannot be
referenced by a `LegalProvision` (FR-001, FR-028).

## LegalProvision

One structured unit of legal text (spec FR-002, FR-003, FR-005).

| Field | Type | Notes |
|---|---|---|
| `provision_id` | string | Stable identifier for this specific provision unit |
| `source_id` | string (FK → LegalSourceDocument) | Which document this came from |
| `rule_sub_rule_clause` | string | e.g. "Rule 6(1)(e)" — the exact legal reference (FR-002) |
| `text` | string | The provision text itself, verbatim from the source |
| `effective_status` | enum: `original` \| `amended` \| `superseded` | (FR-003) |
| `supersedes_provision_id` | string \| null | If `amended`/`superseded`, the prior version |
| `is_currently_effective` | boolean | Which version to surface by default (Edge Cases) |

**Validation rules**: `text` must be non-empty and must trace to a `source_id` that
exists (no orphaned provisions). Exactly one `LegalProvision` in a
`supersedes_provision_id` chain may have `is_currently_effective = true` at a time.

## LegalProvisionRuleLink

The association between a `LegalProvision` and an existing LegalLense `rule_id`
(spec FR-006). This is what makes deterministic retrieval (FR-009) possible — it is
the join table between the legal corpus and `ComplianceEngine`'s existing vocabulary.

| Field | Type | Notes |
|---|---|---|
| `rule_id` | string | Must match an existing `ComplianceEngine` rule_id exactly — see note below |
| `field` | string \| null | The specific field within the rule (e.g. `maximum_retail_price_mrp`); `null` means rule-level, not field-level |
| `provision_id` | string (FK → LegalProvision) | The provision this rule/field maps to |

**Validation rules**: `rule_id` values are drawn from the existing, already-defined
set in `backend/rules/legal_metrology_rules_2011.json` and
`backend/services/compliance_engine.py` (`LMPC-R6-MANDATORY-DECLARATIONS`,
`LMPC-R24-WHOLESALE-DECLARATIONS`, and the three currently-out-of-required-scope
rule_ids) — **this data model does not define a new rule_id vocabulary; it reuses the
existing one verbatim**, satisfying FR-006 and avoiding a second, drifting source of
truth. `field` values, when present, are drawn from the existing field-key vocabulary
already produced by `structured_extraction.py`/`compliance_engine.py`'s
`required_fields`. One `(rule_id, field)` pair may link to more than one
`LegalProvision` (e.g. one rule addressed by several sub-rules).

## LegalBasisResult

The output of one legal-basis request for one compliance finding (spec FR-014–FR-016).
This is a response shape, not a persisted entity — it is computed on request, not
stored (though nothing in this model precludes caching it later).

| Field | Type | Notes |
|---|---|---|
| `rule_id` | string | The finding's rule_id, echoed back |
| `field` | string \| null | The finding's field, echoed back |
| `status` | enum: `found` \| `not_available` \| `low_confidence` | (FR-012, FR-013) |
| `provisions` | list of `LegalProvision` (with embedded `LegalSourceDocument` citation) | Empty when `status != found` |
| `retrieval_method` | enum: `deterministic_link` \| `semantic_fallback` | (FR-009, FR-010) |
| `confidence` | number \| null | Present only for `semantic_fallback`; `null` for a deterministic link (which has no ambiguity to score) |
| `explanation` | string \| null | Optional (FR-015); when present, always co-rendered with `provisions`, never alone |
| `explanation_source` | string \| null | Identifies what generated `explanation`, when present (FR-016 — must be labeled as AI-assisted) |

**Validation rules** (the structural expression of the constitution's Principle III
and this spec's User Story 3): if `status != found`, `provisions` MUST be empty and
`explanation` MUST be `null` — there is no code path that allows a "not available" or
"low confidence" result to simultaneously carry provision text or a citation. This is
the single most important invariant in this data model.

## Relationship to existing LegalLense data (read-only; nothing below is modified by this feature)

- `ComplianceEngine`'s `RuleResult`/`FieldResult` (`backend/models/compliance.py`) —
  read only, to obtain the `rule_id`/field a `LegalBasisResult` is requested for.
  Never written to.
- `productRevisions` (MongoDB, existing) — a `LegalBasisResult` may eventually be
  attached as a new, optional, additive field on a revision's `complianceResult`
  rendering (not on the stored document itself, unless a caching decision is made
  later) — out of this plan's Phase 1 scope to decide; noted for `/speckit-tasks`.
