from __future__ import annotations

from backend.ocr.ensemble import run_ensemble


def test_ensemble_weighted_vote_with_both_tracks_and_disagreement():
    paddle = {
        "success": True,
        "detections": [
            {"text": "MRP 199", "confidence": 0.82, "bbox": [10, 10, 110, 30], "polygon": [[10, 10], [110, 10], [110, 30], [10, 30]]},
            {"text": "NET WT 500g", "confidence": 0.88, "bbox": [30, 60, 150, 90], "polygon": [[30, 60], [150, 60], [150, 90], [30, 90]]},
        ],
    }
    tesseract = {
        "success": True,
        "detections": [
            {"text": "MRP 299", "confidence": 0.69, "bbox": [12, 8, 112, 32], "polygon": [[12, 8], [112, 8], [112, 32], [12, 32]]},
            {"text": "NET WT 500g", "confidence": 0.71, "bbox": [32, 62, 152, 92], "polygon": [[32, 62], [152, 62], [152, 92], [32, 92]]},
        ],
    }

    result = run_ensemble(paddle, tesseract, entity_id="demo-ensemble")
    assert result["status"] == "ok"
    assert any(item["text"] == "MRP 199" for item in result["regions"])
    assert result["provenance"][0]["sources"] == ["paddleocr", "tesseract"]
    assert result["regions"][0]["provenance"] == ["paddleocr", "tesseract"]


def test_ensemble_single_track_graceful_degradation():
    paddle = {
        "success": True,
        "detections": [
            {"text": "MRP 199", "confidence": 0.86, "bbox": [20, 20, 180, 52], "polygon": [[20, 20], [180, 20], [180, 52], [20, 52]]},
        ],
    }

    result = run_ensemble(paddle, {"success": False, "error_code": "not_available"}, entity_id="demo-single")
    assert result["status"] == "ok"
    assert result["mode"] == "paddleocr_only"
    assert result["regions"][0]["text"] == "MRP 199"
    assert result["regions"][0]["provenance"] == ["paddleocr"]


def test_ensemble_iou_threshold_blocks_bad_links():
    paddle = {"success": True, "detections": [{"text": "A", "confidence": 0.85, "bbox": [0, 0, 10, 10], "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}]}
    tesseract = {"success": True, "detections": [{"text": "B", "confidence": 0.75, "bbox": [50, 50, 60, 60], "polygon": [[50, 50], [60, 50], [60, 60], [50, 60]]}]}

    result = run_ensemble(paddle, tesseract, entity_id="demo-iou")
    assert result["status"] == "ok"
    assert len(result["regions"]) == 2
    assert {item["text"] for item in result["regions"]} == {"A", "B"}


def test_zero_confidence_overlapping_detections_are_not_weighted_together():
    paddle = {"success": True, "detections": [{"text": "A", "confidence": 0, "bbox": [0, 0, 10, 10]}]}
    tesseract = {"success": True, "detections": [{"text": "B", "confidence": 0, "bbox": [0, 0, 10, 10]}]}
    result = run_ensemble(paddle, tesseract)
    assert len(result["regions"]) == 2
    assert {item["text"] for item in result["regions"]} == {"A", "B"}


def test_ensemble_preserves_provenance_and_emits_audit_event():
    paddle = {"success": True, "detections": [{"text": "NET WT 500g", "confidence": 0.90, "bbox": [5, 5, 100, 20], "polygon": [[5, 5], [100, 5], [100, 20], [5, 20]]}]}
    tesseract = {"success": True, "detections": [{"text": "NET WT 500g", "confidence": 0.88, "bbox": [6, 6, 101, 21], "polygon": [[6, 6], [101, 6], [101, 21], [6, 21]]}]}

    result = run_ensemble(paddle, tesseract, db=None, entity_id="demo-audit")
    assert result["status"] == "ok"
    assert result["regions"][0]["provenance"] == ["paddleocr", "tesseract"]
    assert result["audit_event"]["eventType"] == "ocr_ensemble_run"
