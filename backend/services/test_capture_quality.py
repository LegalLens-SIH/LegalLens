from __future__ import annotations

import cv2
import numpy as np

from backend.services.capture_quality import evaluate_capture_quality


def _make_clean_frame() -> np.ndarray:
    h, w = 1200, 900
    y, x = np.mgrid[0:h, 0:w]
    img = 80 + 60 * np.sin(x / 20) + 30 * np.cos(y / 18)
    img = np.clip(img, 0, 255).astype(np.uint8)
    return np.stack([img, img, img], axis=-1)


def _make_blurred_frame() -> np.ndarray:
    clean = _make_clean_frame()
    return cv2.GaussianBlur(clean, (91, 91), 0)


def test_capture_quality_accepts_clear_frame():
    result = evaluate_capture_quality(_make_clean_frame())
    assert result["acceptable"] is True
    assert result["overall_score"] >= 0.75


def test_capture_quality_rejects_blurred_frame():
    result = evaluate_capture_quality(_make_blurred_frame())
    assert result["acceptable"] is False
    assert "blur" in result["reasons"][0].lower() or result["blur_score"] < 0.5


def test_capture_quality_rejects_below_minimum_resolution_and_marks_review_boundaries():
    result = evaluate_capture_quality(np.zeros((240, 320, 3), dtype=np.uint8))
    assert result["acceptable"] is False
    assert result["image_width"] == 320
    assert result["image_height"] == 240
    assert any("resolution" in reason.lower() for reason in result["reasons"])
    assert result["framing_status"] == "manual_visual_check_required"
    assert result["physical_calibration"] == {"status": "unavailable", "review_required": True}
