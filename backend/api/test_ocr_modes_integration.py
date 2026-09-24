"""
Integration test: Phase 2B OCR mode selection through the real
POST /api/scans HTTP path. Mirrors test_ocr_authenticity_integration.py's
exact conventions (TestClient, real MongoDB, dependency_overrides,
PaddleOCRService.run monkeypatched to avoid a real, slow OCR call) - here
the stub RECORDS the `preprocess_options` it was called with, so these
tests prove the mode -> PreprocessOptions wiring end-to-end rather than
only at the ocr_modes.py unit level.
"""

from __future__ import annotations

from bson import ObjectId
from fastapi.testclient import TestClient
from gridfs import GridFS

from backend.api import ocr as ocr_module
from backend.api.auth import official_user
from backend.database import get_database
from backend.main import app
from backend.ocr.preprocessing import PreprocessOptions
from backend.ocr.schemas import Detection, OCRResult

FAKE_OFFICIAL = {
    "_id": ObjectId(),
    "role": "official",
    "fullName": "OCR Mode Test Official",
    "email": "test-official-ocr-mode@example.com",
}

_FIXED_TEXT = "MRP Rs. 99 (Inclusive of all taxes)\nNET WEIGHT: 250 g\n"

_recorded_preprocess_options: list = []


def _recording_ocr_run(image_path: str, yolo_service=None, preprocess_options=None) -> OCRResult:
    _recorded_preprocess_options.append(preprocess_options)
    lines = [line for line in _FIXED_TEXT.splitlines() if line.strip()]
    detections = [Detection(text=line, confidence=0.95, bbox=[0, 0, 10, 10], polygon=[[0, 0], [10, 0], [10, 10], [0, 10]]) for line in lines]
    return OCRResult(success=True, image=image_path, full_text=_FIXED_TEXT, detections=detections, detection_count=len(detections))


def _cleanup(db) -> None:
    scans = list(db.scans.find({"userId": FAKE_OFFICIAL["_id"]}, {"sourceImageId": 1}))
    fs = GridFS(db, collection="scan_images")
    for scan in scans:
        image_id = scan.get("sourceImageId")
        if image_id:
            try:
                fs.delete(ObjectId(image_id))
            except Exception:
                pass
    db.scans.delete_many({"userId": FAKE_OFFICIAL["_id"]})
    db.history.delete_many({"userId": FAKE_OFFICIAL["_id"]})


def _tiny_png_bytes() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="green").save(buf, format="PNG")
    return buf.getvalue()


def _upload(client: TestClient, extra_data: dict | None = None):
    data = {"product_name": "Mode Test Product", "category": "General"}
    if extra_data:
        data.update(extra_data)
    return client.post("/api/scans", files={"file": ("a.png", _tiny_png_bytes(), "image/png")}, data=data)


def test_omitting_ocr_mode_uses_existing_default_baseline_preprocess_options(monkeypatch):
    """No ocr_mode field at all (today's existing frontend behavior) must
    produce EXACTLY the module's current live `_preprocess_options` -
    proving zero behavior change for every caller that doesn't opt in,
    whatever that baseline currently is (as of 2026-09-17, that baseline
    unconditionally includes resize=True - see ocr.py's module docstring
    on _preprocess_options)."""
    _recorded_preprocess_options.clear()
    monkeypatch.setattr(ocr_module._service, "run", _recording_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                response = _upload(client)
                assert response.status_code == 200, response.text
                assert response.json()["ocrMode"] == "standard"
                assert _recorded_preprocess_options == [ocr_module._preprocess_options]
                assert ocr_module._preprocess_options.resize is True
                assert ocr_module._preprocess_options.max_side == 2000
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_explicit_standard_mode_matches_existing_baseline(monkeypatch):
    _recorded_preprocess_options.clear()
    monkeypatch.setattr(ocr_module._service, "run", _recording_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                response = _upload(client, {"ocr_mode": "standard"})
                assert response.status_code == 200, response.text
                assert _recorded_preprocess_options == [ocr_module._preprocess_options]
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_high_accuracy_mode_forces_resize_plus_denoise_and_contrast(monkeypatch):
    """high_accuracy must include resize (the new universal baseline) ON
    TOP of denoise+contrast - never a bundle that drops resize and thereby
    regresses relative to "standard" on the one dimension every mode now
    shares."""
    _recorded_preprocess_options.clear()
    monkeypatch.setattr(ocr_module._service, "run", _recording_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                response = _upload(client, {"ocr_mode": "high_accuracy"})
                assert response.status_code == 200, response.text
                assert response.json()["ocrMode"] == "high_accuracy"
                assert _recorded_preprocess_options == [PreprocessOptions(resize=True, max_side=2000, denoise=True, enhance_contrast=True)]
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_fast_mode_is_retired_and_rejected_end_to_end(monkeypatch):
    """"fast" was removed once resize (its one distinguishing feature)
    became the unconditional baseline every mode gets - see ocr_modes.py.
    A client still sending ocr_mode="fast" must get an explicit 400
    through the real HTTP path, never a silent fallback to "standard"
    that would hide the behavior change, and OCR must never even run."""
    _recorded_preprocess_options.clear()
    monkeypatch.setattr(ocr_module._service, "run", _recording_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                response = _upload(client, {"ocr_mode": "fast"})
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "invalid_ocr_mode"
                assert _recorded_preprocess_options == []
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_invalid_ocr_mode_is_rejected_with_400_and_never_reaches_ocr(monkeypatch):
    _recorded_preprocess_options.clear()
    monkeypatch.setattr(ocr_module._service, "run", _recording_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                response = _upload(client, {"ocr_mode": "gpu_turbo_unsafe"})
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "invalid_ocr_mode"
                assert _recorded_preprocess_options == []
                assert get_database().scans.count_documents({"userId": FAKE_OFFICIAL["_id"]}) == 0
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_ocr_mode_never_changes_compliance_result_for_identical_ocr_output(monkeypatch):
    """Different modes only change PREPROCESSING before the (stubbed,
    identical-output) OCR call - the downstream compliance result for the
    same OCR text must be identical across modes, proving mode selection
    cannot itself alter a compliance verdict."""
    monkeypatch.setattr(ocr_module._service, "run", _recording_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                results = {}
                for mode in ("standard", "high_accuracy"):
                    response = _upload(client, {"ocr_mode": mode})
                    assert response.status_code == 200, response.text
                    results[mode] = response.json()["compliance"]["overall_status"]
                assert len(set(results.values())) == 1
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)
