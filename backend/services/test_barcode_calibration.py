from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.ocr.paddle_ocr_service import PaddleOCRService
from backend.services.barcode_calibration import calibrate_barcode_scale, calibrate_ean13_scale, decode_ean13_barcodes, validate_ean13
from backend.services.image_integrity import compute_image_sha256


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


def test_opencv_decodes_real_fixture_ean13_with_original_image_geometry():
    image_path = Path(__file__).resolve().parents[2] / "training" / "raw_images" / "Ferrero_back.jpg"
    if not image_path.is_file():
        pytest.skip("real Ferrero barcode fixture is unavailable")
    image_bytes = image_path.read_bytes()
    image = PaddleOCRService.load_image(image_path)

    result = decode_ean13_barcodes(image, source_image_sha256=compute_image_sha256(image_bytes))

    assert result["status"] == "decoded"
    detection = next(item for item in result["detections"] if item["code"] == "8000500003787")
    assert detection["symbology"] == "EAN_13"
    assert detection["checksum_valid"] is True
    assert detection["confidence"] is None
    assert detection["source_image_sha256"] == compute_image_sha256(image_bytes)
    assert len(detection["polygon"]) == 4
    assert detection["bbox"][2] <= image.shape[1]
    assert detection["bbox"][3] <= image.shape[0]


def test_opencv_barcode_dependency_absence_is_explicit(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)

    result = decode_ean13_barcodes(SimpleNamespace(size=1), source_image_sha256="a" * 64)

    assert result["status"] == "unavailable"
    assert result["available"] is False
    assert result["detections"] == []


def test_opencv_decoder_preserves_and_sorts_multiple_gtin_candidates(monkeypatch):
    class Detector:
        def detectAndDecodeWithType(self, _image):
            return True, ["opaque-value", "036000291452", "4006381333931"], ["CODE_128", "UPC_A", "EAN_13"], np.array([
                [[ [5, 5], [30, 5], [30, 20], [5, 20] ]],
                [[ [40, 5], [100, 5], [100, 25], [40, 25] ]],
                [[ [120, 5], [220, 5], [220, 30], [120, 30] ]],
            ], dtype=np.float32)

    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(barcode_BarcodeDetector=Detector))
    result = decode_ean13_barcodes(SimpleNamespace(size=1, shape=(100, 250, 3)), source_image_sha256="a" * 64)

    assert result["status"] == "decoded"
    assert [item["raw_value"] for item in result["detections"]] == ["4006381333931", "036000291452", "opaque-value"]
    assert result["detections"][0]["code"] == "4006381333931"
    assert result["detections"][1]["code"] is None
    assert result["detections"][1]["gtin"] == "036000291452"
    assert result["detections"][2]["gtin_status"] == "MALFORMED_IDENTIFIER"
    assert result["detections"][0]["bbox"] == [120.0, 5.0, 220.0, 30.0]
    assert len({item["barcode_id"] for item in result["detections"]}) == 3


@pytest.mark.parametrize(
    ("fixture", "expected_status"),
    [("Chips_back.jpg", "no_barcode_detected"), ("DarkFantasy_back.jpg", "barcode_unreadable")],
)
def test_real_images_without_decoded_ean_do_not_fabricate_codes(fixture, expected_status):
    image_path = Path(__file__).resolve().parents[2] / "training" / "raw_images" / fixture
    if not image_path.is_file():
        pytest.skip(f"real barcode fixture {fixture} is unavailable")
    image_bytes = image_path.read_bytes()
    image = PaddleOCRService.load_image(image_path)

    result = decode_ean13_barcodes(image, source_image_sha256=compute_image_sha256(image_bytes))

    assert result["status"] == expected_status
    assert all(item["code"] is None and item["checksum_valid"] is False for item in result["detections"])
    if expected_status == "barcode_unreadable":
        assert result["detections"]
        assert result["detections"][0]["polygon"]
