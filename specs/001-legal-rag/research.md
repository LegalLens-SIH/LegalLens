# Phase 0 Research: Legal RAG

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Each decision below follows the same format: what was decided, why, and what else was
evaluated. Every decision is scoped to the constraints already established as facts
about this project (not assumed): Python 3.11, no containerization, manual
venv+uvicorn deployment, confirmed CPU-only dev/deploy hardware (13th-gen i5, 16GB RAM,
no discrete GPU), zero RAG-related packages currently installed, and a legal corpus
that starts small (2 rule_ids: `LMPC-R6-MANDATORY-DECLARATIONS` with 8 fields,
`LMPC-R24-WHOLESALE-DECLARATIONS` with 3 fields — on the order of a few dozen
provision units at most for the required scope).

This research reuses and formalizes findings from a prior, already-completed
LegalLense RAG audit conducted in this same project (external sources cited there:
Hugging Face model cards, Qdrant Cloud pricing docs, MTEB/embedding-benchmark
write-ups) rather than re-deriving them from scratch — the environment and corpus
facts haven't changed. Nothing here is treated as pre-decided; each item states
explicit alternatives and rationale, and every choice remains revisable at
`/speckit-tasks`/implementation time.

## 1. Does this feature need an external vector database at all (Phase 1 scope)?

**Decision**: No — not for the Phase 1 scope (`LMPC-R6` + `LMPC-R24` only). Start with
deterministic `rule_id`(+field)-keyed lookup against the structured legal corpus
(spec FR-009), with semantic retrieval (FR-010) implemented as an in-process,
file-backed similarity search over a few dozen vectors, not a hosted vector database.

**Rationale**: The spec's own retrieval requirements (FR-009, FR-010) make semantic
search a *fallback*, not the primary path — most requests are expected to resolve via
exact `rule_id`/field linkage alone, which needs no vector search at all. A hosted
vector database sized for a corpus of "a few dozen provisions" is solving a scale
problem this feature doesn't have yet. Constitution Principle VIII (Surgical, Scoped
Changes) and spec FR-031 ("no unnecessary runtime dependencies") both argue against
introducing an external service before there's a concrete need for one. An in-process
similarity search over a few dozen vectors (e.g. cosine similarity via a small NumPy
array — a dependency already present in this project via the OCR stack) has
effectively zero infrastructure cost, zero new service to deploy or credential, and
zero latency concern at this scale.

**Alternatives considered**:
- **Qdrant Cloud (managed, free tier)** — genuinely suitable *if/when* the corpus
  grows past the point an in-process search is comfortable (the prior audit confirmed
  its free tier — 0.5 vCPU/1GB RAM/4GB disk, permanent, not a trial — is more than
  sufficient for that later scale). Rejected for Phase 1 specifically because it adds
  an external service, a credential to manage (constitution Principle XI), and a
  network dependency for a corpus size that doesn't need it. **Revisit this decision**
  when corpus coverage expands meaningfully beyond `LMPC-R6`/`LMPC-R24`, or if
  semantic-fallback query volume turns out to be much higher than expected.
- **Self-hosted vector DB (e.g. local Qdrant/Chroma via Docker)** — rejected outright:
  this project has no containerization today (confirmed: no `Dockerfile`,
  `docker-compose.yml`, or CI config anywhere in the repo), and introducing one just
  to host a vector database for a few dozen vectors would be a disproportionate
  architecture change relative to Principle X (Architectural Conservatism).

## 2. Embedding model (for the semantic-fallback path)

**Decision**: `BAAI/bge-small-en-v1.5` (33M parameters, 384-dim).

**Rationale**: The legal corpus in required scope is English-only regulatory text
(the Legal Metrology (Packaged Commodities) Rules, 2011, and its English-language
amendments/notifications) — there is no confirmed near-term need for multilingual
retrieval. A 33M-parameter model runs comfortably on the confirmed CPU-only, 16GB
hardware with sub-second embedding latency, satisfying spec FR-030's CPU preference
without qualification. It has strong, independently benchmarked English retrieval
quality (MTEB) for its size class.

**Alternatives considered**:
- **`intfloat/multilingual-e5-small`** — reasonable if/when Hindi or other
  regional-language legal text enters the corpus; not justified for the current
  English-only required scope. Larger (118M vs 33M) for no current benefit.
- **`BAAI/bge-m3`** — higher retrieval-quality ceiling (dense+sparse+multi-vector,
  8K-token context) but ~17x the parameter count; disproportionate to a corpus of a
  few dozen short provisions. Revisit only if `bge-small-en-v1.5` proves
  retrieval-precision insufficient against spec SC-002 (≥90% top-result precision).
- **`law-ai/InLegalBERT` / `bhavyagiri/InLegal-Sbert`** — India-legal-domain-pretrained,
  but on Supreme/High Court **judgments**, not regulatory rule text — a genuine domain
  mismatch risk for this corpus (case-law language patterns differ meaningfully from
  short, enumerated regulatory declarations). Worth an empirical A/B against
  `bge-small-en-v1.5` during implementation if retrieval quality is ever in question,
  not assumed superior by domain-name alone.

## 3. Reranking (for the semantic-fallback path, when more than one candidate is returned)

**Decision**: `BAAI/bge-reranker-v2-m3`, used only when semantic fallback returns
multiple plausible candidates for one query (deterministic single-`rule_id` matches
never need reranking at all).

**Rationale**: Lightweight, CPU-viable at the tiny candidate-set sizes this feature
will ever present it (a handful of candidates per query, not corpus-wide reranking),
multilingual headroom if the corpus ever grows beyond English, and shares tooling
lineage with the embedding model above (`FlagEmbedding`), minimizing new dependency
surface.

