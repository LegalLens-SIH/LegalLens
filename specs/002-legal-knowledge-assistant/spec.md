# Feature Specification: Advanced Legal Knowledge Assistant for Officials

**Feature Branch**: `002-legal-knowledge-assistant` (directory only — no git branch created for this specification step, matching the convention established by `001-legal-rag`)

**Created**: 2026-09-14

**Status**: Draft

**Input**: User description: "Advanced Legal Knowledge Assistant for Officials — a separate, read-only, free-form legal-question-answering assistant for Officials, built on top of the existing verified legal corpus, structurally independent from ComplianceEngine/Legal Basis/Official Workflow."

**Governing Authority**: `.specify/memory/constitution.md` (v1.0.0). This specification is written to be consistent with every principle in that document, in particular Principles I (Deterministic Compliance Authority), II (Official Human Authority), III (No Hallucinated Legal Requirements), IV (Structural Separation of AI Output from Authoritative Decisions), V (Evidence Provenance), VIII (Surgical, Scoped Changes), and X (Architectural Conservatism). See "Constitution Alignment" below for an explicit mapping.

**Relationship to `001-legal-rag`**: This is a **separate, additive feature**, not a replacement or modification of the existing deterministic Legal Basis retrieval (`backend/services/legal_retrieval.py`, `backend/api/legal_basis.py`) shipped and checkpointed under `001-legal-rag`. That feature's own evidence-gated evaluation (Phase 8) correctly declined semantic retrieval for the exact-`rule_id`+`field` lookup it performs — a decision this specification does not revisit or reopen. This feature exists because it introduces a **genuinely different, real free-form query use case** (an Official typing an open-ended legal question) that the deterministic mechanism was never designed to serve and structurally cannot serve (it requires an exact key, not natural language). Both features may reuse the same underlying verified legal corpus; neither reads from nor writes to the other's code, data, or output.

## Core Boundary *(non-negotiable — restated from the constitution for this feature)*

- The existing deterministic `ComplianceEngine` remains the sole authority for `PASS`/`FAIL`/`REVIEW_REQUIRED` (and every other compliance status value) and for `compliance_score`. This feature never computes, overrides, or implies any of those values, and never reads a case's compliance result in order to influence its own answer.
- This feature is a **legal research and explanation layer only** — never a decision-maker, never an enforcement mechanism, and never a substitute for `001-legal-rag`'s existing deterministic Legal Basis retrieval (which remains the system of record for "what is the legal basis for *this specific finding*").
- The Official remains the final human decision-maker; every assistant output is contextual input to their own research, never a decision, a recommendation to act, or an instruction.
- No legal claim, citation, or explanation may be presented without traceable provenance to a verified corpus entry. Insufficient evidence is surfaced as a safe refusal, never a plausible-sounding guess.
- A wrong-but-plausible-looking legal citation is treated as a more serious failure than an explicit "I don't have enough verified legal basis to answer that" — the assistant must be tuned to prefer the latter over the former in every ambiguous case.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Official asks a free-form legal question and receives a grounded, cited answer (Priority: P1)

An Official types a natural-language legal question (e.g. "What does Rule 6(11) require regarding unit sale price?", "Which provision covers country of origin?", "Show me the legal provisions related to MRP") into the assistant, unconnected to any specific compliance case, and receives an answer grounded in retrieved verified corpus provisions with exact rule/sub-rule/clause references and source citations — or, when the corpus does not contain sufficient verified evidence to answer confidently, a clearly labeled "insufficient legal basis" response instead of a guess.

**Why this priority**: This is the feature's entire reason for existing — free-form legal question answering is the one capability `001-legal-rag`'s exact-key deterministic retrieval structurally cannot provide. Without this working safely (including safe refusal), the feature delivers no value distinct from what already exists.

**Independent Test**: Can be fully tested by submitting a representative set of free-form questions covering `LMPC-R6`/`LMPC-R24` topics and confirming each either (a) returns a grounded answer whose citation matches a real corpus provision, or (b) returns an explicit insufficient-evidence response — independent of any specific compliance case, the Official Review Workflow, or any other user story below.

**Acceptance Scenarios**:

