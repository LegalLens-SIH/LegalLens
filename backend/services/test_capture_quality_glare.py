from __future__ import annotations

import cv2
import numpy as np

from backend.services.capture_quality import evaluate_capture_quality


def _make_checkerboard_pattern() -> np.ndarray:
    h, w = 800, 1200
    y, x = np.mgrid[0:h, 0:w]
    pattern = ((x // 40 + y // 40) % 2) * 255
    return np.stack([pattern.astype(np.uint8), pattern.astype(np.uint8), pattern.astype(np.uint8)], axis=-1)


def _make_diagonal_stripes() -> np.ndarray:
    h, w = 800, 1200
    y, x = np.mgrid[0:h, 0:w]
    pattern = ((np.mod(x + y, 80) < 40) * 255).astype(np.uint8)
    return np.stack([pattern, pattern, pattern], axis=-1)


def test_checkerboard_and_diagonal_patterns_are_rejected_for_glare_overexposure():
    for frame in (_make_checkerboard_pattern(), _make_diagonal_stripes()):
        result = evaluate_capture_quality(frame)
        assert result["acceptable"] is False
        assert result["glare_score"] > 0.0
        assert "glare" in result["reasons"][0].lower() or result["glare_score"] > 0.6
