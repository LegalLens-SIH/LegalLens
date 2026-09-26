from __future__ import annotations

from backend.services.barcode_calibration import calibrate_barcode_scale, validate_ean13


def test_distorted_barcode_has_low_confidence_and_is_not_accepted():
    valid = validate_ean13("4006381333931")
    assert valid is True

    result = calibrate_barcode_scale(barcode_width_px=420, reference_width_mm=30, observed_width_px=290)
    assert result["acceptable"] is False
    assert result["distortion_score"] > 0.25
    assert result["distortion_score"] < 0.75


def test_undistorted_barcode_keeps_high_acceptance():
    result = calibrate_barcode_scale(barcode_width_px=420, reference_width_mm=30, observed_width_px=400)
    assert result["acceptable"] is True
    assert result["distortion_score"] < 0.25
