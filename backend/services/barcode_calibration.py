from __future__ import annotations

import math
from typing import Any

from backend.database import get_database
from backend.services.audit import append_audit_event
from backend.services.gs1_verification import normalize_gtin

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


def decode_ean13_barcodes(image: Any, *, source_image_sha256: str) -> dict[str, Any]:
    """Decode real barcode symbols and retain their source-image geometry."""
    unavailable = {
        "status": "unavailable", "available": False,
        "decoder": "opencv_barcode_BarcodeDetector", "detections": [],
        "source_image_sha256": source_image_sha256,
        "authoritative": False, "used_for_compliance": False,
    }
    if image is None or getattr(image, "size", 0) == 0:
        return {**unavailable, "status": "invalid_image", "reason": "Barcode decoder received an empty image."}
    if (not isinstance(source_image_sha256, str) or len(source_image_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in source_image_sha256)):
        return {**unavailable, "status": "source_evidence_missing", "reason": "A source-image SHA-256 is required."}
    try:
        import cv2
    except ImportError:
        return {**unavailable, "reason": "OpenCV barcode decoding is unavailable in this runtime."}
    try:
        detector_type = getattr(cv2, "barcode_BarcodeDetector", None)
        if detector_type is None:
            return {**unavailable, "reason": "OpenCV barcode decoding is unavailable in this runtime."}
        decoded, decoded_values, decoded_types, raw_points = detector_type().detectAndDecodeWithType(image)
    except Exception as exc:
        return {
            **unavailable, "status": "decoder_error",
            "reason": f"OpenCV barcode decoding failed ({type(exc).__name__}).",
        }

    try:
        points = raw_points.tolist() if raw_points is not None else []
    except (AttributeError, TypeError, ValueError):
        points = []
    if points and len(points) == 4 and isinstance(points[0], (int, float)):
        points = [points]
    decoded_values = list(decoded_values or [])
    decoded_types = list(decoded_types or [])
    count = max(len(decoded_values), len(decoded_types), len(points))
    height, width = image.shape[:2]
    detections: list[dict[str, Any]] = []
    for index in range(count):
        raw_value = str(decoded_values[index] or "").strip() if index < len(decoded_values) else ""
        symbology = str(decoded_types[index] or "unknown") if index < len(decoded_types) else "unknown"
        try:
            source_points = points[index]
            if len(source_points) == 1 and len(source_points[0]) == 4:
                source_points = source_points[0]
            polygon = [[float(x), float(y)] for x, y in source_points]
        except (IndexError, TypeError, ValueError):
            polygon = []
        if len(polygon) != 4 or any(not math.isfinite(value) for point in polygon for value in point):
            polygon = []
        normalized = normalize_gtin(raw_value, symbology) if raw_value else None
        checksum_valid = bool(normalized and normalized["valid"])
        detection_status = "decoded" if checksum_valid else "unreadable" if not raw_value else "unsupported_or_invalid"
        detections.append({
            "barcode_id": f"barcode_{index}",
            "detection_id": f"barcode_{index}",
            "code": raw_value if checksum_valid and normalized["symbology"] == "EAN_13" else None,
            "raw_value": raw_value or None,
            "gtin": normalized["gtin"] if normalized else None,
            "gtin_status": normalized["status"] if normalized else "UNREADABLE",
            "gtin_error": normalized["error"] if normalized else "Barcode decoder returned no value.",
            "symbology": symbology,
            "checksum_valid": checksum_valid,
            "status": detection_status,
            "polygon": polygon,
            "bbox": [
                min(point[0] for point in polygon), min(point[1] for point in polygon),
                max(point[0] for point in polygon), max(point[1] for point in polygon),
            ] if polygon else None,
            "confidence": None,
            "source": "opencv_barcode_detector",
            "source_image_sha256": source_image_sha256.lower(),
        })

    detections.sort(key=lambda item: (
        not item["checksum_valid"],
        -((item["bbox"][2] - item["bbox"][0]) * (item["bbox"][3] - item["bbox"][1])) if item["bbox"] else 0,
        item["bbox"][1] if item["bbox"] else float("inf"),
        item["bbox"][0] if item["bbox"] else float("inf"),
        item["symbology"],
        item["raw_value"] or "",
    ))
    for index, detection in enumerate(detections):
        detection["barcode_id"] = f"barcode_{index}"
        detection["detection_id"] = detection["barcode_id"]

    valid_codes = any(item["status"] == "decoded" for item in detections)
    detected_geometry = any(item["polygon"] for item in detections)
    return {
        "status": "decoded" if valid_codes else "barcode_unreadable" if detected_geometry or decoded else "no_barcode_detected",
        "available": True,
        "decoder": "opencv_barcode_BarcodeDetector",
        "detections": detections,
        "source_image_sha256": source_image_sha256.lower(),
        "source_image_dimensions": {"width": int(width), "height": int(height)},
        "authoritative": False,
        "used_for_compliance": False,
    }


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
        "uncertainty": {"status": "not_quantified", "reason": "This legacy helper has no verified reference tolerance or source evidence."},
        "authoritative": False,
        "used_for_compliance": False,
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
            "authoritative": False,
            "used_for_compliance": False,
            "uncertainty": {"status": "not_quantified", "reason": "A validated reference tolerance and camera geometry error model are unavailable."},
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
    confidence = None
    raw_confidence = evidence.get("confidence")
    if raw_confidence is None:
        verified_decoder_result = (
            evidence.get("source") == "opencv_barcode_detector"
            and evidence.get("symbology") == "EAN_13"
            and evidence.get("checksum_valid") is True
            and evidence.get("source_image_sha256")
        )
        if not verified_decoder_result:
            return review("Barcode source confidence is missing and no checksum-validated EAN-13 decoder evidence is available")
    else:
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            return review("Barcode source confidence is invalid")
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
        "authoritative": False,
        "used_for_compliance": False,
        "method": "ean13_symbol_geometry_with_explicit_reference",
        "provenance": {
            "source": "barcode_geometry_and_operator_reference",
            "reference_source": reference_source.strip(),
            "evidence_region_ids": [evidence.get("region_id")] if evidence.get("region_id") else [],
        },
        "scale_px_per_mm": round(symbol_width_px / reference_width, 6),
        "uncertainty": {"status": "not_quantified", "reason": "A validated reference tolerance and camera geometry error model are unavailable."},
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
        "evidence_region": {
            "bbox": [min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)],
            "polygon": points,
        },
    }
