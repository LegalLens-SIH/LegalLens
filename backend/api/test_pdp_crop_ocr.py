from types import SimpleNamespace

import numpy as np

from backend.api import ocr


def test_pdp_crop_ocr_returns_region_local_non_authoritative_evidence_and_cleans_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "PDP_CROP_OCR_ENABLED", True)
    monkeypatch.setattr(ocr, "_UPLOAD_TMP_DIR", tmp_path)
    observed = {}

    class FakeOCR:
        def run(self, path, **kwargs):
            from pathlib import Path
            observed["exists_during_ocr"] = Path(path).is_file()
            observed["kwargs"] = kwargs
            return SimpleNamespace(
                success=True,
                full_text="MRP Rs. 99",
                detections=[SimpleNamespace(model_dump=lambda: {"text": "MRP Rs. 99", "bbox": [1, 2, 8, 4]})],
            )

    monkeypatch.setattr(ocr, "_service", FakeOCR())
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    result = ocr._experimental_pdp_crop_ocr(image, {
        "status": "experimental_candidate", "available": True,
        "detections": [{"region_id": "pdp_1", "class_name": "mrp", "confidence": 0.92, "bbox": [-2, 3, 12, 11]}],
    })

    assert result["status"] == "experimental_candidate"
    assert result["authoritative"] is False
    assert result["used_for_compliance"] is False
    region = result["regions"][0]
    assert region["bbox"] == [0, 3, 12, 11]
    assert region["detection_coordinates"] == "region_local_pixels"
    assert region["full_text"] == "MRP Rs. 99"
    assert region["detections"] == [{"text": "MRP Rs. 99", "bbox": [1, 2, 8, 4]}]
    assert region["authoritative"] is False
    assert region["used_for_compliance"] is False
    assert observed["exists_during_ocr"] is True
    assert observed["kwargs"]["yolo_service"] is None
    assert list(tmp_path.iterdir()) == []


def test_pdp_crop_ocr_stays_inactive_when_disabled_or_candidate_missing(monkeypatch):
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    monkeypatch.setattr(ocr, "PDP_CROP_OCR_ENABLED", False)
    assert ocr._experimental_pdp_crop_ocr(image, {})["status"] == "disabled"

    monkeypatch.setattr(ocr, "PDP_CROP_OCR_ENABLED", True)
    unavailable = ocr._experimental_pdp_crop_ocr(image, {"status": "validation_required", "available": False})
    assert unavailable["status"] == "review_required"
    assert unavailable["regions"] == []
    assert unavailable["used_for_compliance"] is False