1. **Given** an Official submits a free-form question whose answer exists in the verified corpus, **When** the assistant processes it, **Then** the response includes a grounded explanation, the exact rule/sub-rule/clause reference(s) it is grounded in, and the source document/citation for each — with the quoted/source legal text visibly distinguished from any generated explanatory wording.
2. **Given** an Official submits a question the verified corpus has no adequate basis to answer, **When** the assistant processes it, **Then** the response is an explicit "insufficient legal basis to answer" indication — never a citation, never generated legal-sounding text presented as fact.
3. **Given** an Official submits a question that is topically similar to two or more distinct provisions (e.g. a "manufacturer name and address" question that could plausibly refer to either `LMPC-R6`'s retail-package provision or `LMPC-R24`'s wholesale-package provision), **When** the assistant processes it, **Then** it either correctly disambiguates using available context, or explicitly presents the distinct candidates rather than silently guessing one and presenting it as the single confident answer.
4. **Given** the same question is submitted twice, **When** both responses are compared, **Then** the cited provision(s) are consistent between the two responses (repeatable retrieval, not response-to-response drift in *which* provisions are cited — see SC-012).

---

### User Story 2 - Official requests legal background for a specific compliance case (Priority: P2)

While reviewing a compliance case in the existing Official Review Workflow, an Official asks the assistant a legal question for context (e.g. "why does this finding matter" or a related free-form question), and the assistant provides an answer grounded in the verified corpus without reading, altering, or being influenced by that case's `complianceResult`, `overall_status`, `compliance_score`, or `officialDecision`.

**Why this priority**: This is the feature's most valuable point of contact with real work — legal research that requires leaving the case-review context to look up separately delivers less value than research available in place. Placed at P2 (not P1) because User Story 1's core retrieval/safety behavior must exist and be proven safe first; contextual placement is additive presentation on top of it.

**Independent Test**: Can be fully tested by opening an existing case, asking the assistant an arbitrary legal question, and confirming (a) a grounded answer or safe refusal is returned exactly as in User Story 1, and (b) the case's own `complianceResult`/`overall_status`/`compliance_score`/`officialDecision` are provably unread and unchanged by the interaction (see User Story 5) — independent of whether the specific case has any bearing on the question asked.

**Acceptance Scenarios**:

1. **Given** an Official is reviewing a compliance case, **When** they ask the assistant a legal question from within that case's review context, **Then** they receive the same grounded-answer-or-safe-refusal behavior as User Story 1, presented in a way that is visually and structurally distinct from the case's compliance findings, the deterministic Legal Basis panel (`001-legal-rag`), and the decision-recording controls.
2. **Given** an Official asks the assistant a question while reviewing a case, **When** the underlying cited legal provisions are inspected, **Then** each is traceable to the same verified corpus entries `001-legal-rag`'s deterministic retrieval uses — no separate, unverified, or inconsistent legal content is introduced by this feature.
3. **Given** an Official uses the assistant while reviewing a case, **When** they finish and return to recording their decision, **Then** the decision-recording flow (`log-case-action.html`, `officialDecision`, `decisionHistory`) is entirely unaffected — no assistant output can be written into it, referenced by it automatically, or block it.

---

### User Story 3 - Official inspects provenance, amendments, and effective-date history (Priority: P2)

An Official asks a question specifically about a provision's history (e.g. "What changed in Rule 6 after the 2022 amendment?") or, for any answer the assistant gives, can inspect the full source provenance — source document identity, version, amendment/effective date, and whether a cited provision is the currently effective one or a superseded original.

