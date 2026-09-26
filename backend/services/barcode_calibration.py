from __future__ import annotations

import math
from typing import Any

from backend.database import get_database
from backend.services.audit import append_audit_event

MIN_BARCODE_EVIDENCE_CONFIDENCE = 0.90


def validate_ean13(code: str) -> bool:
    if not isinstance(code, str):
        return False
    digits = code.strip()
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits[:12]):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    check = (10 - (total % 10)) % 10
    return check == int(digits[-1])


def calibrate_barcode_scale(barcode_width_px: float, reference_width_mm: float, observed_width_px: float) -> dict[str, Any]:
    if barcode_width_px <= 0 or reference_width_mm <= 0 or observed_width_px <= 0:
        raise ValueError("barcode calibration inputs must be positive")

    scale_px_per_mm = barcode_width_px / reference_width_mm
    distortion = abs(observed_width_px - barcode_width_px) / barcode_width_px
    acceptable = distortion <= 0.30
    result = {
        "scale_px_per_mm": round(float(scale_px_per_mm), 4),
        "distortion_score": round(float(distortion), 4),
        "acceptable": acceptable,
    }
    try:
        db = get_database()
        append_audit_event(
            db,
            event_type="barcode_calibration",
            entity_type="barcode",
            entity_id="barcode_calibration",
            actor_role="system",
            action="barcode_calibrated",
            details={
                "scale_px_per_mm": result["scale_px_per_mm"],
                "distortion_score": result["distortion_score"],
                "acceptable": result["acceptable"],
            },
        )
    except Exception:
        pass
    return result


def calibrate_ean13_scale(
    *,
    code: str | None,
    polygon: list[list[float]] | None,
    image_shape: tuple[int, int] | None,
    reference_width_mm: float | None,
    reference_source: str | None,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    """Calibrate from an evidence-backed EAN-13 symbol observation.

    The reference dimension is deliberately caller-supplied and must name
    its source. No default GS1 size is assumed: symbol magnification varies,
    and OCR of the human-readable digits is not symbol geometry.
    """
    source_evidence = dict(evidence) if isinstance(evidence, dict) else None

    def review(reason: str) -> dict[str, Any]:
        return {
            "status": "review_required",
            "acceptable": False,
            "scale_px_per_mm": None,
            "confidence": None,
            "distortion_score": None,
            "reasons": [reason],
            "source_evidence": source_evidence,
        }

    if not isinstance(code, str) or not code.strip():
        return review("EAN-13 evidence is missing")
    normalized_code = code.strip()
    if not normalized_code.isdigit() or len(normalized_code) != 13:
        return review("Barcode evidence is ambiguous; a complete 13-digit EAN-13 is required")
    if not validate_ean13(normalized_code):
        return review("EAN-13 check digit is invalid")

    if not isinstance(evidence, dict) or not evidence:
        return review("Barcode source evidence is missing")
    observed_code = evidence.get("value", evidence.get("text", evidence.get("code")))
    if not isinstance(observed_code, str) or observed_code.strip() != normalized_code:
        return review("Barcode code does not match its source evidence")
    try:
        confidence = float(evidence["confidence"])
    except (KeyError, TypeError, ValueError):
        return review("Barcode source confidence is missing or invalid")
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return review("Barcode source confidence is outside [0, 1]")
    if confidence < MIN_BARCODE_EVIDENCE_CONFIDENCE:
        return review("Barcode source confidence is below the 0.90 calibration threshold")

    if reference_width_mm is None or isinstance(reference_width_mm, bool):
        return review("Verified physical reference width is missing")
    try:
        reference_width = float(reference_width_mm)
    except (TypeError, ValueError):
        return review("Physical reference width is invalid")
    if not math.isfinite(reference_width) or reference_width <= 0:
        return review("Physical reference width must be finite and positive")
    if not isinstance(reference_source, str) or not reference_source.strip():
        return review("Physical reference source is missing")

    if not isinstance(image_shape, (tuple, list)) or len(image_shape) != 2:
        return review("Image dimensions are missing")
    try:
        image_height, image_width = (float(value) for value in image_shape)
    except (TypeError, ValueError):
        return review("Image dimensions are invalid")
    if not all(math.isfinite(value) and value > 0 for value in (image_height, image_width)):
        return review("Image dimensions must be finite and positive")

    if not isinstance(polygon, list) or len(polygon) != 4:
        return review("Barcode geometry requires four ordered corner points")
    try:
        points = [(float(point[0]), float(point[1])) for point in polygon if len(point) == 2]
    except (TypeError, ValueError, IndexError):
        return review("Barcode corner coordinates are invalid")
    if len(points) != 4 or any(not math.isfinite(value) for point in points for value in point):
        return review("Barcode corner coordinates are invalid")
    if any(x < 0 or x > image_width or y < 0 or y > image_height for x, y in points):
        return review("Barcode geometry extends outside the source image")

    cross_products = []
    for index in range(4):
        origin = points[index]
        first = points[(index + 1) % 4]
        second = points[(index + 2) % 4]
        cross_products.append((first[0] - origin[0]) * (second[1] - first[1]) - (first[1] - origin[1]) * (second[0] - first[0]))
    if any(abs(value) < 1e-6 for value in cross_products) or not (
        all(value > 0 for value in cross_products) or all(value < 0 for value in cross_products)
    ):
        return review("Barcode geometry is degenerate or self-intersecting")

    edge_lengths = [
        math.hypot(points[(index + 1) % 4][0] - points[index][0], points[(index + 1) % 4][1] - points[index][1])
        for index in range(4)
    ]
    opposite_distortions = [
        abs(edge_lengths[index] - edge_lengths[index + 2]) / max(edge_lengths[index], edge_lengths[index + 2])
        for index in (0, 1)
    ]
    distortion = max(opposite_distortions)
    if distortion > 0.30:
        return review("Barcode geometry is too distorted for reliable calibration")

    opposite_widths_px = ((edge_lengths[0] + edge_lengths[2]) / 2, (edge_lengths[1] + edge_lengths[3]) / 2)
    symbol_width_px = max(opposite_widths_px)
    if not math.isfinite(symbol_width_px) or symbol_width_px <= 0:
        return review("Barcode geometry has no measurable width")

    return {
        "status": "calibrated",
        "acceptable": True,
        "scale_px_per_mm": round(symbol_width_px / reference_width, 6),
        "confidence": confidence,
        "distortion_score": round(distortion, 4),
        "reasons": [],
        "barcode": {
            "code": normalized_code,
            "polygon": points,
            "width_px": round(symbol_width_px, 4),
        },
        "reference": {
            "width_mm": reference_width,
            "source": reference_source.strip(),
        },
        "source_evidence": source_evidence,
    }
