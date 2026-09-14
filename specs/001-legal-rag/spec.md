# Feature Specification: Legal RAG (Legally Grounded Knowledge Layer)

**Feature Branch**: `001-legal-rag` (directory only — no git branch created for this specification step)

**Created**: 2026-09-14

**Status**: Draft

**Input**: User description: "A legally grounded knowledge layer that allows LegalLense to retrieve and present authoritative legal provisions supporting compliance findings, governed by the LegalLense constitution, scoped around the current system, prioritizing LMPC-R6 then LMPC-R24."

**Governing Authority**: `.specify/memory/constitution.md` (v1.0.0). This specification is written to be consistent with every principle in that document, in particular Principles I (Deterministic Compliance Authority), II (Official Human Authority), III (No Hallucinated Legal Requirements), IV (Structural Separation of AI Output from Authoritative Decisions), and V (Evidence Provenance). See "Constitution Alignment" below for an explicit mapping.

## Core Boundary *(non-negotiable — restated from the constitution for this feature)*

- The existing deterministic `ComplianceEngine` remains the sole authority for `PASS`/`FAIL`/`REVIEW_REQUIRED` (and every other compliance status value) and for `compliance_score`. This feature never computes, overrides, or implies any of those values.
- RAG retrieval and any generated explanation are an **evidence, legal-basis, and explanation layer only** — never a decision-maker.
- The Official remains the final human decision-maker; RAG output is contextual input to their review, never a substitute for it.
- No legal requirement, citation, or explanation may be presented without traceable provenance to a verified source. A missing or low-confidence result is surfaced as "not found," never filled in with a plausible-sounding guess.
- AI-generated explanatory text is never treated as legal authority — the authority is the underlying source document/provision it cites, and the citation must always be visible alongside the explanation.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Retrieve legal basis for an LMPC-R6 finding (Priority: P1)

A user reviewing a compliance report (Official or Manufacturer) for a finding under `LMPC-R6-MANDATORY-DECLARATIONS` (e.g. a missing `maximum_retail_price_mrp` declaration) can request the legal basis for that specific finding and receive the applicable provision text, its rule/sub-rule reference, and its verified source — the same rule this project's `ComplianceEngine` already evaluates today, and the highest-traffic rule in the system (evaluated on every default `e_commerce_product_listing` submission).

**Why this priority**: `LMPC-R6-MANDATORY-DECLARATIONS` is the only rule evaluated on every manufacturer self-check and every official scan today — it is the rule with by far the most real-world findings, and therefore the rule where a legal-basis explanation delivers the most value fastest. Without this working end-to-end for at least one rule, the feature delivers no real value.

**Independent Test**: Can be fully tested by requesting the legal basis for a known `LMPC-R6` sub-field finding (e.g. `maximum_retail_price_mrp`) and verifying the response contains a specific rule/sub-rule reference, source citation, and provenance — independent of any other rule, of the Official Review UI, or of any other user story below.

**Acceptance Scenarios**:

1. **Given** a compliance finding exists for `LMPC-R6-MANDATORY-DECLARATIONS` on field `maximum_retail_price_mrp`, **When** its legal basis is requested, **Then** the system returns the applicable provision text, a specific rule/sub-rule reference (not just the rule-level ID), the source document/citation, and provenance metadata identifying the verified source and its version.
2. **Given** a compliance finding exists for any of the other seven `LMPC-R6` fields (manufacturer/packer/importer details, country of origin, generic name, net quantity, mfg date, unit sale price, consumer care details), **When** its legal basis is requested, **Then** the system returns a result linked to that specific field, not a generic rule-level summary only.
3. **Given** a legal-basis result is returned, **When** it is inspected, **Then** every piece of legal text present is traceable to a specific verified source document and provision — nothing in the response is unsourced free text presented as law.

---

### User Story 2 - Retrieve legal basis for an LMPC-R24 finding (Priority: P2)

A user reviewing a wholesale-package compliance finding under `LMPC-R24-WHOLESALE-DECLARATIONS` (e.g. a missing `identity_of_commodity` declaration) can request its legal basis through the same mechanism as User Story 1.

