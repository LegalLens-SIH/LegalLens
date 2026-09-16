"""
Phase 1, Requirement 5: mm-calibrated approximate physical measurement.

Scope, deliberately narrow: given a KNOWN real-world reference width (e.g.
an operator-measured package width in millimetres) and the pixel width that
same reference spans in a photo, compute an approximate pixel-to-mm scale
and use it to estimate the physical height of some other pixel measurement
(e.g. an OCR detection's bounding-box height) in the SAME photo. This is a
pure, deterministic unit-conversion utility - nothing more.

CRITICAL - why this is NOT wired into ComplianceEngine or any compliance
verdict:

1. No calibration input exists anywhere in this project today. No API
   endpoint, scan schema, or frontend form collects a package width in mm
   (confirmed by inspection - see the Phase 1 Gap Audit). Without a
   genuine, operator-supplied reference measurement, there is no way to
   convert an OCR bounding box's pixel height into millimetres that isn't
   pure invention - a photo alone carries no absolute scale (the same
   package photographed closer/farther, or a photo with any perspective
   distortion, produces completely different pixel-to-mm ratios). This
   module refuses to guess: every function below REQUIRES an explicit
   reference measurement and raises rather than silently fabricating one.

2. Even with a genuine calibration input, the verified legal corpus
   (backend/legal_corpus/legal_metrology_packaged_commodities_2011.json)
   contains NO provision specifying a minimum numeral/font height in mm
   for any LMPC-R6 declaration - only 11 provisions are verified there
   (MRP, net quantity, manufacturer details, consumer care, etc.; see the
   corpus's own docstring on how each entry was sourced/cross-checked),
   and font-size requirements are not among them. Legal Metrology does
   have such provisions, but they are NOT yet part of this project's
   verified corpus - attaching a compliance PASS/FAIL to an unverified
   legal threshold would violate this project's own "never fabricate legal
   certainty" principle (see legal_retrieval.py's identical stance on an
   unresolved provision).

Given both (1) and (2), this module is deliberately a standalone, tested,
NEVER-imported-by-compliance_engine.py math utility - available for a
FUTURE integration once both a real calibration input AND a verified
legal minimum-height provision exist, and not before. Every result is
explicitly labeled approximate and carries its own assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass


class CalibrationError(ValueError):
    """Raised when a calibration or measurement input is not physically
    meaningful (zero/negative). Never silently coerced or ignored."""


@dataclass(frozen=True)
class CalibratedMeasurement:
    """One approximate physical measurement, always self-describing.

    `approximate` is always True (there is no calibration source this
    module can treat as exact - even a careful manual measurement of a
    package's width carries real-world error, and camera perspective
    introduces further, unmodeled distortion). `assumptions` documents,
    in the result itself, the two things this estimate depends on: (a)
    the reference measurement was accurate, and (b) the reference and the
    measured feature lie in the same image plane (no significant
    perspective tilt between them) - callers must not present this value
    as regulatory-grade without their own awareness of those limits.
    """

    feature_height_px: float
    scale_px_per_mm: float
    estimated_height_mm: float
    approximate: bool = True
    assumptions: tuple[str, ...] = (
        "The supplied reference width in mm was measured accurately.",
        "The reference and the measured feature lie in approximately the same image plane (no significant perspective tilt between them).",
        "This is a geometric estimate only - it is not, by itself, a determination of legal compliance with any font-size requirement.",
    )


def estimate_height_mm(*, feature_height_px: float, reference_width_px: float, reference_width_mm: float) -> CalibratedMeasurement:
    """Estimate the physical height (in mm) of a pixel measurement, given a
    known real-world reference width and the pixel width that reference
    spans in the SAME photo.

    All three inputs must be strictly positive - there is no meaningful
    fallback for a missing/zero/negative calibration input, so this raises
    CalibrationError rather than returning a fabricated or silently-wrong
    measurement (per this task's explicit requirement: never pretend a
    pixel measurement is a physical millimetre value without genuine
    calibration).
    """
    if feature_height_px <= 0:
        raise CalibrationError("feature_height_px must be a positive number of pixels")
    if reference_width_px <= 0:
        raise CalibrationError("reference_width_px must be a positive number of pixels")
    if reference_width_mm <= 0:
        raise CalibrationError("reference_width_mm must be a positive, real-world measurement in millimetres")

    scale_px_per_mm = reference_width_px / reference_width_mm
    estimated_height_mm = feature_height_px / scale_px_per_mm
    return CalibratedMeasurement(
        feature_height_px=feature_height_px,
        scale_px_per_mm=round(scale_px_per_mm, 6),
        estimated_height_mm=round(estimated_height_mm, 4),
    )
