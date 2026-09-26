from __future__ import annotations

from types import SimpleNamespace

import backend.ocr.tesseract_service as tesseract_module
from backend.ocr.tesseract_service import TesseractOCRService


def test_tesseract_service_initializes_and_reports_no_image_error():
    service = TesseractOCRService()
    result = service.run(None)
    assert result["success"] is False
    assert result["error_code"] in {"missing_input", "not_available"}


def test_tesseract_service_reports_not_available_when_binary_is_missing():
    service = TesseractOCRService(enabled=True, binary_path=None)
    result = service.run(object())
    assert result["success"] is False
    assert result["error_code"] == "not_available"
    assert "disabled or unavailable" in result["message"]


def test_tesseract_is_opt_in_by_default(monkeypatch):
    monkeypatch.delenv("TESSERACT_ENABLED", raising=False)
    service = TesseractOCRService(binary_path="tesseract")
    assert service.requested is False
    assert service.enabled is False


def test_tesseract_extracts_line_boxes_confidence_and_provenance(monkeypatch):
    data = {
        "text": ["MRP", "Rs.", "99", "NET", "250g", ""],
        "conf": [90, 80, 100, 70, 60, -1],
        "left": [10, 50, 75, 10, 55, 0],
        "top": [20, 20, 20, 60, 60, 0],
        "width": [35, 20, 15, 30, 35, 0],
        "height": [12, 12, 12, 14, 14, 0],
        "block_num": [1, 1, 1, 1, 1, 1],
        "par_num": [1, 1, 1, 1, 1, 1],
        "line_num": [1, 1, 1, 2, 2, 3],
    }
    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=None),
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda image, **kwargs: data,
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    service = TesseractOCRService(enabled=True, binary_path="tesseract")

    result = service.run(object())

    assert result["success"] is True
    assert result["full_text"] == "MRP Rs. 99\nNET 250g"
    assert result["detection_count"] == 2
    assert result["detections"][0]["bbox"] == [10, 20, 90, 32]
    assert result["detections"][0]["confidence"] == 0.9
    assert result["detections"][0]["polygon"] == [[10, 20], [90, 20], [90, 32], [10, 32]]
    assert result["detections"][0]["provenance"] == ["tesseract"]


def test_tesseract_runtime_failure_returns_explicit_degraded_result(monkeypatch):
    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=None),
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("runtime unavailable")),
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    result = TesseractOCRService(enabled=True, binary_path="tesseract").run(object())
    assert result["success"] is False
    assert result["error_code"] == "ocr_failed"
    assert result["provenance"] == "secondary_review_only"
