from __future__ import annotations

from backend.services.legibility_validator import measure_ocr_detection, validate_legibility


def test_legibility_validator_converts_px_to_mm_and_uses_calibration():
    outcome = validate_legibility(
        text_height_px=45,
        calibration={"scale_px_per_mm": 15.0},
        geometry={"status": "eligible", "panel_coverage_ratio": 0.42},
        minimum_font_height_mm=2.0,
    )

    assert outcome["estimated_height_mm"] == 3.0
    assert outcome["status"] == "eligible"
    assert outcome["review_reason"] is None


def test_legibility_validator_defaults_to_review_when_font_threshold_is_unverified():
    outcome = validate_legibility(
        text_height_px=45,
        calibration={"scale_px_per_mm": 15.0},
        geometry={"status": "eligible", "panel_coverage_ratio": 0.42},
    )

    assert outcome["status"] == "review_required"
    assert "threshold" in outcome["review_reason"].lower()


def test_legibility_validator_routes_geometry_failures_to_review():
    outcome = validate_legibility(
        text_height_px=45,
        calibration={"scale_px_per_mm": 15.0},
        geometry={"status": "review_required", "panel_coverage_ratio": 0.10},
        minimum_font_height_mm=2.0,
    )

    assert outcome["status"] == "review_required"
    assert "geometry" in outcome["review_reason"].lower()


def test_missing_or_invalid_calibration_returns_review_without_physical_measurement():
    for calibration in (None, {}, {"scale_px_per_mm": 0}, {"scale_px_per_mm": -1}, {"status": "review_required", "scale_px_per_mm": 10}):
        outcome = validate_legibility(text_height_px=45, calibration=calibration, geometry={"status": "eligible"}, minimum_font_height_mm=2.0)
        assert outcome["status"] == "review_required"
        assert outcome["estimated_height_mm"] is None


def test_zero_or_tiny_invalid_ocr_boxes_are_unavailable():
    outcome = measure_ocr_detection(
        detection={"text": "MRP", "confidence": 0.95, "bbox": [5, 5, 5, 10]},
        calibration={"scale_px_per_mm": 10},
    )
    assert outcome["status"] == "review_required"
    assert outcome["estimated_height_mm"] is None


def test_rotated_ocr_polygon_is_measured_from_shorter_edges_and_preserves_evidence():
    detection = {
        "text": "MRP Rs. 99",
        "confidence": 0.94,
        "bbox": [10, 10, 50, 60],
        "polygon": [[20, 10], [60, 20], [50, 60], [10, 50]],
    }
    outcome = measure_ocr_detection(
        detection=detection,
        calibration={"status": "calibrated", "scale_px_per_mm": 10, "confidence": 0.96, "source_evidence": {"region_id": "barcode-1"}},
        minimum_font_height_mm=4.0,
        evidence_region_ids=["ocr-region-2"],
        image_shape=(100, 100),
    )
    assert outcome["pixel_height_px"] == 41.23105625617661
    assert outcome["estimated_height_mm"] == 4.1231
    assert outcome["status"] == "eligible"
    assert outcome["ocr_evidence"]["text"] == detection["text"]
    assert outcome["ocr_evidence"]["region_ids"] == ["ocr-region-2"]


def test_missing_calibration_and_out_of_image_box_never_produce_mm():
    detection = {"text": "MRP", "confidence": 0.9, "bbox": [-1, 0, 30, 20]}
    outcome = measure_ocr_detection(detection=detection, calibration=None, image_shape=(100, 100))
    assert outcome["status"] == "review_required"
    assert outcome["estimated_height_mm"] is None
    assert outcome["pixel_height_px"] is None
    assert outcome["ocr_evidence"]["text"] == "MRP"


def test_threshold_boundary_is_deterministic():
    outcome = validate_legibility(
        text_height_px=30,
        calibration={"status": "calibrated", "scale_px_per_mm": 10},
        geometry={"status": "eligible"},
        minimum_font_height_mm=3.0,
    )
    assert outcome["estimated_height_mm"] == 3.0
    assert outcome["status"] == "eligible"
