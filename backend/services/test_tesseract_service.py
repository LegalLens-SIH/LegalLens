from __future__ import annotations

from types import SimpleNamespace
import sys

import numpy as np

import backend.ocr.tesseract_service as tesseract_module
from backend.ocr.tesseract_service import TesseractOCRService


def test_tesseract_service_initializes_and_reports_no_image_error():
    service = TesseractOCRService()
    result = service.run(None)
    assert result["success"] is False
    assert result["error_code"] in {"missing_input", "not_available"}


def test_tesseract_service_reports_not_available_when_binary_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda _name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("TESSERACT_BINARY_PATH", "")
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
        get_languages=lambda **_kwargs: ["eng"],
        image_to_data=lambda image, **kwargs: data,
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    service = TesseractOCRService(enabled=True, binary_path=sys.executable)

    result = service.run(np.zeros((100, 200, 3), dtype=np.uint8))

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
        get_languages=lambda **_kwargs: ["eng"],
        image_to_data=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("runtime unavailable")),
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    result = TesseractOCRService(enabled=True, binary_path=sys.executable).run(np.zeros((20, 20, 3), dtype=np.uint8))
    assert result["success"] is False
    assert result["error_code"] == "ocr_failed"
    assert result["provenance"] == "secondary_review_only"


def test_tesseract_discovers_binary_and_passes_language_psm_timeout_and_detection_limit(monkeypatch):
    captured = {}
    data = {
        "text": ["one", "two"], "conf": [90, 80], "left": [0, 10], "top": [0, 0],
        "width": [8, 8], "height": [5, 5], "block_num": [1, 1], "par_num": [1, 1], "line_num": [1, 2],
    }

    def image_to_data(_image, **kwargs):
        captured.update(kwargs)
        return data

    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=None),
        Output=SimpleNamespace(DICT="dict"),
        get_languages=lambda **_kwargs: ["eng", "fra"],
        image_to_data=image_to_data,
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda name: sys.executable if name == "tesseract" else None)
    monkeypatch.setenv("TESSERACT_ENABLED", "true")
    monkeypatch.setenv("TESSERACT_BINARY_PATH", "")
    monkeypatch.setenv("TESSERACT_LANGUAGE", "eng+fra")
    monkeypatch.setenv("TESSERACT_PSM", "11")
    monkeypatch.setenv("TESSERACT_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("TESSERACT_MAX_PIXELS", "10000")
    monkeypatch.setenv("TESSERACT_MAX_DETECTIONS", "1")

    service = TesseractOCRService()
    result = service.run(np.zeros((20, 20, 3), dtype=np.uint8))

    assert service.enabled is True
    assert result["success"] is True
    assert result["detection_count"] == 1
    assert captured["lang"] == "eng+fra"
    assert captured["config"] == "--oem 3 --psm 11"
    assert captured["timeout"] == 4.0


def test_tesseract_reports_an_explicitly_configured_invalid_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(tesseract_module, "pytesseract", SimpleNamespace())
    result = TesseractOCRService(enabled=True, binary_path=str(tmp_path / "missing-tesseract.exe")).run(
        np.zeros((20, 20, 3), dtype=np.uint8)
    )
    assert result["error_code"] == "invalid_binary"
    assert result["provenance"] == "secondary_review_only"


def test_tesseract_discovers_default_windows_install_when_not_on_path(monkeypatch, tmp_path):
    binary = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"test executable path")
    monkeypatch.setattr(tesseract_module.shutil, "which", lambda _name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))

    service = TesseractOCRService(enabled=True)

    assert service.tesseract_binary == str(binary)
    assert service.enabled is True


def test_tesseract_reports_missing_language_data_before_ocr(monkeypatch):
    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=None),
        Output=SimpleNamespace(DICT="dict"),
        get_languages=lambda **_kwargs: ["eng"],
        image_to_data=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OCR must not run")),
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    monkeypatch.setenv("TESSERACT_LANGUAGE", "fra")

    result = TesseractOCRService(enabled=True, binary_path=sys.executable).run(
        np.zeros((20, 20, 3), dtype=np.uint8)
    )

    assert result["success"] is False
    assert result["error_code"] == "language_unavailable"
    assert result["missing_languages"] == ["fra"]
    assert result["provenance"] == "secondary_review_only"


def test_tesseract_timeout_and_malformed_output_have_explicit_states(monkeypatch):
    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=None),
        Output=SimpleNamespace(DICT="dict"),
        get_languages=lambda **_kwargs: ["eng"],
        image_to_data=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Tesseract process timeout")),
    )
    monkeypatch.setattr(tesseract_module, "pytesseract", fake_pytesseract)
    timeout = TesseractOCRService(enabled=True, binary_path=sys.executable).run(np.zeros((20, 20, 3), dtype=np.uint8))
    assert timeout["error_code"] == "timeout"
    assert timeout["provenance"] == "secondary_review_only"

    fake_pytesseract.image_to_data = lambda *_args, **_kwargs: {"text": ["bad"]}
    malformed = TesseractOCRService(enabled=True, binary_path=sys.executable).run(np.zeros((20, 20, 3), dtype=np.uint8))
    assert malformed["error_code"] == "malformed_output"
    assert malformed["provenance"] == "secondary_review_only"
