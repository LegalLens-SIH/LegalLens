from __future__ import annotations

import math
from typing import Any


def _scale_px_per_mm(calibration: dict[str, Any] | None) -> tuple[float | None, str | None]:
    if not isinstance(calibration, dict):
        return None, "A valid physical calibration is unavailable."
    if calibration.get("status") != "calibrated" or calibration.get("acceptable") is not True:
        return None, "Calibration is not an explicitly accepted, evidence-backed calibration."
    if calibration.get("status") in {"review_required", "invalid", "unavailable"} or calibration.get("acceptable") is False:
        return None, "Calibration was rejected or requires review."
    scale = calibration.get("scale_px_per_mm")
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return None, "Calibration scale is missing or invalid."
    if isinstance(scale, bool) or not math.isfinite(value) or value <= 0:
        return None, "Calibration scale must be finite and positive."
    return value, None


def validate_legibility(
    *,
    text_height_px: float,
    calibration: dict[str, Any] | None,
    geometry: dict[str, Any] | None,
    minimum_font_height_mm: float | None = None,
    threshold_source: dict[str, Any] | None = None,
    review_default_threshold_mm: float = 2.0,
) -> dict[str, Any]:
    """Validate a text block's legibility using existing calibration/geometry output.

    This function is intentionally narrow: it converts an OCR bounding-box height
    from pixels into millimetres using the already-established scale from
    barcode_calibration.py and it checks that against a minimum height threshold.
    If no verified legal threshold is provided, it routes to review by default
    instead of pretending to know the law.
    """
    try:
        height_px = float(text_height_px)
    except (TypeError, ValueError):
        height_px = 0.0
    if not math.isfinite(height_px) or height_px <= 0:
        return {
            "status": "review_required",
            "estimated_height_mm": None,
            "minimum_font_height_mm": minimum_font_height_mm,
            "review_reason": "OCR text height is missing, zero, or invalid.",
            "calibration_used": calibration,
        }

    scale, calibration_error = _scale_px_per_mm(calibration)
    if calibration_error is not None or scale is None:
        return {
            "status": "review_required",
            "estimated_height_mm": None,
            "minimum_font_height_mm": minimum_font_height_mm,
            "review_reason": calibration_error,
            "calibration_used": calibration,
        }

    geometry_status = (geometry or {}).get("status")
    if geometry_status == "review_required":
        return {
            "status": "review_required",
            "estimated_height_mm": round(height_px / scale, 4),
            "review_reason": "Geometry check failed; package coverage is below the minimum threshold.",
            "minimum_font_height_mm": minimum_font_height_mm,
            "calibration_used": calibration,
        }

    estimated_mm = height_px / scale

    if minimum_font_height_mm is None:
        return {
            "status": "review_required",
            "estimated_height_mm": round(estimated_mm, 4),
            "minimum_font_height_mm": None,
            "threshold_status": "unavailable",
            "review_reason": "No verified minimum font-height threshold is available; legal compliance requires manual review.",
            "calibration_used": calibration,
        }

    try:
        threshold = float(minimum_font_height_mm)
    except (TypeError, ValueError):
        threshold = 0.0
    if not math.isfinite(threshold) or threshold <= 0:
        return {
            "status": "review_required",
            "estimated_height_mm": round(estimated_mm, 4),
            "minimum_font_height_mm": minimum_font_height_mm,
            "review_reason": "Minimum font-height threshold is invalid and requires review.",
            "calibration_used": calibration,
        }

    threshold_verified = (
        isinstance(threshold_source, dict)
        and threshold_source.get("verification_status") == "verified"
        and bool(threshold_source.get("provision_id"))
        and bool(threshold_source.get("citation"))
    )
    if estimated_mm >= threshold:
        return {
            "status": "review_required",
            "estimated_height_mm": round(estimated_mm, 4),
            "minimum_font_height_mm": threshold if threshold_verified else None,
            "threshold_status": "verified_review_only" if threshold_verified else "unverified",
            "threshold_comparison": "at_or_above" if threshold_verified else None,
            "review_reason": "Measurement remains non-authoritative and requires review.",
            "calibration_used": calibration,
            "threshold_source": threshold_source if threshold_verified else None,
            "used_for_compliance": False,
        }

    return {
        "status": "review_required",
        "estimated_height_mm": round(estimated_mm, 4),
        "minimum_font_height_mm": threshold if threshold_verified else None,
        "threshold_status": "verified_review_only" if threshold_verified else "unverified",
        "threshold_comparison": "below" if threshold_verified else None,
        "review_reason": "Measurement remains non-authoritative and requires review; the legal threshold must be verified.",
        "calibration_used": calibration,
        "threshold_source": threshold_source if threshold_verified else None,
        "used_for_compliance": False,
    }