**Why this priority**: Second-highest-value rule after `LMPC-R6` — evaluated on the `wholesale_package` validation profile, a real but lower-traffic production path than the default e-commerce profile. Reuses the same retrieval mechanism as User Story 1 rather than introducing a second one, so it is lower additional effort once US1 exists, but is still explicitly a distinct, separately verifiable increment because it depends on a second rule's worth of sourced legal text existing in the corpus.

**Independent Test**: Can be fully tested by requesting the legal basis for a known `LMPC-R24` finding (e.g. `identity_of_commodity`) and verifying the same quality bar as User Story 1 (specific reference, source, provenance) — independently of whether `LMPC-R6` legal text has also been acquired, though in practice US1's corpus work is expected to land first per the priority above.

**Acceptance Scenarios**:

1. **Given** a compliance finding exists for `LMPC-R24-WHOLESALE-DECLARATIONS` on any of its three fields (name and address of manufacturer or packer, identity of commodity, total number of retail packages or net quantity), **When** its legal basis is requested, **Then** the system returns the applicable provision text, rule/sub-rule reference, source, and provenance, to the same standard as User Story 1.
2. **Given** legal text for `LMPC-R24` has not yet been acquired/verified (e.g. because corpus work has prioritized `LMPC-R6` first, per the stated priority), **When** its legal basis is requested, **Then** the system returns an explicit "not available" result rather than a degraded, fabricated, or borrowed-from-a-different-rule answer.

---

### User Story 3 - Handle unverified or missing legal sources without fabrication (Priority: P1)

When a legal-basis request cannot be satisfied by a verified source — because the source hasn't been acquired yet, because retrieval confidence is too low, or because the finding doesn't map to any known provision — the system says so explicitly rather than inventing or approximating an answer.

**Why this priority**: This is not an optional feature increment; it is the safety floor the rest of the feature depends on. Every other user story's acceptance scenarios assume this behavior holds. It must be verified independently and explicitly, not merely assumed to follow from "the happy path works."

**Independent Test**: Can be fully tested by requesting legal basis for (a) a finding whose rule has no corpus entry at all, and (b) a finding whose best retrieval match falls below whatever confidence bar the system uses — in both cases verifying the response is a clearly labeled "no verified legal basis available" result, never a citation, never generated legal-sounding text, and never a silent empty success.

**Acceptance Scenarios**:

1. **Given** a compliance finding whose rule_id has no corresponding entry in the legal knowledge corpus, **When** its legal basis is requested, **Then** the system returns an explicit "not available" indication and does not return any provision text, citation, or explanation.
2. **Given** a compliance finding whose rule_id exists in the corpus but semantic retrieval returns only low-confidence matches, **When** its legal basis is requested, **Then** the system does not present the low-confidence match as if it were a confident answer — it is either withheld with a "low confidence / not available" indication, or presented with its confidence/uncertainty visibly attached, never silently upgraded to a definitive-sounding citation.
3. **Given** any legal-basis response that includes explanatory text generated from retrieved content, **When** that response is inspected, **Then** the explanation is never presented without the source citation it was grounded in visibly attached — an explanation can never appear "bare."

---

### User Story 4 - Show legal basis to an Official during case review (Priority: P2)

An Official reviewing a case in the existing Official Review Workflow (`case-details.html`) can see the legal basis for a compliance finding alongside that finding, presented so it is visibly distinguishable from the finding itself, from any AI-suggested field value, and from the Official's own decision.

**Why this priority**: This is the feature's actual point of human contact — legal-basis data that exists but is never seen by an Official during review delivers no real value. Placed at P2 (not P1) because it is additive presentation on top of User Stories 1–3's retrieval capability, which must exist and be safe first; showing an unsafe or unverified result would be worse than not showing one at all.

