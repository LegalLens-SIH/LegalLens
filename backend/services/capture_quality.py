from __future__ import annotations

import hashlib
from typing import Any

import cv2
import numpy as np

from backend.database import get_database
from backend.services.audit import append_audit_event

MIN_CAPTURE_WIDTH = 640
MIN_CAPTURE_HEIGHT = 480


def _sharpness_score(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.hypot(sobel_x, sobel_y)
    edge_density = float(np.mean(magnitude > 25.0))
    texture = float(np.std(gray))
    score = (texture / 50.0) + edge_density - 0.25
    return float(np.clip(score, 0.0, 1.0))


def _glare_score(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    bright_pixels = np.mean(gray > 220)
    return float(np.clip(bright_pixels * 3.5, 0.0, 1.0))


def _blur_score(image: np.ndarray) -> float:
    sharpness = _sharpness_score(image)
    return float(np.clip(1.0 - sharpness, 0.0, 1.0))


def evaluate_capture_quality(image: np.ndarray, *, source_image_sha256: str | None = None) -> dict[str, Any]:
    if image is None or image.size == 0:
        raise ValueError("image must not be empty")

    sharpness = _sharpness_score(image)
    blur = _blur_score(image)
    glare = _glare_score(image)
    height, width = image.shape[:2]
    overall_score = max(0.0, min(1.0, sharpness * 0.9 + (1.0 - glare) * 0.1))
    reasons: list[str] = []
    if width < MIN_CAPTURE_WIDTH or height < MIN_CAPTURE_HEIGHT:
        reasons.append(f"Image resolution must be at least {MIN_CAPTURE_WIDTH} x {MIN_CAPTURE_HEIGHT} pixels")
    if blur > 0.45:
        reasons.append("Blur too high for reliable OCR")
    if glare > 0.60:
        reasons.append("Glare likely to obscure declaration text")
    if overall_score < 0.55:
        reasons.append("Frame quality below acceptance threshold")

    result = {
        "acceptable": not reasons,
        "overall_score": round(overall_score, 4),
        "sharpness_score": round(float(sharpness), 4),
        "blur_score": round(float(blur), 4),
        "glare_score": round(float(glare), 4),
        "image_width": int(width),
        "image_height": int(height),
        "framing_status": "manual_visual_check_required",
        "physical_calibration": {"status": "unavailable", "review_required": True},
        "reasons": reasons,
    }

    try:
        db = get_database()
        append_audit_event(
            db,
            event_type="capture_evaluation",
            entity_type="capture_quality",
            entity_id=source_image_sha256 or "capture_quality",
            actor_role="system",
            action="capture_quality_assessed",
            source_image_sha256=source_image_sha256,
            details={
                "acceptable": result["acceptable"],
                "source_image_sha256": source_image_sha256,
                "overall_score": result["overall_score"],
                "sharpness_score": result["sharpness_score"],
                "blur_score": result["blur_score"],
                "glare_score": result["glare_score"],
                "image_width": result["image_width"],
                "image_height": result["image_height"],
                "reasons": result["reasons"],
            },
        )
    except Exception:
        pass
    return result


def evaluate_capture_bytes(image_bytes: bytes) -> dict[str, Any]:
    """Decode an original upload and attach its exact-byte integrity reference."""
    if not image_bytes:
        raise ValueError("image bytes must not be empty")
    encoded = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("uploaded bytes are not a decodable image")
    source_image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    result = evaluate_capture_quality(image, source_image_sha256=source_image_sha256)
    result["source_image_sha256"] = source_image_sha256
    return result
