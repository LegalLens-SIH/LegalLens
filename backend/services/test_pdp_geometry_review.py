from __future__ import annotations

from backend.services.pdp_geometry import evaluate_pdp_geometry


def test_missing_or_invalid_calibration_routes_to_officer_review():
    result = evaluate_pdp_geometry([], image_shape=(800, 900))
    assert result["status"] == "review_required"
    assert result["reasons"] == ["No panel boxes detected"]

    low_coverage = [{"x1": 10, "y1": 10, "x2": 120, "y2": 130}]
    result = evaluate_pdp_geometry(low_coverage, image_shape=(800, 900))
    assert result["status"] == "review_required"
    assert result["reasons"] == ["Panel geometry below minimum coverage"]
