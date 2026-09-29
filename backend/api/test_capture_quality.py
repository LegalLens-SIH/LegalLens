from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import ocr as ocr_module
from backend.api.auth import official_user
from backend.main import app
from backend.ocr.schemas import Detection, OCRResult
from backend.services import capture_quality as capture_quality_module


OFFICIAL = {"_id": ObjectId(), "role": "official", "email": "capture-quality-test@example.com"}


class FakeAuditCollection:
    def __init__(self):
        self.events = []

    def find_one(self, *_args, **_kwargs):
        return self.events[-1] if self.events else None

    def insert_one(self, event):
        event["_id"] = ObjectId()
        self.events.append(event)
        return SimpleNamespace(inserted_id=event["_id"])


class FakeDatabase:
    def __init__(self):
        self.audit_events = FakeAuditCollection()


def _png_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_capture_quality_endpoint_rejects_small_frame_and_audits_exact_hash(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(ocr_module, "get_database", lambda: database)
    monkeypatch.setattr(capture_quality_module, "get_database", lambda: database)
    app.dependency_overrides[official_user] = lambda: OFFICIAL
    image_bytes = _png_bytes(320, 240)
    expected_hash = hashlib.sha256(image_bytes).hexdigest()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/captures/quality",
                files={"file": ("small.png", image_bytes, "image/png")},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["quality"]["acceptable"] is False
        assert data["quality"]["source_image_sha256"] == expected_hash
        assert data["quality"]["framing_status"] == "manual_visual_check_required"
        assert data["quality"]["physical_calibration"] == {"status": "unavailable", "review_required": True}
        assert len(database.audit_events.events) == 2
        assert all(event["sourceImageSha256"] == expected_hash for event in database.audit_events.events)
        assert data["audit_event_hash"] == database.audit_events.events[-1]["eventHash"]
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_capture_quality_endpoint_rejects_malformed_image_bytes(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(ocr_module, "get_database", lambda: database)
    monkeypatch.setattr(capture_quality_module, "get_database", lambda: database)
    app.dependency_overrides[official_user] = lambda: OFFICIAL
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/captures/quality",
                files={"file": ("invalid.png", b"not an image", "image/png")},
            )
        assert response.status_code == 400
        assert not database.audit_events.events
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_capture_text_visibility_retains_bounded_paddle_evidence_as_review_only():
    result = OCRResult(
        success=True,
        image="label.png",
        image_dimensions={"width": 100, "height": 80},
        detections=[Detection(text="NET WT 250g", confidence=0.91, bbox=[4, 8, 70, 20], polygon=[[4, 8], [70, 8], [70, 20], [4, 20]])],
        detection_count=1,
    )

    visibility = ocr_module._capture_text_visibility(result)

    assert visibility["status"] == "regions_detected"
    assert visibility["detected_region_count"] == 1
    assert visibility["evidence_regions"][0]["bbox"] == [4, 8, 70, 20]
    assert visibility["coordinate_space"] == "paddleocr_input_pixels"
    assert visibility["used_for_compliance"] is False


def test_capture_text_visibility_does_not_treat_no_detections_as_absence():
    result = OCRResult(success=True, image="label.png", detections=[], detection_count=0)

    visibility = ocr_module._capture_text_visibility(result)

    assert visibility["status"] == "not_detected_review_required"
    assert "does not establish" in visibility["guidance"]
    assert visibility["used_for_compliance"] is False