def measure_ocr_detection(
    *,
    detection: dict[str, Any],
    calibration: dict[str, Any] | None,
    geometry: dict[str, Any] | None = None,
    minimum_font_height_mm: float | None = None,
    threshold_source: dict[str, Any] | None = None,
    evidence_region_ids: list[str] | None = None,
    image_shape: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Measure an OCR detection's physical height, retaining its source evidence.

    A quadrilateral uses its shorter pair of opposite edges, so rotation
    does not turn text width into text height. Axis-aligned OCR boxes remain
    supported as a deterministic fallback.
    """
    if not isinstance(detection, dict):
        result = validate_legibility(
            text_height_px=0,
            calibration=calibration,
            geometry=geometry,
            minimum_font_height_mm=minimum_font_height_mm,
            threshold_source=threshold_source,
        )
        result["ocr_evidence"] = None
        return result

    polygon_value = detection.get("polygon")
    polygon: list[tuple[float, float]] | None = None
    height_px = 0.0
    try:
        if isinstance(polygon_value, (list, tuple)) and len(polygon_value) == 4:
            polygon = [(float(point[0]), float(point[1])) for point in polygon_value if len(point) == 2]
            if len(polygon) != 4 or any(not math.isfinite(value) for point in polygon for value in point):
                raise ValueError
            cross_products = []
            for index in range(4):
                origin = polygon[index]
                first = polygon[(index + 1) % 4]
                second = polygon[(index + 2) % 4]
                cross_products.append((first[0] - origin[0]) * (second[1] - first[1]) - (first[1] - origin[1]) * (second[0] - first[0]))
            if any(abs(value) < 1e-6 for value in cross_products) or not (
                all(value > 0 for value in cross_products) or all(value < 0 for value in cross_products)
            ):
                raise ValueError
            edge_lengths = [
                math.hypot(polygon[(index + 1) % 4][0] - polygon[index][0], polygon[(index + 1) % 4][1] - polygon[index][1])
                for index in range(4)
            ]
            opposite_pairs = ((edge_lengths[0], edge_lengths[2]), (edge_lengths[1], edge_lengths[3]))
            if any(abs(first - second) / max(first, second) > 0.30 for first, second in opposite_pairs):
                raise ValueError
            height_px = min(sum(pair) / 2 for pair in opposite_pairs)
        else:
            bbox = detection.get("bbox")
            if isinstance(bbox, dict):
                x1, y1, x2, y2 = (float(bbox[key]) for key in ("x1", "y1", "x2", "y2"))
            elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                x1, y1, x2, y2 = (float(value) for value in bbox)
            else:
                raise ValueError
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
                raise ValueError
            polygon = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            height_px = y2 - y1
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        height_px = 0.0

    if image_shape is not None and polygon is not None:
        try:
            image_height, image_width = (float(value) for value in image_shape)
            inside_image = (
                math.isfinite(image_height)
                and math.isfinite(image_width)
                and image_height > 0
                and image_width > 0
                and all(0 <= x <= image_width and 0 <= y <= image_height for x, y in polygon)
            )
        except (TypeError, ValueError):
            inside_image = False
        if not inside_image:
            height_px = 0.0

    result = validate_legibility(
        text_height_px=height_px,
        calibration=calibration,
        geometry=geometry,
        minimum_font_height_mm=minimum_font_height_mm,
        threshold_source=threshold_source,
    )
    raw_bbox = detection.get("bbox")
    if raw_bbox is None and polygon is not None:
        raw_bbox = [min(point[0] for point in polygon), min(point[1] for point in polygon), max(point[0] for point in polygon), max(point[1] for point in polygon)]
    result["pixel_height_px"] = height_px if height_px > 0 else None
    result["ocr_evidence"] = {
        "text": detection.get("text"),
        "confidence": detection.get("confidence"),
        "bbox": raw_bbox,
        "polygon": detection.get("polygon"),
        "region_ids": evidence_region_ids if evidence_region_ids is not None else detection.get("evidence_region_ids", []),
    }
    result.setdefault("source", "paddleocr")
    result.setdefault("method", "ocr_polygon_text_height")
    result.setdefault("confidence", detection.get("confidence"))
    result.setdefault("status", "review_required")
    result["authoritative"] = False
    result["used_for_compliance"] = False
    result["evidence_region"] = {
        "bbox": detection.get("bbox"),
        "polygon": detection.get("polygon"),
        "region_ids": list(evidence_region_ids or []),
    }
    result["pixel_height"] = round(height_px, 4) if height_px > 0 else None
    return result
