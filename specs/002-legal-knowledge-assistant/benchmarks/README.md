# Benchmarks: Advanced Legal Knowledge Assistant for Officials

**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md) | **Tasks**: [../tasks.md](../tasks.md)

This directory holds this feature's benchmark **dataset and results** (pure JSON/
Markdown, small, safe to version-control) — see `tasks.md` T005–T007, T011, T016,
T045.

## What lives here vs. what does not (T004)

**Lives here (committed, versioned)**:
- `benchmark_dataset.json` — the query/expected-answer fixture (T005).
- `phase3-retrieval-results.md`, `phase4-reranking-results.md`,
  `phase5-generation-results.md`, `phase6-security-design-review.md`,
  `final-acceptance-results.md` — evidence tables/logs from each phase's evaluation
  (T011, T012, T016, T018/T019, T045).
- This README.

**Does NOT live here, and must never be committed** — exactly the discipline
`001-legal-rag`'s own Phase 8 benchmark already established and this feature's
`research.md`/`tasks.md` explicitly carry forward:
- Any evaluation-only Python dependency (`sentence-transformers`, `torch`, a
  reranker/generation library, etc.) — installed only into a disposable virtual
  environment outside this repository (the session scratchpad, or an equivalent
  throwaway location), never into `backend/requirements.txt`/`backend/constraints.txt`/
  `backend/.venv311` unless and until a technology is actually **adopted** (T048), and
  even then only via the existing `--no-deps` + `constraints.txt` pinning discipline.
- Any downloaded model weight/cache (a Hugging Face model cache can be multiple
  gigabytes) — lives in that same disposable location's cache directory, never in
  this repository.
- Any vector index/database file built from a candidate embedding model — same rule.

**Rationale**: this mirrors `001-legal-rag`'s Phase 8 benchmark run exactly (a
disposable `bench_venv`, `sentence-transformers`+`torch` installed there only,
`BAAI/bge-small-en-v1.5`/`BAAI/bge-m3` downloaded there only) — that benchmark left
`git status` identical before and after running it. This feature's benchmark work
must leave the same footprint: rich, reusable **evidence** committed under this
directory; heavy, disposable **runtime dependencies** never committed anywhere.
