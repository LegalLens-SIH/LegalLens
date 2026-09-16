"""Integration test: real upload -> POST /api/scans -> result path, proving
Authenticity Verification Layer Section 3 (EXIF) is attached to the real
scan response and that it does not alter the OCR/compliance results for an
identical OCR input. Mirrors test_manufacturer.py/test_official.py's exact
conventions (TestClient, real MongoDB + GridFS, dependency_overrides for
auth, only PaddleOCRService.run monkeypatched to avoid a real, slow OCR
inference call - the actual uploaded bytes, including their real EXIF, are
what this test cares about, not OCR accuracy, which is already covered
elsewhere).
"""
from __future__ import annotations

from pathlib import Path

from bson import ObjectId
from fastapi.testclient import TestClient
from gridfs import GridFS

from backend.api import ocr as ocr_module
from backend.api.auth import official_user
from backend.database import get_database
from backend.main import app
from backend.ocr.schemas import Detection, OCRResult

FAKE_OFFICIAL = {
    "_id": ObjectId(),
    "role": "official",
    "fullName": "Authenticity Test Official",
    "email": "test-official-authenticity@example.com",
}

REAL_IMAGE_WITH_EXIF = Path(__file__).resolve().parents[2] / "training" / "raw_images" / "Handwash_back.jpg"

# Fixed, canned OCR result - deliberately NOT derived from the uploaded
# bytes (unlike test_manufacturer.py's _echo_ocr_run, which decodes the
# file as text): the real fixture image here is a genuine binary JPEG, and
# this test's purpose is to prove EXIF attachment + OCR/compliance
# stability, not to re-derive OCR text from real pixels (already covered
# by this project's existing OCR test suite).
_FIXED_TEXT = "MRP Rs. 99 (Inclusive of all taxes)\nNET WEIGHT: 250 g\n"


def _fixed_ocr_run(image_path: str, yolo_service=None, preprocess_options=None) -> OCRResult:
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


def test_scan_upload_attaches_real_exif_evidence_without_changing_compliance(monkeypatch):
    """Before: normal upload -> OCR -> extraction -> compliance (no EXIF
    concept at all). After: normal upload -> EXIF extraction + OCR ->
    extraction -> compliance, with EXIF evidence attached as an
    independent, additive `authenticity` key. This test proves both halves
    at once against the real HTTP path: the real repository fixture image
    (genuine camera EXIF) produces real EXIF evidence in the response, AND
    the `compliance`/`ocr` keys are exactly what the fixed OCR stub alone
    would produce - i.e. identical to what today's pipeline already
    produces for that same OCR input, unaffected by EXIF."""
    if not REAL_IMAGE_WITH_EXIF.exists():
        import pytest
        pytest.skip("real fixture image not present in this checkout")

    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                image_bytes = REAL_IMAGE_WITH_EXIF.read_bytes()
                response = client.post(
                    "/api/scans",
                    files={"file": ("Handwash_back.jpg", image_bytes, "image/jpeg")},
                    data={"product_name": "Authenticity Test Product", "category": "General"},
                )
                assert response.status_code == 200, response.text
                scan = response.json()

                # --- EXIF evidence is present and genuinely reflects the
                # real uploaded image's real camera metadata. ---
                assert "authenticity" in scan
                exif = scan["authenticity"]["exif"]
                assert exif["available"] is True
                assert exif["make"] is not None
                assert exif["model"] is not None
                assert "gps_present" in exif
                assert isinstance(exif["gps_present"], bool)
                # Privacy: no raw coordinate ever reaches the API response.
                assert "gps_latitude" not in exif and "gps_longitude" not in exif and "gps_coordinates" not in exif

                # --- OCR/compliance are exactly what the fixed stub alone
                # produces - unaffected by EXIF being present. ---
                assert scan["full_text"] == _FIXED_TEXT
                assert scan["detection_count"] == 2
                mrp = scan["compliance"]["rule_results"][0]["required_fields"]["maximum_retail_price_mrp"]
                assert mrp["status"] == "PASS"
                assert mrp["value"]["normalized_value"] == 99
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_scan_upload_without_exif_still_succeeds_with_unavailable_evidence(monkeypatch):
    """A synthetic (no-EXIF) upload must produce authenticity.exif.
    available == False, never a failure, and OCR/compliance must still be
    exactly the same as the EXIF-present case above - proving EXIF
    presence/absence never changes the OCR/compliance path at all."""
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                import io
                from PIL import Image
                buf = io.BytesIO()
                Image.new("RGB", (10, 10), color="green").save(buf, format="PNG")
                response = client.post(
                    "/api/scans",
                    files={"file": ("no_exif.png", buf.getvalue(), "image/png")},
                    data={"product_name": "No EXIF Product", "category": "General"},
                )
                assert response.status_code == 200, response.text
                scan = response.json()
                assert scan["authenticity"]["exif"]["available"] is False
                assert scan["full_text"] == _FIXED_TEXT
                mrp = scan["compliance"]["rule_results"][0]["required_fields"]["maximum_retail_price_mrp"]
                assert mrp["status"] == "PASS"
                assert mrp["value"]["normalized_value"] == 99
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)
