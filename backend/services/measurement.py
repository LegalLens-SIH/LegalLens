"""Deterministic image-geometry measurements, isolated from compliance.

Physical dimensions are emitted only when callers supply a calibration
marked calibrated/acceptable. Otherwise pixel geometry remains available
with a review state. Font measurements never use a legal threshold unless
one is explicitly supplied, and this module is not imported by the
authoritative ComplianceEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


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


def measure_pixel_geometry(*, bbox: list[float] | tuple[float, ...] | None = None,
                           polygon: list[list[float]] | tuple[tuple[float, float], ...] | None = None,
                           calibration: dict[str, Any] | None = None,
                           unit: str = "mm", confidence: float | None = None,
                           evidence_region: dict[str, Any] | None = None,
                           provenance: dict[str, Any] | None = None,
                           uncertainty_px: float | None = None) -> dict[str, Any]:
    """Deterministic review-only width/height estimate from image geometry.

    No physical dimension is returned unless a validated calibration object
    supplies px/mm. Uncertainty is propagated as a conservative pixel bound.
    """
    aliases = {"mm": (1.0, "mm"), "millimeter": (1.0, "mm"), "millimeters": (1.0, "mm"),
               "cm": (10.0, "mm"), "centimeter": (10.0, "mm"), "centimeters": (10.0, "mm"),
               "m": (1000.0, "mm"), "meter": (1000.0, "mm"), "meters": (1000.0, "mm"),
               "in": (25.4, "mm"), "inch": (25.4, "mm"), "inches": (25.4, "mm"),
               "px": (1.0, "px"), "pixel": (1.0, "px"), "pixels": (1.0, "px")}
    unit_key = str(unit).strip().lower()
    if unit_key not in aliases:
        return {"status": "review_required", "available": False, "reason": "unsupported_unit", "dimensions": None}
    scale_factor, normalized_unit = aliases[unit_key]
    try:
        if polygon is not None:
            points = [(float(p[0]), float(p[1])) for p in polygon]
            if len(points) < 3 or not all(math.isfinite(v) for p in points for v in p):
                raise ValueError
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            x1, y1, x2, y2 = (float(v) for v in bbox)
        else:
            raise ValueError
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
            raise ValueError
        error_px = None if uncertainty_px is None else float(uncertainty_px)
        conf = None if confidence is None else float(confidence)
        if (error_px is not None and (not math.isfinite(error_px) or error_px < 0)) or (conf is not None and (not math.isfinite(conf) or not 0 <= conf <= 1)):
            raise ValueError
    except (TypeError, ValueError, IndexError, OverflowError):
        return {"status": "review_required", "available": False, "reason": "invalid_geometry_or_quality", "dimensions": None}
    width_px, height_px = x2 - x1, y2 - y1
    scale = None
    if normalized_unit == "mm" and isinstance(calibration, dict) and calibration.get("status") == "calibrated" and calibration.get("acceptable") is True:
        try:
            scale = float(calibration.get("scale_px_per_mm"))
        except (TypeError, ValueError):
            scale = None
        if scale is None or not math.isfinite(scale) or scale <= 0:
            scale = None
    can_convert = normalized_unit == "px" or scale is not None
    divisor = 1.0 if normalized_unit == "px" else scale
    dims = {"width": round(width_px / divisor / scale_factor, 4), "height": round(height_px / divisor / scale_factor, 4)} if can_convert else None
    error = round(error_px / divisor / scale_factor, 4) if can_convert and error_px is not None else None
    return {
        "status": "experimental_measurement" if can_convert else "review_required", "available": can_convert,
        "dimensions": dims, "unit": "px" if normalized_unit == "px" else unit_key,
        "pixel_dimensions": {"width": round(width_px, 4), "height": round(height_px, 4)},
        "uncertainty": {"absolute_bound": error, "unit": "px" if normalized_unit == "px" else unit_key, "status": "bounded" if error_px is not None else "not_quantified"},
        "confidence": conf, "quality": "unassessed" if conf is None else "confidence_recorded",
        "evidence_region": evidence_region, "provenance": provenance or {"method": "image_geometry", "source": "provided_geometry"},
        "authoritative": False, "used_for_compliance": False,
        "reason": None if can_convert else "validated_physical_calibration_unavailable",
    }


def measure_ocr_dimensions(detection: dict[str, Any], *, calibration: dict[str, Any] | None = None,
                          evidence_region_ids: list[str] | None = None) -> dict[str, Any]:
    """Return auditable width/height measurements for an OCR line geometry."""
    bbox = detection.get("bbox") if isinstance(detection, dict) else None
    polygon = detection.get("polygon") if isinstance(detection, dict) else None
    evidence = {"bbox": bbox, "polygon": polygon, "region_ids": list(evidence_region_ids or [])}
    return measure_pixel_geometry(
        bbox=bbox, polygon=polygon, calibration=calibration,
        confidence=(detection.get("confidence") if isinstance(detection, dict) else None),
        evidence_region=evidence,
        provenance={"source": "paddleocr", "method": "ocr_line_geometry", "engine": "paddleocr"},
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
    values = (feature_height_px, reference_width_px, reference_width_mm)
    if any(isinstance(value, bool) for value in values):
        raise CalibrationError("measurement inputs must be numeric, finite values")
    try:
        feature_height_px, reference_width_px, reference_width_mm = (float(value) for value in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CalibrationError("measurement inputs must be numeric, finite values") from exc
    if not all(math.isfinite(value) for value in (feature_height_px, reference_width_px, reference_width_mm)):
        raise CalibrationError("measurement inputs must be finite")
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
