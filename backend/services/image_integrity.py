"""
Phase 1, Requirement 6: SHA-256 image integrity hashing.

Scope (deliberately narrow, same spirit as authenticity_service.py):
compute a deterministic SHA-256 digest of the EXACT raw bytes a caller
uploaded, before any OCR, preprocessing, or re-encoding touches them. This
is integrity/provenance evidence only - it never makes, implies, or stores
a compliance decision, and nothing in backend/services/compliance_engine.py
imports or reads it. It answers "is this the exact file that was analyzed"
(useful for audit/dispute resolution), never "is this image genuine/
tampered" (that question - and ELA/noise/authenticity scoring - was
explicitly benchmarked and rejected for this project; see backend/services/
authenticity_service.py's own docstring for the same boundary applied to
EXIF).

Callers (backend/api/ocr.py's create_scan, backend/api/manufacturer.py's
create_self_check) already read the raw upload bytes into memory before
calling run_ocr() for their own reasons (to persist the original image via
GridFS) - this function is called against that SAME already-in-memory
buffer, so hashing never requires a second read of the upload and never
touches OCR/preprocessing behavior in any way.
"""

from __future__ import annotations

import hashlib


def compute_image_sha256(image_bytes: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of the exact given bytes.
    Deterministic (same bytes always produce the same digest); a single
    changed byte anywhere produces a completely different digest. Never
    raises for any byte content, including empty bytes."""
    return hashlib.sha256(image_bytes).hexdigest()
