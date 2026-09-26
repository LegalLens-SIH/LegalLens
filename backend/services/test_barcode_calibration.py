from __future__ import annotations

import pytest

from backend.services.barcode_calibration import calibrate_barcode_scale, calibrate_ean13_scale, validate_ean13


def test_ean13_validation_accepts_known_code():
    assert validate_ean13("4006381333931") is True
    assert validate_ean13("1234567890128") is True
    assert validate_ean13("1234567890123") is False


def test_barcode_calibration_returns_feasible_scale_and_distortion():
    result = calibrate_barcode_scale(barcode_width_px=420, reference_width_mm=30, observed_width_px=400)
    assert result["scale_px_per_mm"] > 0
    assert result["distortion_score"] >= 0
    assert result["acceptable"] is True


def _calibrate(**overrides):
    arguments = {
        "code": "4006381333931",
        "polygon": [[10, 10], [410, 10], [410, 110], [10, 110]],
        "image_shape": (500, 500),
        "reference_width_mm": 40,
        "reference_source": "verified package specification",
        "evidence": {"value": "4006381333931", "region_id": "region_1", "confidence": 0.96},
    }
    arguments.update(overrides)
    return calibrate_ean13_scale(**arguments)


def test_ean13_calibration_converts_geometry_and_preserves_evidence():
    result = _calibrate()
    assert result["status"] == "calibrated"
    assert result["scale_px_per_mm"] == 10.0
    assert result["confidence"] == 0.96
    assert result["source_evidence"]["region_id"] == "region_1"
    assert result["reference"]["source"] == "verified package specification"


@pytest.mark.parametrize("code", [None, "", "4006381333930", "400638133393"])
def test_invalid_or_ambiguous_ean13_is_not_calibrated(code):
    result = _calibrate(code=code)
    assert result["status"] == "review_required"
    assert result["scale_px_per_mm"] is None


def test_missing_reference_or_source_evidence_is_not_calibrated():
    assert _calibrate(reference_width_mm=None)["scale_px_per_mm"] is None
    assert _calibrate(reference_source=None)["scale_px_per_mm"] is None
    assert _calibrate(evidence=None)["scale_px_per_mm"] is None


@pytest.mark.parametrize("polygon", [
    None,
    [[0, 0], [0, 0], [0, 0], [0, 0]],
    [[0, 0], [20, 0], [10, 10], [20, 20]],
    [[10, 10], [410, 10], [600, 110], [10, 110]],
])
def test_missing_degenerate_out_of_bounds_or_distorted_geometry_is_rejected(polygon):
    result = _calibrate(polygon=polygon)
    assert result["status"] == "review_required"
    assert result["scale_px_per_mm"] is None


@pytest.mark.parametrize("reference_width_mm", [0, -1, float("inf"), float("nan")])
def test_nonpositive_or_nonfinite_reference_dimensions_are_rejected(reference_width_mm):
    result = _calibrate(reference_width_mm=reference_width_mm)
    assert result["status"] == "review_required"
    assert result["scale_px_per_mm"] is None


def test_mismatched_code_or_invalid_confidence_is_rejected():
    assert _calibrate(evidence={"value": "4006381333930", "confidence": 0.9})["scale_px_per_mm"] is None
    assert _calibrate(evidence={"value": "4006381333931", "confidence": 1.1})["scale_px_per_mm"] is None


def test_barcode_confidence_threshold_boundary():
    assert _calibrate(evidence={"value": "4006381333931", "confidence": 0.8999})["scale_px_per_mm"] is None
    assert _calibrate(evidence={"value": "4006381333931", "confidence": 0.90})["status"] == "calibrated"
