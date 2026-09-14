# Contract: Legal Knowledge Assistant Endpoint

**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md)

Documentation of the interface this feature exposes, matching the existing project's
router conventions (`backend/api/official.py`, `backend/api/legal_basis.py` — a plain
`APIRouter`, a Pydantic request/response model, no framework beyond what's already
used). **Not implemented by this plan** — this is the contract `/speckit-tasks` and
implementation work against, following the same review/test/benchmark discipline as
every other endpoint in this project, and only after research.md's technology
benchmark (§1, §4) concludes with something to implement.

## `POST /api/official/legal-assistant/ask`

Read-only with respect to every existing authoritative data model (spec FR-003) —
`POST` only because a free-form question is naturally a request body, not because
this endpoint writes anything. Requires `official_user` (`backend/api/auth.py`'s
existing role dependency, the same one `backend/api/official.py` already uses) — no
new authentication concept, and explicitly **not** reachable by `manufacturer_user`
or an unauthenticated request (spec FR-001, FR-002). Routed under the existing
`/api/official` prefix precisely because it is official-only, matching
`backend/api/official.py`'s own router.

**Request body** (`LegalQuery`, see [../data-model.md](../data-model.md)):

```json
{
  "question_text": "What does Rule 6(11) require regarding unit sale price?",
  "case_context": null
}
```

`case_context`, when present, is a `productRevisions.revisionId` the question is
being asked about (User Story 2) — it is echoed back for display/audit only and is
**never** read by retrieval/generation (spec FR-024; see data-model.md's validation
rules for `LegalQuery`).

**Response** (`LegalAssistantAnswer`, see [../data-model.md](../data-model.md)) —
the answered case:

```json
{
  "query_id": "...",
  "status": "answered",
  "answer_text": "Rule 6(11), as substituted by GSR 226(E), requires the unit sale price to be declared in rupees, rounded to the nearest two decimal places, expressed per gram/kilogram, centimetre/metre, millilitre/litre, or per unit depending on how the commodity is sold.",
  "citations": [
    {
      "provision_id": "LMPC-R6-11",
      "rule_sub_rule_clause": "Rule 6(11)",
      "text": "The unit sale price in rupees, rounded off to the nearest two decimal place, shall be declared on every pre-packaged commodity in the following manner...",
      "effective_status": "amended",
      "is_currently_effective": true,
      "source": {
        "source_id": "IN-LM-PCR-2011-GSR226E-2022",
        "title": "The Legal Metrology (Packaged Commodities) Rules, 2011 - as amended by GSR 226(E)",
        "citation": "GSR 226(E), dated 28th March 2022...",
        "version": "2022-03-28-amendment"
      }
    }
  ],
  "ambiguous_candidates": null,
  "generated_by": null
}
```

(`generated_by` is `null` only in this illustrative example — a real implementation
that includes a generation step per research.md §4 MUST populate it, per FR-014, once
that step exists; this contract does not fix what value it holds since the generation
technology is not yet chosen.)

**Response** — the insufficient-evidence case (same 200 status, not an error; an
expected, first-class outcome per spec FR-015, exactly matching `001-legal-rag`'s own
`status: "not_available"` precedent):

```json
{
  "query_id": "...",
  "status": "insufficient_evidence",
  "answer_text": null,
  "citations": [],
  "ambiguous_candidates": null,
  "generated_by": null
}
```

**Response** — the explicit-disambiguation case (spec FR-017; same 200 status):

```json
{
  "query_id": "...",
  "status": "insufficient_evidence",
  "answer_text": null,
  "citations": [],
  "ambiguous_candidates": [
    { "provision_id": "LMPC-R6-1-A", "rule_sub_rule_clause": "Rule 6(1)(a)", "...": "..." },
    { "provision_id": "LMPC-R24-A", "rule_sub_rule_clause": "Rule 24(a)", "...": "..." }
  ],
  "generated_by": null
}
```

A question genuinely ambiguous between two rule_ids is treated as a variant of
"insufficient evidence to give ONE confident answer," not as a third status value —
`ambiguous_candidates` carries the distinct options rather than the endpoint silently
picking one (FR-017). Exact status-modeling (a third enum value vs. this
`ambiguous_candidates`-on-`insufficient_evidence` shape) is confirmed at
`/speckit-tasks`, not fixed here.

**Error responses**: `401`/`403` for missing/wrong-role authentication (FR-001,
FR-002) — the actual access-control enforcement, not merely a hidden UI element.
`422` for a malformed request (e.g. empty `question_text`). No `5xx` path should ever
surface a fabricated or plausible-wrong result — a downstream retrieval/generation
failure degrades to `status: "insufficient_evidence"`, consistent with User Story 6
(existing behavior preserved when this feature is unavailable), never a hard error
that could make a research session fail ungracefully, and never a silently-degraded
"best guess" presented as confident.

## `GET /api/official/legal-assistant/history` (optional, supports FR-028's audit requirement)

Read-only, `official_user`-gated, same pattern as `backend/api/official.py`'s
`review-queue` listing. Returns `QueryInteractionRecord` entries
(see [../data-model.md](../data-model.md)) — confirmed necessary, and its exact
filter/pagination shape decided, at `/speckit-tasks`, not fixed here; listed as a
candidate endpoint because spec FR-028 requires the audit capability to exist
*somewhere*, and this is the natural read surface for it if a dedicated UI view is
built.

## Non-goals of this contract (reiterated from the spec's Non-Goals)

- No endpoint in this feature ever accepts compliance-decision content — there is no
  `POST`/`PUT`/`PATCH` anywhere in this contract that writes to `complianceResult`,
  `officialDecision`, or `decisionHistory`.
- No endpoint in this feature is reachable without `official_user` — no manufacturer
  or public/anonymous path exists (spec Non-Goals: no manufacturer chatbot, no public
  legal chatbot).
- This contract does not replace, extend, or alias `001-legal-rag`'s
  `GET /api/compliance/legal-basis` — that endpoint's exact-`rule_id`+field contract
  is untouched; this is a wholly separate route (`/api/official/legal-assistant/...`),
  reflecting that this feature answers a structurally different question (a free-form
  question, not "what supports this specific already-known finding").