**Independent Test**: Can be fully tested by opening an existing case-review context for a finding with an available legal basis and verifying the legal-basis content is visible, clearly labeled as legal/reference information (not as part of the compliance finding's own deterministic status, and not as part of the decision-recording controls), and that it renders correctly as read-only/contextual information with no control that could feed it back into the finding or the decision.

**Acceptance Scenarios**:

1. **Given** an Official is reviewing a case with a compliance finding that has an available legal basis, **When** they view the case, **Then** the legal basis (provision text, reference, source, citation) is visible alongside the finding, visually and structurally distinct from the finding's own status/value and from the decision-recording controls.
2. **Given** an Official is reviewing a case with a compliance finding that has no available legal basis, **When** they view the case, **Then** the case review experience is otherwise unaffected — the absence of a legal basis does not block, degrade, or alter the Official's ability to review the finding and record a decision as they already can today.
3. **Given** an Official views a legal basis alongside a finding, **When** they interact with it, **Then** there is no control that writes the legal-basis content into the finding, the compliance result, or the decision record — it is read-only, contextual information only.

---

### User Story 5 - RAG output can never change compliance status (Priority: P1)

Regardless of what legal basis is retrieved, generated, or displayed for a finding, the finding's own `ComplianceEngine`-computed status (`PASS`/`FAIL`/`REVIEW_REQUIRED`/etc.) and the overall `compliance_score` are unaffected before, during, and after any legal-basis retrieval.

**Why this priority**: This is the feature's central constitutional boundary (Principle I). It must be independently, explicitly verifiable — not inferred from the fact that no code path happens to connect the two today.

**Independent Test**: Can be fully tested by recording a finding's compliance status and score, performing one or more legal-basis retrievals for that finding (including a failed/low-confidence retrieval), and verifying the status and score are byte-for-byte identical before and after, and that no retrieval outcome (success, failure, low confidence, error) has any code path capable of writing to compliance status or score.

**Acceptance Scenarios**:

1. **Given** a finding with a known compliance status and score, **When** its legal basis is successfully retrieved and displayed, **Then** the finding's status and score are unchanged.
2. **Given** a finding with a known compliance status and score, **When** its legal-basis retrieval fails, times out, or returns "not available," **Then** the finding's status and score are unchanged (the failure has no effect on the deterministic result).
3. **Given** the legal-basis retrieval mechanism is entirely unavailable (see User Story 6), **When** a finding is evaluated by `ComplianceEngine`, **Then** the finding's status and score are computed exactly as they are today, with zero dependency on the legal-basis mechanism's availability.

---

### User Story 6 - Existing behavior is fully preserved when RAG is unavailable (Priority: P1)

If the legal-basis/RAG mechanism is disabled, unreachable, or fails for any reason, every existing LegalLense flow — OCR, structured extraction, compliance evaluation, the Gemini fallback, evidence rendering, the Manufacturer self-check flow, and the Official Review Workflow — continues to function exactly as it does today, with no error, degradation, or behavior change visible to a user who isn't looking for legal-basis information specifically.

**Why this priority**: This is the feature's non-regression guarantee and a direct expression of constitutional Principle X (Architectural Conservatism). It is the precondition that makes it safe to ship this feature incrementally at all.

**Independent Test**: Can be fully tested by disabling/removing the legal-basis mechanism entirely and running the complete existing test suite plus a full manual walkthrough of the Manufacturer self-check flow and the Official Review Workflow, verifying zero change in behavior, output, or test results compared to the pre-feature baseline (289 passed / 1 skipped, per the current suite).

**Acceptance Scenarios**:

1. **Given** the legal-basis mechanism is disabled or unreachable, **When** a manufacturer submits a self-check, **Then** they receive the exact same `complianceResult` shape and content as today, with no error and no missing/broken UI element.
2. **Given** the legal-basis mechanism is disabled or unreachable, **When** an Official reviews a case and records a decision, **Then** the entire existing Official Review Workflow (queue, case detail, decision recording, decision history) behaves identically to today.
3. **Given** the legal-basis mechanism is disabled or unreachable, **When** the existing automated test suite is run, **Then** it passes with the same result as the current baseline (289 passed, 1 skipped), with zero test modified, skipped, or removed to achieve this.

---

### Edge Cases

- What happens when a finding's `rule_id` matches a corpus entry but the specific sub-field (e.g. one of `LMPC-R6`'s eight fields) has no distinctly sourced provision, only a rule-level one? → The system must not silently present the rule-level provision as if it specifically addressed the sub-field; it must be clearly scoped to what was actually matched.
- What happens when the same legal provision has been amended, and both an original and amended version exist in the corpus? → The system must surface the currently effective version by default and make clear that an amendment exists, never silently mixing original and amended text into one presented answer.
- What happens when a manufacturer's revision predates this feature entirely (no `reviewStatus`/legal-basis-related fields present)? → Treated identically to "no legal basis available" — never an error, never retroactively backfilled with fabricated content.
- What happens when retrieval succeeds but the underlying source document's provenance/version cannot be confirmed (e.g. a corpus entry with incomplete metadata)? → Treated as "not available" for citation purposes — an unverifiable source is not a verified source, regardless of how well it matches semantically.
- What happens when a compliance finding covers a rule_id outside the two prioritized in this spec (`LMGEN-R12`, `LMGEN-R14`, `LMPC-R11`)? → Returns "not available" until/unless corpus coverage is explicitly extended to those rules in a future increment; this spec does not require coverage beyond `LMPC-R6` and `LMPC-R24`.
- What happens under concurrent/high-volume legal-basis requests? → Out of scope for this specification's functional requirements (see Non-Goals and Success Criteria for the latency bar that does apply); no specific concurrency behavior is mandated beyond not degrading the existing system's own performance (User Story 6).

