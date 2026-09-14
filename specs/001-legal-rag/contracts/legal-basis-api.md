# Contract: Legal Basis Retrieval Endpoint

**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md)

Documentation of the interface this feature exposes, matching the existing project's
router conventions (`backend/api/compliance.py`, `backend/api/official.py` — a plain
`APIRouter`, a Pydantic request/response model, no framework beyond what's already
used). **Not implemented by this plan** — this is the contract `/speckit-tasks` and
implementation work against, following the same review/test/benchmark discipline as
every other endpoint in this project.

## `GET /api/compliance/legal-basis`

Read-only. Requires no new authentication concept — reuses the existing session-based
auth already present on every other API route (`current_user`, or a role dependency,
to be confirmed at implementation time based on which surfaces call it: likely
reachable by both `manufacturer_user` and `official_user`, since both the Manufacturer
report and the Official case-review page need it, per spec FR-017 and FR-021).

**Query parameters**:

| Name | Required | Notes |
|---|---|---|
| `rule_id` | yes | Must match an existing `ComplianceEngine` rule_id |
| `field` | no | The specific field within the rule, when applicable |

**Response** (`LegalBasisResult`, see [../data-model.md](../data-model.md)):

```json
{
  "rule_id": "LMPC-R6-MANDATORY-DECLARATIONS",
  "field": "maximum_retail_price_mrp",
  "status": "found",
  "retrieval_method": "deterministic_link",
  "confidence": null,
  "provisions": [
    {
      "provision_id": "...",
      "rule_sub_rule_clause": "Rule 6(1)(e)",
      "text": "...",
      "effective_status": "original",
      "is_currently_effective": true,
      "source": {
        "source_id": "IN-LM-PACKAGED-COMMODITIES-RULES-2011",
        "title": "Legal Metrology (Packaged Commodities) Rules, 2011",
        "citation": "...",
        "version": "..."
      }
    }
  ],
  "explanation": null,
  "explanation_source": null
}
```

**"Not available" response** — same 200 status, not an error (a missing legal basis
is an expected, first-class outcome per spec User Story 3, not a failure condition):

```json
{
  "rule_id": "LMGEN-R12-VERIFICATION-INTERVALS",
  "field": null,
  "status": "not_available",
  "retrieval_method": null,
  "confidence": null,
  "provisions": [],
  "explanation": null,
  "explanation_source": null
}
```

**Error responses**: `422` for a malformed/unknown `rule_id` that doesn't match any
value in the existing rule_id vocabulary (a client error — asking about a rule_id that
doesn't exist at all is different from asking about one that exists but has no legal
basis yet). No `5xx` path should ever surface a fabricated result — a downstream
failure (e.g. an unavailable retrieval backend) degrades to `status: "not_available"`,
consistent with User Story 6 (existing behavior preserved when RAG is unavailable),
never a hard error that could make a report page fail to render.

## Non-goals of this endpoint (reiterated from the spec's Non-Goals)

- No endpoint in this feature accepts a write — there is no `POST`/`PUT`/`PATCH` in
  this contract. Legal-basis data is retrieved, never submitted or edited through this
  API.
- No endpoint in this feature can be called with arbitrary free-text legal questions —
  every request is scoped to an existing `rule_id`(+field), never an open query. This
  is the structural enforcement of "no broad legal chatbot functionality" (spec
  Non-Goals).
