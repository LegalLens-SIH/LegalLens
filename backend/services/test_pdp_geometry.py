from __future__ import annotations

from backend.services.pdp_geometry import evaluate_pdp_geometry


def test_pdp_geometry_scores_reasonable_panel_cover():
    boxes = [
        {"x1": 50, "y1": 40, "x2": 850, "y2": 650},
        {"x1": 100, "y1": 100, "x2": 500, "y2": 300},
    ]
    result = evaluate_pdp_geometry(boxes, image_shape=(800, 900))
    assert result["panel_coverage_ratio"] > 0.4
    assert result["status"] in {"eligible", "review_required"}


def test_overlapping_boxes_count_union_area_only():
    boxes = [
        {"x1": 0, "y1": 0, "x2": 600, "y2": 500},
        {"x1": 300, "y1": 0, "x2": 900, "y2": 500},
    ]
    result = evaluate_pdp_geometry(boxes, image_shape=(1000, 1000))
    assert result["panel_coverage_ratio"] == 0.45


def test_geometry_preserves_original_evidence_and_supports_perspective_polygon():
    region = {
        "region_id": "pdp_front",
        "evidence_region_ids": ["ocr_region_4"],
        "polygon": [[100, 100], [900, 140], [880, 700], [120, 680]],
        "source": "image_localization",
    }
    result = evaluate_pdp_geometry([region], image_shape=(800, 1000))
    assert result["status"] == "eligible"
    assert result["evidence_regions"][0]["region_id"] == "pdp_front"
    assert result["evidence_regions"][0]["evidence_region_ids"] == ["ocr_region_4"]
    assert result["evidence_regions"][0]["source_evidence"] == region


def test_invalid_degenerate_and_out_of_image_coordinates_route_to_review():
    for boxes in (
        [{"x1": 20, "y1": 20, "x2": 20, "y2": 30}],
        [{"x1": 0, "y1": 0, "x2": 500, "y2": float("inf")}],
        [{"x1": -1, "y1": 0, "x2": 500, "y2": 500}],
        [{"polygon": [[0, 0], [100, 100], [0, 100], [100, 0]]}],
    ):
        result = evaluate_pdp_geometry(boxes, image_shape=(1000, 1000))
        assert result["status"] == "review_required"
        assert result["panel_coverage_ratio"] is None
        assert result["reasons"]


def test_invalid_image_dimensions_route_to_review():
    result = evaluate_pdp_geometry([{"x1": 0, "y1": 0, "x2": 10, "y2": 10}], image_shape=(0, 100))
    assert result["status"] == "review_required"
    assert result["panel_coverage_ratio"] is None