**Why this priority**: Legal Metrology provisions are frequently amended (as `001-legal-rag`'s own corpus already demonstrates — several `LMPC-R6` provisions carry `effective_status: "amended"`); an assistant that cannot correctly represent amendment history is not trustworthy for real legal research, but this is additive depth on top of User Story 1's core answer-or-refuse behavior.

**Independent Test**: Can be fully tested by asking a question whose answer involves an amended provision and confirming the response correctly identifies which version is currently effective, and by inspecting any returned citation's full provenance metadata (source, version, acquisition date, amendment relationship) independent of whether the question was asked inside or outside a case-review context.

**Acceptance Scenarios**:

1. **Given** a provision in the corpus has both an original and a currently-effective amended version, **When** an Official asks about it, **Then** the assistant's answer is grounded in the currently effective version by default and, if the amendment history itself was asked about, explicitly distinguishes original from amended text rather than silently blending them.
2. **Given** any assistant answer with a citation, **When** an Official inspects that citation, **Then** they can see the source document's identity, citation string, version/acquisition-date identifier, and (where applicable) which prior source it amends.
3. **Given** an Official asks about a provision the corpus records as amended but whose amendment is not yet in force (a future effective date), **When** the assistant answers, **Then** it does not present the not-yet-effective text as the currently applicable rule without clearly labeling it as such.

---

### User Story 4 - Corpus expansion readiness (Priority: P3)

As the verified legal corpus is expanded beyond `LMPC-R6`/`LMPC-R24` in future work (a separate, independently-approved effort — see Non-Goals), the assistant continues to function correctly against the larger corpus without requiring a redesign of its query/retrieval/citation contract.

**Why this priority**: Lowest priority because it is a forward-looking non-regression property, not a capability required for this feature's own MVP — nothing about corpus size is being expanded by this specification itself.

**Independent Test**: Can be fully tested (once corpus expansion actually happens, in a separate future effort) by confirming the assistant's existing query/answer/citation contract requires no breaking change to serve the larger corpus — not testable meaningfully before then, beyond confirming this specification's requirements below impose no corpus-size ceiling.

**Acceptance Scenarios**:

1. **Given** the verified corpus grows to cover additional rule_ids beyond `LMPC-R6`/`LMPC-R24`, **When** the assistant is queried about the newly covered content, **Then** it answers using the same grounded-citation contract as existing coverage, with no feature-level change required to recognize the new content.

---

### User Story 5 - Assistant output can never change a compliance decision (Priority: P1)

Regardless of what the assistant retrieves, generates, or answers, no compliance finding's `ComplianceEngine`-computed status, no `compliance_score`, and no Official's `officialDecision`/`decisionHistory` are read, written, or influenced by the assistant — including when the assistant is used from within a case-review context, and including in a failure/refusal/error case.

**Why this priority**: This is the feature's central constitutional boundary (Principles I, II, IV) and must be independently, explicitly verifiable, exactly as `001-legal-rag`'s own User Story 5 required for its feature. A feature this close to case-review UI is under a strictly *higher* bar to prove non-interference, not a lower one, because it is conversational and free-form rather than a fixed lookup.

**Independent Test**: Can be fully tested by recording a case's compliance status/score/decision, performing one or more assistant interactions (including a forced refusal and a forced error) for that case, and verifying status/score/decision are byte-for-byte identical before and after, and that no code path in the assistant is capable of writing to any of them.

**Acceptance Scenarios**:

1. **Given** a case with a known compliance status, score, and (if any) recorded decision, **When** an Official asks the assistant any question about it, **Then** all three remain unchanged, regardless of the assistant's answer.
2. **Given** the assistant's retrieval or generation step fails, times out, or returns a refusal, **When** this occurs during a case-review session, **Then** the case's compliance status/score/decision are unaffected and the existing Official Review Workflow continues to function exactly as it does today.
3. **Given** the assistant is entirely unavailable, **When** a compliance finding is evaluated or a decision is recorded, **Then** both happen exactly as they do today, with zero dependency on this feature's availability.

---

### User Story 6 - Existing behavior is fully preserved when the assistant is unavailable (Priority: P1)

If this assistant is disabled, unreachable, or fails for any reason, every existing LegalLense flow — OCR, structured extraction, `ComplianceEngine`, the Gemini fallback, the Manufacturer self-check flow, the Official Review Workflow, and `001-legal-rag`'s existing deterministic Legal Basis retrieval — continues to function exactly as it does today, with no error, degradation, or visible behavior change to anyone not specifically trying to use this assistant.

**Why this priority**: This is the feature's non-regression guarantee and a direct expression of constitutional Principle X, at the same standard `001-legal-rag` already established and shipped.

**Independent Test**: Can be fully tested by disabling/removing this feature entirely and running the complete existing test suite (backend baseline as of the `001-legal-rag` checkpoint: 324 passed, 1 skipped) plus a full manual walkthrough of the Manufacturer self-check flow, the Official Review Workflow, and `001-legal-rag`'s Legal Basis panel, verifying zero change in behavior, output, or test results.

**Acceptance Scenarios**:

1. **Given** this assistant is disabled or unreachable, **When** an Official reviews a case, views its deterministic Legal Basis panel, and records a decision, **Then** every part of that flow behaves identically to today, with no missing or broken UI element attributable to this feature.
2. **Given** this assistant is disabled or unreachable, **When** the existing automated test suite is run, **Then** it passes with the same result as the pre-feature baseline, with zero existing test modified, skipped, or removed to achieve this.

---

### Edge Cases

- What happens when a question's best retrieval match is only tangentially relevant (e.g. mentions a package-labeling concept but not one the corpus actually covers)? → Treated as insufficient evidence, not a confident answer — the same discipline `001-legal-rag`'s own benchmark found necessary (a "close enough" similarity score is not evidence of correctness at this corpus's scale).
- What happens when a question could plausibly be answered by two provisions from *different* rule_ids that share surface vocabulary (the exact confusable-query class `001-legal-rag`'s Phase 8 benchmark identified — e.g. "manufacturer name and address" appearing in both `LMPC-R6` and `LMPC-R24`)? → The assistant must either correctly disambiguate or explicitly present both candidates distinctly labeled by their rule_id — it must never silently present one as the sole confident answer when the query itself was ambiguous between rules.
- What happens when a question refers to a rule_id or legal topic entirely outside the current corpus's verified coverage (e.g. a `LMGEN-R12` question, or an unrelated statute)? → An explicit "not covered by the verified corpus" response, never a fabricated or externally-sourced answer.
- What happens when retrieved candidate provisions conflict (e.g. an original and superseded amended text both surface as candidates)? → The currently-effective version is preferred and used for the answer; the superseded version, if relevant to the question asked, is presented explicitly labeled as superseded, never blended into one undifferentiated answer.
- What happens if the underlying generation step produces wording not actually supported by the retrieved provision text? → This is a groundedness failure and must not reach the Official as a normal answer — see FR-015/FR-016 and SC-007 (groundedness).
- What happens under concurrent/high-volume assistant use? → Out of scope for this specification's functional requirements (see Success Criteria for the latency bar that does apply); this feature must not degrade the OCR pipeline's or `ComplianceEngine`'s performance regardless of its own load (FR-034).
- What happens if a retrieved legal source document's own text contains something that reads like an instruction (e.g. a sentence structured to resemble a system/prompt directive)? → It is treated strictly as inert quoted data, never as an instruction to any component that processes it — see FR-030.

## Requirements *(mandatory)*

### Access & Authorization

- **FR-001**: The assistant MUST be accessible only to authenticated users with the `official` role, using this project's existing authentication/authorization mechanism (`backend/api/auth.py`'s `official_user` dependency, the same one `001-legal-rag`'s Official-side integration and the Official Review Workflow already use) — never a new, separate auth scheme.
- **FR-002**: The assistant MUST NOT be reachable by a `manufacturer`-role session, nor by any unauthenticated request — verified the same way `001-legal-rag`'s own tests verify official-only backend enforcement (a 403 from the API directly, not merely a hidden UI element).
- **FR-003**: The assistant is read-only with respect to every existing data model this project treats as authoritative — it MUST NOT create, modify, or delete any `complianceResult`, `officialDecision`, `decisionHistory`, product/revision record, or user/session record.

### Query & Retrieval (technology-independent)

- **FR-004**: The system MUST accept a free-form natural-language legal question as input, with no requirement that the Official already know or supply an exact `rule_id`/field key (the defining capability gap `001-legal-rag`'s deterministic mechanism cannot fill).
- **FR-005**: Given a question, the system MUST retrieve candidate legal provisions from the verified legal corpus using a retrieval mechanism whose specific technology (embedding model, vector index, reranker, or absence thereof) is an implementation decision deferred to the planning phase — this specification defines retrieval *quality and safety* requirements, not a retrieval *algorithm*.
- **FR-006**: The system MUST be able to rank or otherwise narrow multiple retrieval candidates down to the evidence actually used to construct an answer, in a way that is auditable after the fact (which candidates were retrieved, which were used, with what relevance signal) — the specific ranking/reranking mechanism is a planning-time decision.
- **FR-007**: Retrieval MUST operate only over the verified legal corpus (the same corpus `001-legal-rag` uses, and any future verified extension of it) — never over unverified external content fetched at query time, and never over the compliance ruleset (`backend/rules/`) as if it were legal source text.
- **FR-008**: The system MUST support questions that reference a specific rule/sub-rule/clause by name or number (e.g. "Rule 6(11)"), questions phrased around a topic rather than a citation (e.g. "country of origin requirements"), and questions about change/history (e.g. "what changed after the 2022 amendment") — all three query shapes are required scope, not optional extensions.

### Grounding, Citation & Trust

- **FR-009**: Every answer the system presents as a confident answer MUST be grounded in one or more specific retrieved corpus provisions and MUST include, for each: the exact rule/sub-rule/clause reference, the source document identity and citation, and the source's version/acquisition-date identifier.
- **FR-010**: The system MUST NOT present any legal claim, citation, rule reference, or provision text that does not trace to an actual entry in the verified corpus — inventing, extrapolating, or paraphrasing-as-if-quoting a provision that does not exist in the corpus is prohibited outright, with no exception for "plausible" or "likely correct" content.
- **FR-011**: Where the system produces a generated explanation in addition to quoted provision text, the explanation MUST be visibly and structurally distinguishable from the quoted/source legal text it is grounded in — a reader must be able to tell, without inference, which words are the law and which words are the assistant's own explanatory language.
- **FR-012**: A generated explanation MUST never be presented without the source citation(s) it is grounded in visibly attached in the same response — an explanation can never appear "bare."
- **FR-013**: Every generated explanation's factual/legal content MUST be fully supported by the retrieved provision text it cites — no claim in the explanation may be absent from, or contradict, its cited source (groundedness, mirroring `001-legal-rag` spec's own SC-004 standard, now made load-bearing for this feature since it does include a generation step).
- **FR-014**: The system MUST clearly and visibly label generated/explanatory content as AI-generated assistance, distinct in labeling from the verified quoted provision text.

### Refusal & Safety Behavior

- **FR-015**: When retrieved evidence is insufficient to support a confident, correctly-cited answer, the system MUST return an explicit "insufficient legal basis" response rather than a low-confidence answer presented as if confident — this is a first-class, expected outcome, not an error state.
- **FR-016**: The system MUST prefer returning an insufficient-evidence response over returning a plausible-but-incorrect citation whenever retrieval confidence cannot clearly distinguish the two — per the explicit acceptance-gate standard below (Success Criteria), a wrong-but-plausible citation is a more serious failure than a correct refusal.
- **FR-017**: When a question is genuinely ambiguous between two or more distinct provisions (e.g. spanning two different rule_ids with overlapping vocabulary), the system MUST either correctly disambiguate using available context or explicitly present the distinct candidates as separate, clearly labeled options — never silently collapse them into one confidently-presented answer.
- **FR-018**: The system MUST treat any text retrieved from the legal corpus as inert data only — retrieved legal text MUST NOT be interpreted or executed as an instruction to the retrieval, ranking, or generation components, regardless of its phrasing (prompt-injection resistance from within the corpus's own content).

### Source, Version & Provenance Handling

- **FR-019**: Every citation the system produces MUST carry, at minimum: source document identity, citation string, version/acquisition-date identifier, the specific rule/sub-rule/clause reference, and whether the cited text is the currently effective version.
- **FR-020**: Where a provision has both an original and one or more amended versions in the corpus, the system MUST default to answering from the currently effective version, and MUST make the existence of an amendment/version history visible and inspectable when relevant to the question asked or explicitly requested.
- **FR-021**: The system MUST never silently blend original and amended text of the same provision into one undifferentiated answer.
- **FR-022**: Where the corpus's own provenance metadata for a candidate is incomplete or unverifiable (mirroring `001-legal-rag`'s own corpus-validation discipline), that candidate MUST be excluded from being used as a citation — an unverifiable source is not a usable source, regardless of retrieval relevance.

### Official Review Integration (additive to `001-legal-rag` and the Official Review Workflow)

- **FR-023**: The assistant MUST be reachable both as a standalone research surface (not tied to any specific case) and from within an existing case-review context, without requiring two separate implementations of its core query/answer/citation behavior.
- **FR-024**: When used from within a case-review context, the assistant MUST NOT read that case's `complianceResult`, `overall_status`, `compliance_score`, or `officialDecision` in order to construct its answer — its retrieval is always over the verified legal corpus only, never over case-specific compliance data (this is a stronger boundary than merely "does not write to" those fields — it does not consult them at all).
- **FR-025**: The assistant's interface MUST be visually and structurally distinct from: the compliance findings display, the existing deterministic Legal Basis panel (`001-legal-rag`), the compliance status/score display, and the decision-recording controls (`log-case-action.html`) — no shared container, no merged visual treatment that could cause any of these to be mistaken for one another.
- **FR-026**: No control in the assistant's interface may write assistant output into a compliance finding, `complianceResult`, `officialDecision`, or `decisionHistory` — it is read-only/contextual information only, matching `001-legal-rag`'s own FR-018 standard for its Legal Basis panel.

### Security & Audit

- **FR-027**: All assistant requests MUST be authenticated and authorized per FR-001/FR-002; there is no anonymous or manufacturer-accessible path to this feature.
- **FR-028**: The system SHOULD retain an audit record of assistant interactions sufficient to answer "who asked what, when, and what was cited in response" — consistent with this project's existing `history` collection pattern, without recording any content that would itself become an authoritative decision record.
- **FR-029**: No secret, API key, or credential related to this feature's implementation may ever be committed to source control; any such credential lives only in the existing gitignored `.env`/`.env.example` mechanism, following the exact convention already used for the Gemini integration (constitution Principle XI).
- **FR-030**: The system MUST be resistant to prompt-injection content embedded within retrieved legal source text or within a submitted question — neither can cause the system to disclose data it should not, bypass FR-001/FR-002's access control, or cause any component to treat retrieved text as executable instructions rather than inert quoted content.

### Performance & Deployment

- **FR-031**: The feature MUST operate within the project's existing Python 3.11 environment (`backend/.venv311`) and MUST NOT require a runtime the current deployment model (manual venv + `uvicorn`, no containerization) cannot support.
- **FR-032**: Components selected at planning/implementation time SHOULD be capable of running on CPU, consistent with the project's current deployment hardware, but — unlike `001-legal-rag`'s Phase 1 scope — an externally-hosted service (managed vector database, hosted embedding/generation API) MAY be justified here specifically because this feature's real free-form-query use case is the one `001-legal-rag`'s benchmark found such infrastructure was not yet justified for; any such choice must be benchmark-justified at planning time, not assumed.
- **FR-033**: No dependency may be introduced into the project's pinned environment without following the existing `--no-deps` + `backend/constraints.txt` discipline already used for OCR-related packages; this specification does not itself add any dependency.
- **FR-034**: This feature's query processing, retrieval, and generation MUST run as a separate capability that does not add latency or resource contention to the OCR pipeline, `ComplianceEngine` evaluation, or any existing request path — a slow or heavily-loaded assistant must never make an unrelated compliance scan slower.
- **FR-035**: A single assistant query SHOULD complete within a latency that keeps it usable as an interactive research tool during case review — not required to match `001-legal-rag`'s near-instant deterministic lookup (a different, heavier mechanism is expected here), but not a multi-minute wait either; the specific bar is set in Success Criteria below.

### UI

- **FR-036**: The system MUST provide an Official-only interface presenting, per answer: the question asked, the generated answer (clearly labeled AI-generated), the cited provision(s) with exact rule/sub-rule/clause reference, source/provenance for each citation, and relevant amendment/effective-date information where applicable.
- **FR-037**: The interface MUST NOT be merged with, or visually confusable with, compliance status/score displays, the `001-legal-rag` deterministic Legal Basis panel, or official decision-recording controls (restated from FR-025 as a UI-specific requirement).
- **FR-038**: The interface MUST render a clearly labeled, non-broken state for an insufficient-evidence response — never a blank, error-looking, or ambiguous UI state indistinguishable from a real system failure.

## Key Entities *(this feature introduces new data concepts)*

- **Legal Query**: A single free-form natural-language question submitted by an Official, optionally associated with a specific compliance case for context, always processed against the verified legal corpus only.
- **Retrieval Candidate**: One provision surfaced by the retrieval step as potentially relevant to a Legal Query, carrying a relevance/ranking signal — an intermediate concept, not itself shown to the Official as a final answer.
- **Legal Assistant Answer**: The output of processing one Legal Query — carries either (a) a generated explanation plus one or more grounding citations (each an existing `001-legal-rag` `LegalProvision`/`LegalSourceDocument` reused, not redefined), or (b) an explicit insufficient-evidence result. Structurally never carries, references, or implies a compliance status, score, or decision.
- **Query Interaction Record**: An audit-log-style record of one Legal Query and its Legal Assistant Answer (who asked, when, what was cited) — informational/audit only, never itself an authoritative decision record.
- **Benchmark Query Set**: A representative, adversarial-inclusive set of query/expected-outcome pairs (mirroring `001-legal-rag`'s Phase 8 benchmark methodology) used to gate this feature's retrieval quality before acceptance, classifying every outcome as (A) correct retrieval, (B) plausible-but-wrong retrieval, or (C) correct refusal.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (Top-1 relevance)**: For a representative benchmark of free-form questions with a known correct provision, the correct provision is the top-ranked citation in at least 90% of cases.
- **SC-002 (Top-k recall)**: The correct provision appears within the top 3 retrieved candidates in at least 97% of cases in the same benchmark.
- **SC-003 (Wrong-rule retrieval)**: Fewer than 5% of benchmark questions with a known correct answer result in a top-1 citation from a different rule_id than the correct one.
- **SC-004 (Wrong-field retrieval)**: Fewer than 5% of benchmark questions with a known correct answer result in a top-1 citation from the correct rule_id but the wrong field/sub-rule.
- **SC-005 (Citation correctness)**: In 100% of sampled answers that include a citation, the cited rule/sub-rule reference matches the actual content of the cited provision text — zero mismatched citations.
- **SC-006 (Provenance correctness)**: In 100% of sampled answers, every citation's displayed source/version/effective-date information matches the corpus entry it was actually retrieved from.
- **SC-007 (Groundedness)**: In 100% of sampled answers that include a generated explanation, every factual/legal claim in that explanation is directly supported by the attached cited provision text — zero unsupported claims.
- **SC-008 (Refusal correctness)**: For a representative benchmark of questions with no adequate corpus coverage (including entirely unrelated topics), at least 95% correctly return an explicit insufficient-evidence response rather than a confident-looking wrong answer.
- **SC-009 (Adversarial/confusable-query safety)**: Across a dedicated adversarial benchmark set (near-duplicate cross-rule vocabulary — e.g. `LMPC-R6` vs `LMPC-R24` manufacturer requirements, MRP vs unit sale price, net quantity vs unit quantity, original vs amended Rule 6, and unrelated-law queries expecting no answer), every outcome is classified as (A) correct retrieval, (B) plausible-but-wrong retrieval, or (C) correct refusal — **class (B) outcomes must be less than 5% of this adversarial set**, a materially stricter bar than the general SC-003/SC-004 rate, because a plausible wrong legal citation is treated as this feature's single most serious failure mode.
- **SC-010 (Contradictory/superseded-source handling)**: 100% of sampled answers involving a provision with both an original and a currently-effective amended version correctly ground the answer in the current version, with zero instances of blended original/amended text presented as one undifferentiated answer.
- **SC-011 (Latency)**: A single assistant query returns a complete answer or refusal within a duration that keeps it usable as an interactive research tool during case review (specific numeric target set at planning time, benchmark-justified — not fixed here, matching `001-legal-rag` research.md's own precedent of deferring exact numeric SLAs to implementation-time empirical tuning); it must not be the dominant wait time in a case-review session under normal conditions.
- **SC-012 (Repeatability)**: For a fixed benchmark question set, repeated submission of the same question returns the same cited provision(s) in at least 95% of repeats (retrieval is not required to be bit-for-bit deterministic if a generation step is involved, but *which provisions are cited* must be stable).
- **SC-013 (Regression)**: 100% of the existing automated test suite continues to pass at its current post-`001-legal-rag` checkpoint count (324 passed, 1 skipped) with this feature both enabled and disabled, and zero existing test is modified, skipped, or removed to achieve this.
- **SC-014 (Compliance/decision immutability)**: Across a representative sample of case-review interactions with the assistant, 0% show any change in `overall_status`, `compliance_score`, `officialDecision`, or `decisionHistory` attributable to an assistant interaction, success or failure.

## Non-Goals *(explicit exclusions from this feature)*

- Autonomous legal decisions or autonomous enforcement action of any kind.
- Modifying any compliance result, status, or score — this feature only ever reads the verified legal corpus, never a case's compliance data, to construct an answer (see FR-024).
- Replacing `ComplianceEngine`, its decision logic, rule evaluation, or scoring.
- Replacing or modifying `001-legal-rag`'s existing deterministic Legal Basis retrieval, its corpus file, its API, or its frontend integration — this feature is additive and reuses that corpus, never forks or supersedes it.
- Replacing PaddleOCR, the OCR pipeline, or its existing accuracy benchmark baseline.
- Replacing the existing Gemini OCR/vision-AI fallback.
- YOLO integration or any change to the existing localization step.
- A Manufacturer-facing chatbot or any Manufacturer access to this feature (Official-only, per FR-001/FR-002).
- An unrestricted, public-facing, or consumer-facing legal chatbot — scope is strictly Legal Metrology (Packaged Commodities) content already verified into the corpus, for authenticated Officials only.
- Legal advice to consumers or any party outside the Official role.
- Automatic case disposition or any action that closes, escalates, or otherwise progresses a case's review state.
- Expanding the verified legal corpus itself beyond what `001-legal-rag` already established (`LMPC-R6`/`LMPC-R24`) — corpus expansion is a separate, independently-approved acquisition effort (see User Story 4), not part of this specification's required scope.
- Mandating any specific embedding model, vector database, reranker, or generation model — per explicit instruction, technology selection is a planning-time, benchmark-driven decision, not fixed here.

## Assumptions

- The existing verified legal corpus (`backend/legal_corpus/legal_metrology_packaged_commodities_2011.json`) and its `LegalProvision`/`LegalSourceDocument` data model (`backend/models/legal.py`) are reused as this feature's retrieval corpus and citation data shape — this feature does not define a second, parallel corpus format.
- `001-legal-rag`'s Phase 8 benchmark conclusion (semantic retrieval adds no value for exact-key retrieval at this corpus's scale) does not transfer to this feature's free-form-query use case, which is a genuinely different query shape that exact-key lookup cannot serve at all — this feature's own technology choice must be benchmarked independently at planning time, not inherited from that conclusion or assumed to repeat it.
- The existing Official authentication/session mechanism (`backend/api/auth.py`) is sufficient for this feature's access control; no new auth scheme is required.
- "Verified corpus" carries the same meaning `001-legal-rag` established: text deliberately checked against an authoritative publication (Gazette notification or recognized legal database), with recorded provenance — this specification does not redefine what counts as verified.
- A generation/explanation step is in required scope for this feature (unlike `001-legal-rag`, where it was explicitly optional and deferred) — because free-form question answering without any synthesized explanation would be of materially lower value than presenting matched provisions alone; the specific generation technology remains a planning-time decision, and it must be held to the same groundedness standard (FR-013, SC-007) `001-legal-rag` set for any future optional explanation step.
- The exact numeric latency target (SC-011) and the specific confidence-threshold mechanics implementing FR-015/FR-016 are empirically determined at planning/implementation time against real corpus/query pairs, consistent with `001-legal-rag` research.md's own precedent for not pre-committing an unvalidated number.
- This feature may justify introducing infrastructure `001-legal-rag` explicitly declined for its own scope (e.g. a vector index, an external embedding or generation service) — such a decision is independently evaluated and benchmark-justified for *this* feature's real use case, not inherited automatically from the other feature's decision either direction.

## Constitution Alignment

| Constitution Principle | How this specification honors it |
|---|---|
| I. Deterministic Compliance Authority | FR-003, FR-024, User Story 5, SC-014 — no requirement in this spec computes, reads for decision purposes, or writes a compliance status/score. |
| II. Official Human Authority | FR-001, FR-002, FR-026, User Stories 1–3 — every answer is research input to an Official's own work, never a decision; Official-only access is enforced, not merely presented. |
| III. No Hallucinated Legal Requirements | FR-009, FR-010, FR-015–FR-017, SC-005–SC-009 — every requirement treats an unsourced or low-confidence answer as a safe refusal, never a guess; a plausible wrong citation is this feature's most serious failure class (SC-009). |
| IV. Structural Separation of AI Output from Authoritative Decisions | FR-011, FR-014, FR-025, FR-037 — generated explanations and citations are always visibly distinct from quoted legal text, from `ComplianceEngine`/Official decision content, and from `001-legal-rag`'s own Legal Basis panel. |
| V. Evidence Provenance | FR-009, FR-019–FR-022, SC-006 — provenance is a first-class, end-to-end requirement for every citation, reusing `001-legal-rag`'s own provenance data shape rather than inventing a weaker one. |
| VI. Accuracy Over Performance | SC-001–SC-010 prioritize correctness/groundedness/safe-refusal; SC-011's latency bar is deliberately non-numeric here, deferred to empirical planning, not a target that could pressure accuracy trade-offs. |
| VII. Test + Benchmark Discipline | SC-013 mandates the existing suite stays green; SC-001–SC-012 define this feature's own benchmark-style acceptance bar, explicitly modeled on `001-legal-rag`'s own Phase 8 benchmark methodology (and its A/B/C outcome classification). |
| VIII. Surgical, Scoped Changes | Non-Goals explicitly excludes OCR/YOLO/Gemini/`ComplianceEngine`/`001-legal-rag` modification; this feature is additive only, reusing the existing corpus and auth mechanism rather than forking either. |
| IX. Test Integrity | SC-013 explicitly forbids modifying/skipping/removing existing tests to accommodate this feature. |
| X. Architectural Conservatism | User Story 6, FR-031, Non-Goals — additive only; existing behavior when this feature is absent/unavailable is a first-class, independently tested requirement, exactly as `001-legal-rag` required of itself. |
| XI. Secrets Hygiene | FR-029. |
| XII. Clean Reversion | Every requirement above describes new, independently-removable capability (new endpoint(s), new UI surface, reused existing corpus/models) — nothing rewires or replaces `001-legal-rag`'s or any other existing component's code, making clean reversion possible if this feature is ever rejected at its own benchmark gate, the same way `001-legal-rag`'s Phase 8 semantic-retrieval experiment was cleanly declined without leaving dead code behind. |
