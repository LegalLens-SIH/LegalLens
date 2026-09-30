# LegalLense Production-Build Sprint Status

**Date:** 2026-09-30
**Repository:** `Portal/`
**Scope:** Code-completable sprint only. No secrets, real provider responses, trained checkpoints, real legal thresholds, physical-reference provenance, or benchmark claims were fabricated. No commit or push was performed.

## Requirement Matrix

| Item | Status | Implementation files | Tests added/used | Focused validation | Remaining gap / reason |
|---|---|---|---|---|---|
| 1. Architecture contract | DONE | `config/architecture.yaml`, `backend/services/architecture_contract.py`, `backend/main.py` | `backend/services/test_architecture_contract.py` | `pytest backend/services/test_architecture_contract.py` | Contract is validated at import/startup; deployment-specific origin and secret policy still require environment configuration. |
| 2. Compliance engine audit | PARTIAL | `backend/services/compliance_engine.py`, `backend/models/compliance.py` | `backend/services/test_compliance_engine.py` | `pytest backend/services/test_compliance_engine.py` | Net-quantity keyword-only false PASS fixed. Rule coverage and shared public status migration remain incomplete. |
| 3. Legal corpus loader/validator | PARTIAL | `backend/services/legal_retrieval.py`, `backend/models/legal.py`, `backend/legal_corpus/legal_metrology_packaged_commodities_2011.json` | `backend/api/test_legal_basis.py`, `backend/services/test_legal_retrieval.py` | `pytest backend/api/test_legal_basis.py backend/services/test_legal_retrieval.py` | One unit-sale-price source is secondary-cross-checked rather than primary VERIFIED; it must not be claimed as fully authoritative without re-verification. |
| 4. Benchmark harness | DONE | `benchmarks/run.py`, `benchmarks/test_run.py`, existing `training/benchmark_e2e.py` | `benchmarks/test_run.py` | `pytest benchmarks/test_run.py` | Harness machinery is complete; real golden labels and a full benchmark run remain data-dependent. |
| 5. OCR speed/accuracy | PARTIAL | `backend/ocr/paddle_ocr_service.py`, `backend/ocr/preprocessing.py`, `benchmarks/run.py` | Existing OCR/lifecycle and benchmark tests | Focused OCR suites and tooling test | PaddleOCR remains authoritative; real accuracy misses and hardware-dependent runtime remain. No unmeasured optimization was claimed. |
| 6. OCR ensemble | PARTIAL | `backend/ocr/ensemble.py`, `backend/ocr/tesseract_service.py` | `backend/ocr/test_ensemble.py`, `backend/services/test_tesseract_service.py` | Existing focused ensemble tests | Tesseract runtime and representative comparison corpus are unavailable; ensemble remains review-only. |
| 7. Font-height | BLOCKED | `backend/services/legibility_validator.py`, `backend/services/measurement.py` | `backend/services/test_legibility_validator.py`, `backend/services/test_measurement.py` | Existing focused tests | Complete review-only machinery exists, but verified legal threshold provenance and independently verified calibration are required for a legal result. |
| 8. Calibration | BLOCKED | `backend/services/physical_reference_registry.py`, `backend/services/barcode_calibration.py`, `backend/api/physical_references.py` | Physical-reference and barcode focused tests | Existing focused tests | Real source-backed reference dimensions and independent physical validation are unavailable. Outputs remain review-only. |
| 9. PDP tooling | BLOCKED | `training/manage_pdp_dataset.py`, `training/_dataset_utils.py`, `training/train_pdp.py`, `training/evaluate_pdp.py`, `backend/services/pdp_detector.py` | `training/test_pdp_dataset.py`, `training/test_dataset_manifest.py`, `backend/services/test_pdp_detector.py` | Existing focused tests | No real labeled PDP dataset, trained checkpoint, held-out metrics, or human promotion approval. Detector remains gated/experimental. |
| 10. ELA | PARTIAL | `backend/services/authenticity_service.py`, `backend/models/authenticity.py` | `backend/services/test_authenticity_service.py`, API authenticity tests | Existing focused tests | ELA/EXIF evidence is implemented and review-only; representative real manipulated-image validation is unavailable. |
| 11. E-commerce verification | PARTIAL | `backend/services/url_safety.py`, `backend/services/listing_retrieval.py`, `backend/services/listing_extraction.py`, `backend/api/listing.py` | Listing, URL safety, and API tests | Existing focused tests | SSRF/redirect/size guards exist; DNS rebinding residual risk and dynamic-page limitations remain. Results never affect authoritative compliance. |
| 12. TSA | BLOCKED | `backend/services/timestamp_authority.py`, `backend/api/evidence.py` | `backend/services/test_timestamp_authority.py`, `backend/services/test_sprint4.py` | Existing focused tests | RFC3161 machinery fails closed, but no configured reachable provider/token was validated. |
| 13. e-Maap | PARTIAL | `backend/services/em_maap_client.py`, `backend/api/em_maap.py` | `backend/services/test_sprint4.py` | Focused e-Maap tests | Explicit simulator is available but never transmits or acknowledges. Real API contract/provider access is unavailable. |
| 14. Security | PARTIAL | `backend/api/auth.py`, `backend/main.py`, upload/URL/evidence services, `.env.example` | API/security/listing/evidence tests | Focused security suites | Configurable secure cookies/CORS and OTP attempt counting added; rate limiting, deployment secret validation, retention and tenant-isolation operations remain. |
| 15. Frontend cleanup | PARTIAL | `frontend/assets/*`, existing frontend test suite | `frontend/tests/*.mjs` | `node --test frontend/tests/*.mjs` | Real API rendering and experimental labels exist, but explicit demo/local-storage preview code remains by design and needs a production-build guard. |
| 16. Deployment | PARTIAL | `backend/Dockerfile`, `.dockerignore`, `backend/main.py`, `.env.example`, `manage-local.py` | Syntax and API smoke checks | Docker validation depends on installed Docker | Non-root backend image, healthcheck, readiness route and deterministic startup exist; CI/container orchestration/resource-limit validation is not yet present. |