## Requirements *(mandatory)*

### Legal-Source Acquisition & Verification

- **FR-001**: The system's legal knowledge MUST originate only from verified, authoritative sources for the Legal Metrology (Packaged Commodities) Rules, 2011, and its relevant, verified amendments/notifications — never from paraphrase, memory, or unverified web content.
- **FR-002**: Every acquired legal-source unit MUST record which specific rule/sub-rule/clause it corresponds to, sufficient to answer "which exact provision does this text represent" without ambiguity.
- **FR-003**: Where a provision has been amended, the system MUST be able to distinguish original text from amended text, and identify which is currently effective.
- **FR-004**: Legal-source acquisition MUST be a distinct, auditable step, separable from and completed before any retrieval or embedding step depends on it — this specification does not assume the corpus already exists.

### Legal Knowledge Corpus

- **FR-005**: The system MUST maintain a structured representation of legal provisions, each unit carrying at minimum: a stable identifier, its rule/sub-rule/clause reference, its source document and citation, its effective-date/amendment status, and its provenance (where it was verified from).
- **FR-006**: Each corpus unit MUST be linkable to the existing LegalLense rule_id vocabulary already used by `ComplianceEngine` (`LMPC-R6-MANDATORY-DECLARATIONS`, `LMPC-R24-WHOLESALE-DECLARATIONS`, and — out of this spec's required scope but not precluded — `LMGEN-R12-VERIFICATION-INTERVALS`, `LMGEN-R14-STAMPING-SEALING`, `LMPC-R11-NET-QUANTITY-EXCLUSION`), and, where applicable, to the specific field within that rule (e.g. `maximum_retail_price_mrp`).
- **FR-007**: The corpus MUST be versioned or otherwise identifiable such that "which version of the legal source produced this citation" is always answerable after the fact.
- **FR-008**: Corpus coverage for this specification's required scope is `LMPC-R6-MANDATORY-DECLARATIONS` (all eight fields) first, then `LMPC-R24-WHOLESALE-DECLARATIONS` (all three fields) second. Coverage of other rule_ids is explicitly out of required scope (see Non-Goals) but the corpus structure must not preclude adding them later.

### Retrieval

- **FR-009**: Given a compliance finding's `rule_id` (and, where applicable, its specific field), the system MUST first attempt a deterministic linkage to corpus entries tagged with that exact `rule_id`/field, before falling back to any broader search.
- **FR-010**: Semantic or similarity-based retrieval MAY be used as a supporting or fallback mechanism when deterministic linkage is insufficient (e.g. multiple candidate provisions, or a query that doesn't map to one exact tag), but is never the primary mechanism when an exact `rule_id` linkage exists.
- **FR-011**: Every retrieval result presented to a user MUST include the source reference(s) it came from — a result with legal-sounding content but no attached source reference must never be presented.
- **FR-012**: When retrieval confidence is low (however "low" is ultimately defined at implementation time), the system MUST NOT fabricate, guess, or present a low-confidence match as a confident answer — it is withheld or explicitly marked as low-confidence/uncertain.
- **FR-013**: Retrieval MUST function correctly (return "not available," not an error) when the corpus has zero coverage for a given rule/field, not only when coverage is partial.

### Legal-Basis Output

- **FR-014**: For a compliance finding with an available legal basis, the system MUST be able to produce, at minimum: the legal provision text, its rule/sub-rule reference, its source, its source provenance, the linked LegalLense rule_id, and the linked field (where applicable).
- **FR-015**: The system MAY optionally produce a grounded explanation in addition to the raw provision text, but any such explanation MUST be presented together with, never separately from, its source citation.
- **FR-016**: A generated explanation MUST be visibly and structurally labeled as AI-assisted/explanatory content, distinct from the verified provision text it is grounded in.

### Official Review Integration

- **FR-017**: An Official reviewing a case MUST be able to view the legal basis for a compliance finding in that case, presented alongside (not merged into) the finding itself.
- **FR-018**: Legal-basis content shown to an Official MUST be read-only/contextual — no control may allow it to be edited, or to write into the compliance finding, the `ComplianceEngine` output, or the Official's own decision record.
- **FR-019**: Legal-basis content MUST be visually and structurally distinguishable from the Official's decision-recording controls and from the decision history/timeline, so an Official can never mistake a legal citation for a decision, or vice versa.
- **FR-020**: The absence of a legal basis for a given finding MUST NOT block, degrade, or alter an Official's ability to review that finding and record a decision using the existing Official Review Workflow.

### Manufacturer/Report Integration

- **FR-021**: Existing report pages (compliance report, self-check report, case detail) MUST continue to render their current explanations exactly as today; this feature only ever adds an additional, clearly separate "Legal Basis" section/element, never replacing or altering existing explanation text.
- **FR-022**: Adding a legal basis to a report MUST NOT alter that report's `complianceResult`, `overall_status`, `compliance_score`, or any other existing field's value.
- **FR-023**: A report for a finding with no available legal basis MUST render identically to how it renders today (no broken section, no error state visible to the end user beyond an unobtrusive "not available" indication where the section would go).

### Security & Trust

- **FR-024**: No legal claim may be presented to any user without traceable provenance to a verified source, without exception.
- **FR-025**: No secret, credential, or API key related to this feature's implementation may ever be committed to source control; any such credential lives only in the existing gitignored `.env` mechanism, following the same convention already used for the Gemini integration.
- **FR-026**: Provenance information (source, version, citation) MUST be preserved end-to-end from corpus entry through retrieval to final display — never dropped at any intermediate step.
- **FR-027**: No component introduced by this feature may write to any field that `ComplianceEngine` or the Official Review Workflow treats as authoritative (compliance status/score, `officialDecision`, `decisionHistory`).
- **FR-028**: Every legal source represented in the corpus MUST carry an identifiable version/acquisition-date, so that a citation can always be traced back to exactly which source snapshot produced it.

### Performance & Deployment

- **FR-029**: The feature MUST operate within the project's existing Python 3.11 environment and MUST NOT require a runtime the current deployment model (manual venv + `uvicorn`, no containerization) cannot support.
- **FR-030**: Components selected at implementation time SHOULD be capable of running on CPU, consistent with the project's current deployment hardware (confirmed CPU-only, no discrete GPU, ~16GB RAM) — this is a strong preference, not itself a technology lock, and is a planning-time (`/speckit-plan`) decision, not a specification-time one.
- **FR-031**: No dependency may be introduced into the project's pinned environment without following the existing `--no-deps` + `constraints.txt` discipline already used for OCR-related packages; this specification does not itself add any dependency.
- **FR-032**: Embedding/indexing of the legal corpus SHOULD be performable as an offline/batch step, not required synchronously on every user request.
- **FR-033**: An externally-hosted vector database MAY be used; this specification does not mandate self-hosting or preclude a managed service, and does not select a specific one.

## Key Entities *(this feature introduces new data concepts)*

- **Legal Source Document**: A verified, authoritative legal text (e.g. the Legal Metrology (Packaged Commodities) Rules, 2011, or a specific amendment/notification) — carries an identifiable version, acquisition/verification date, and original citation.
- **Legal Provision**: One structured unit of legal text extracted from a Legal Source Document — carries a rule/sub-rule/clause reference, the provision text itself, amendment status (original vs. amended, and which is effective), and a link to the Legal Source Document it came from.
- **Legal Provision ↔ LegalLense Rule Link**: The association between a Legal Provision and an existing `ComplianceEngine` `rule_id` (and, where applicable, a specific field within that rule) — this is what makes deterministic rule_id-first retrieval possible.
- **Legal Basis Result**: The output of a legal-basis request for one compliance finding — carries the matched Legal Provision(s) (or an explicit "not available" state), the retrieval method used (deterministic link vs. semantic match) and its confidence, an optional grounded explanation, and full provenance back to the Legal Source Document.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (Source correctness)**: 100% of legal provisions presented to any user are traceable to a specific, identifiable verified source document and citation — zero instances of unsourced legal text in any sampled output.
- **SC-002 (Retrieval precision)**: For compliance findings under `LMPC-R6` and `LMPC-R24` with an available legal basis, the correct provision is returned as the top result in at least 90% of a representative sample of finding types (all eight `LMPC-R6` fields and all three `LMPC-R24` fields).
- **SC-003 (Citation correctness)**: In 100% of sampled legal-basis results that include a citation, the cited rule/sub-rule reference matches the actual content of the returned provision text (no mismatched citations).
- **SC-004 (Groundedness)**: In 100% of sampled results that include a generated explanation, every factual/legal claim in that explanation is directly supported by the attached source provision text — zero claims present in the explanation that are absent from the cited source.
- **SC-005 (Wrong-rule retrieval)**: Less than 5% of a representative sample of legal-basis requests return a provision associated with a different `rule_id` than the one requested, when deterministic linkage is available.
- **SC-006 (Missing-source behavior)**: 100% of legal-basis requests for findings with no corpus coverage return an explicit "not available" result — zero fabricated or guessed answers in this condition, verified over a representative sample including all rule_ids outside `LMPC-R6`/`LMPC-R24`.
- **SC-007 (Latency)**: A legal-basis lookup for a finding with an available deterministic rule_id link completes fast enough not to be the dominant wait time in an Official's case-review workflow (i.e., perceptibly near-instant relative to the existing page load, not a multi-second blocking wait) under normal operating conditions.
- **SC-008 (Regression)**: 100% of the existing automated test suite continues to pass at its current count (289 passed, 1 skipped) with the legal-basis feature both enabled and disabled, and zero existing test is modified, skipped, or removed to achieve this.
- **SC-009 (Compliance-status immutability)**: Across a representative sample of findings, 0% show any change in `overall_status` or `compliance_score` attributable to a legal-basis retrieval, success or failure.

## Non-Goals *(explicit exclusions from this feature)*

- Replacing PaddleOCR or any part of the existing OCR pipeline.
- Modifying OCR accuracy, tuning, or the existing accuracy benchmark baseline.
- Replacing the existing Gemini OCR/vision-AI fallback.
- YOLO integration or any change to the existing (disabled-by-default-in-practice) YOLO localization step.
- Changing `ComplianceEngine`'s decision logic, rule evaluation, or scoring — this feature only ever reads a finding's already-computed `rule_id`/field, never influences how that finding was computed.
- Introducing any autonomous legal decision-making — this feature never decides anything; it only retrieves and presents.
- Broad legal chatbot / open-ended legal Q&A functionality — retrieval is scoped strictly to explaining an existing, specific compliance finding, not answering arbitrary legal questions.
- Redesigning the existing frontend or backend architecture — this feature is additive (new optional fields, a new read-only endpoint or two, a new report section), following the same integration pattern the Official Review Workflow already established, not a rewrite of any existing router, page, or data model.

## Assumptions

- The Legal Metrology (Packaged Commodities) Rules, 2011 and its relevant amendments are the correct and sufficient legal scope for `LMPC-R6` and `LMPC-R24` coverage; broader Legal Metrology (General) Rules, 2011 coverage (for `LMGEN-R12`/`LMGEN-R14`) is a plausible future extension but not required by this spec.
- "Verified source" means a source whose text has been deliberately checked against an authoritative publication (e.g. an official Gazette notification or a recognized legal database), not merely retrieved by automated scraping — the specific verification process is a Phase 1 implementation concern (per the prior RAG audit), not something this specification defines procedurally.
- A finding's `rule_id` and field name (already produced by `ComplianceEngine`/`structured_extraction.py` today) are a sufficient and stable key for linking to the legal corpus; no new finding-identification scheme is required.
- The existing Manufacturer self-check and Official Review Workflow report pages are the delivery surfaces for this feature's output; no new page type is required beyond adding a section to what already exists.
- Confidence thresholds, specific retrieval algorithms, specific embedding/reranking/generation technology, and specific vector-store choice are implementation decisions deferred to `/speckit-plan`, not fixed by this specification.

## Constitution Alignment

| Constitution Principle | How this specification honors it |
|---|---|
| I. Deterministic Compliance Authority | FR-022, FR-027, User Story 5, SC-009 — no requirement in this spec ever computes or writes a compliance status/score. |
| II. Official Human Authority | User Story 4, FR-017–FR-020 — legal basis is always contextual input to an Official's own review, never a decision. |
| III. No Hallucinated Legal Requirements | User Story 3, FR-011–FR-013, FR-024, SC-001, SC-004, SC-006 — every requirement above treats an unsourced or low-confidence answer as "not available," never a guess. |
| IV. Structural Separation of AI Output from Authoritative Decisions | FR-016, FR-019, FR-021 — generated explanations and legal citations are always visibly distinct from `ComplianceEngine`/Official decision content. |
| V. Evidence Provenance | FR-002, FR-005, FR-007, FR-026, FR-028, SC-001, SC-003 — provenance is a first-class, end-to-end requirement, not an afterthought. |
| VI. Accuracy Over Performance | SC-002–SC-006 prioritize correctness/groundedness; SC-007's latency bar is deliberately soft ("not the dominant wait"), not a hard performance target that could pressure accuracy trade-offs. |
| VII. Test + Real-Image Benchmark Discipline | SC-008 mandates the existing suite stays green; Success Criteria above define the equivalent benchmark-style acceptance bar for this feature's own accuracy (retrieval precision, citation correctness, groundedness). |
| VIII. Surgical, Scoped Changes | Non-Goals section explicitly excludes OCR/YOLO/Gemini/`ComplianceEngine`/frontend-architecture changes. |
| IX. Test Integrity | SC-008 explicitly forbids modifying/skipping/removing existing tests to accommodate this feature. |
| X. Architectural Conservatism | User Story 6, FR-029, Non-Goals — the feature is additive only; existing behavior when the feature is absent/unavailable is a first-class, independently tested requirement. |
| XI. Secrets Hygiene | FR-025. |
| XII. Clean Reversion of Rejected Experiments | Not directly a spec-time concern, but the additive-only nature of every FR above (new optional fields/endpoints, nothing rewired) is what makes clean reversion possible if any part of this feature is later rejected. |
