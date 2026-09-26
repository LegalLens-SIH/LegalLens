# Production Contract

## Scope

This document defines the production-safe behavior required for LegalLense when it is running outside demo mode. The app may keep demo mock data and preview flows for local exploration, but they must be disabled by default in production and never appear in the normal authenticated runtime.

## Required behaviour

1. Demo / mock data must remain inert unless the app is explicitly started with the `demo=true` query flag or equivalent opt-in.
2. All production scans must be backed by real uploaded bytes, real OCR results, and real compliance evaluation.
3. Every persisted scan must store:
   - a deterministic source-image SHA-256 digest of the exact original upload bytes,
   - the source image record in GridFS,
   - the OCR result,
   - the compliance result,
   - the authenticity evidence block if EXIF is available.
4. The compliance engine remains the only source of PASS / FAIL / manual review decisions. Authenticity evidence is additive metadata and never influences the verdict.
5. The confidence threshold must remain 0.90 for production operational decisions unless a separate signed change explicitly updates the contract.
6. The scan status must follow the four-state contract:
   - `COMPLIANT`
   - `NON_COMPLIANT`
   - `PARTIALLY_COMPLIANT`
   - `NEEDS_MANUAL_VERIFICATION`
7. A production image evidence ID scheme must be stable and human-auditable, e.g. `IMG-<timestamp>-<sha256prefix>` for original uploads and `REV-<uuid>` for revision records.
8. The system must preserve model / rule-set metadata alongside the evaluation result so each report can identify which version produced the verdict.
9. A retained evidence object must include the upload’s MIME type and the original filename; GPS metadata is intentionally stored only as a presence flag (`gps_present: true|false`) and never as raw coordinates or validated lat/long values. This is a deliberate privacy and data-minimization decision, not a placeholder or half-implemented validation path: the system must not retain location data or expose device GPS coordinates via the API, because compliance does not require location data and storing it would expand the personal-data footprint without producing a decision signal.
10. Retention and access: source images are retained only for their required audit window and never publicly served; each stored record must be tied to the owning user or official workflow.
11. Audit events are append-only and contain the acting user, timestamp, action, and immutable evidence reference.

## Non-goals and guardrails

- This contract does not allow authenticity evidence to produce a fraud verdict.
- This contract does not permit mock dataset leakage into the production path.
- This contract does not allow unbounded public access to raw GridFS image content.
- This contract does not permit downgrading evidence storage to a placeholder or synthetic record.

## Auto-verdict rules

Production verdicts are computed automatically from the compliance engine output. The engine must apply the configured threshold and aggregate the same rule states it already uses for the deterministic workflow. The overall status is computed only from compliance findings; external authenticity metadata is never injected into the rule engine inputs.

## Default production mode

The default runtime must be secure production mode. Demo data is available only when an explicit opt-in flag is present. Until that flag is set, the app must behave as a real production system with no seeded demo records.