## A. Code-Complete Functionality

- Architecture contract loading and fail-closed validation.
- Net-quantity rule correction: keyword-only text such as `Net Vol.` now becomes `NEEDS_MANUAL_VERIFICATION`, not `PASS`.
- Benchmark CLI with ground-truth schema validation, field matching, evidence coverage, stage timing, p50/p95, and baseline comparison.
- Explicit opt-in e-Maap simulator that visibly reports `SIMULATED` and never transmits or acknowledges.
- Configurable CORS and session-cookie security settings.
- Invalid password-reset OTP attempts now increment the attempt counter.
- Non-root backend Dockerfile with `/api/health` healthcheck.

## B. Real-Data-Dependent Functionality

- OCR accuracy and generalization on real golden labels.
- PDP training, evaluation, checkpoint promotion and panel performance.
- Physical reference calibration and font-height measurements.
- ELA manipulation-detection accuracy.
- Production OCR performance on comparable hardware.

## C. External-Access-Dependent Functionality

- Live TSA timestamp issuance and token verification against a configured provider.
- Real e-Maap transmission after an official API contract is available.
- Email delivery through configured Brevo credentials.
- Official re-verification of the secondary-cross-checked legal provision.
- MongoDB/GridFS production persistence, backup and retention operations.

## D. Remaining Production Risks

- No general API rate limiter.
- Local development defaults remain permissive unless deployment environment variables are set.
- No complete CI pipeline/container smoke workflow in the repository.
- Existing frontend demo/local-storage preview code must remain visibly and operationally isolated from production builds.
- Full regression and real benchmark execution remain environment/model dependent.

## E. Recommended Next Actions

1. Add a focused production-build guard that prevents demo data from being bundled or activated without an explicit preview build flag.
2. Add a CI workflow for syntax, focused tests, security checks, Docker build, and `/api/health` smoke testing.
3. Re-verify the secondary legal provision from its primary official source before treating it as authoritative.
4. Fix remaining OCR benchmark misses using retained raw OCR traces and real golden labels.
5. Supply real PDP data/checkpoint and independently reviewed promotion evidence.
6. Configure real TSA/e-Maap providers only after credentials/contracts are available.

## Validation Snapshot

- Complete pytest regression suite: **707 passed, 1 skipped, 1 warning** in 113.36 seconds.
- Frontend Node tests: **47 passed, 0 failed**.
- Modified Python modules: `py_compile` passed.
- Backend smoke test: `/api/health` and `/api/readiness` both returned HTTP 200 against the real FastAPI lifecycle and local MongoDB.
- Benchmark CLI: `python -m benchmarks.run --help` passed.
- `npm audit --omit=dev`: **0 vulnerabilities**.
- `pip-audit`: blocked because the tool is not installed in the environment.
- Docker build/smoke validation: blocked because Docker is not installed in the environment.
- No model training, live TSA request, live e-Maap transmission, or full real-image E2E benchmark was performed.