**Alternatives considered**: `jinaai/jina-reranker-v2-base-multilingual` — comparable
quality, marginally faster per available CPU benchmarks; not chosen over
`bge-reranker-v2-m3` for any strong reason, and either is acceptable — this is a
low-stakes choice given the tiny candidate-set sizes involved either way. **Whether a
reranking step is needed at all in Phase 1** is itself open: given deterministic
`rule_id` linkage is expected to resolve the large majority of the required scope
(`LMPC-R6`/`LMPC-R24` fields all have a clean 1:1 or small 1:N rule↔field mapping),
reranking may turn out to be unnecessary for the required scope and only relevant once
corpus coverage broadens. Confirm empirically before implementing.

## 4. Grounded explanation generation (optional, per spec FR-015)

**Decision**: Not required for Phase 1. Spec FR-015 makes the generated explanation
explicitly optional — the required output (FR-014) is the provision text, reference,
source, and provenance, all of which come directly from the structured corpus with no
generation step at all. Defer the generation-LLM decision entirely until provision
text + citation alone are shown to be insufficient for an Official's needs.

**Rationale**: This is the single highest-risk technology decision in the whole
feature (constitution Principle III — no hallucinated legal requirements) and the
*least* necessary one for a Phase 1 MVP. Removing it from Phase 1 scope removes an
entire class of groundedness risk (spec SC-004) without reducing the feature's core
value (a citable legal provision is useful on its own).

**Alternatives considered, for when/if this is revisited**:
- **Reuse the existing Gemini integration** (`backend/ocr/gemini_service.py`) —
  lowest-new-infrastructure option; the pattern (async client, timeout, graceful
  fallback to "no explanation" on failure) already exists in this codebase and is
  proven. Would need the same "never let generation affect a decision" discipline
  this plan requires everywhere else.
- **A small model via Hugging Face Inference API** (e.g. `microsoft/Phi-4-mini-instruct`)
  — avoids adding a second AI vendor dependency's-worth of trust surface if the team
  wants to reduce reliance on Gemini specifically; not self-hosted (self-hosting any
  generation-capable model on this CPU-only 16GB machine is not realistic).
- **No generation at all, ever** — a legitimate permanent choice, not just a Phase 1
  deferral; the spec was deliberately written to make this optional rather than
  required, precisely to keep this door open.

## 5. Legal-source acquisition method

**Decision**: Manual, human-verified acquisition against an authoritative published
source (e.g. an official Gazette notification / recognized legal database), not
automated scraping. This is explicitly a data-correctness task, not a technology
choice, and is out of this plan's scope to perform (per the task instructions: do not
acquire or invent legal text). It is scoped here only to note that **no tooling
decision in this plan should assume or require full corpus acquisition being
complete** — Phase 1 tasks must be able to proceed incrementally starting with however
much of `LMPC-R6` is verified first (spec's own stated priority).

**Rationale**: Constitution Principle III is unconditional — "no hallucinated legal
requirements" and "verified source provenance" cannot be satisfied by any automated
acquisition method without a human verification step. This is a process decision, not
a library/model decision, and is intentionally not resolved by picking a tool.

## 6. Confidence threshold for "low-confidence retrieval" (spec FR-012)

**Decision**: Deferred to implementation-time empirical tuning, not fixed here. The
existing Gemini integration's `GEMINI_CONFIDENCE_THRESHOLD=0.75` is a useful precedent
for "a configurable, environment-driven threshold is this project's established
pattern for this kind of decision" — the same pattern (an env-configurable value with
a conservative default) should apply here, but the specific numeric default requires
empirical validation against real corpus/query pairs, which doesn't exist yet.

**Rationale**: Picking a specific number now, before any real retrieval has been run
against the real (not-yet-acquired) corpus, would be guessing — exactly the kind of
premature technology lock this plan is directed to avoid.

## Summary of what remains genuinely open (by design)

- Whether grounded explanation generation is ever built at all (explicitly optional,
  deferred past Phase 1).

### Closed, evidence-based (Phase 8 evaluation - no longer open)

The items below were open at Phase 0 research time and are now **resolved** by the
Phase 8 benchmark (specs/001-legal-rag/tasks.md, "Phase 8 Evaluation Outcome"):

- **Semantic retrieval / embedding model (§2 above)**: evaluated empirically -
  `BAAI/bge-small-en-v1.5` (36-query benchmark: 78.1% top-1, 15.6% wrong-rule) and
  `BAAI/bge-m3` (81.25% top-1, 15.6% wrong-rule, unchanged) both missed spec SC-002
  (≥90% top-1) and SC-005 (<5% wrong-rule). **Declined for production adoption** -
  deterministic `(rule_id + field)` lookup remains the sole retrieval mechanism.
  §2's model recommendation above is retained as the historical Phase 0 record of
  which model would have been tried first, not as a still-pending decision.
- **Reranking (§3 above)**: moot - reranking exists to disambiguate multiple semantic
  candidates, and semantic retrieval itself was declined; there is nothing for a
  reranker to rerank.
- **Whether/when to introduce Qdrant Cloud (§1 above)**: moot for the same reason -
  no vector index exists to host. Revisit only alongside a future semantic-retrieval
  reconsideration (see legal_retrieval.py's module docstring for the exact trigger
  conditions: materially broader corpus coverage, or a genuine freeform-query use
  case that an exact rule_id/field key cannot serve).
- **Exact confidence threshold value (§6 above)**: moot for the same reason - there
  is no semantic-fallback confidence signal to threshold.
