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

import asyncio
import hashlib
from io import BytesIO
from pathlib import Path

from bson import ObjectId
from fastapi.testclient import TestClient
from gridfs import GridFS
from PIL import Image

from backend.api import ocr as ocr_module
from backend.api.auth import official_user
from backend.database import connect_to_mongodb, get_database
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
    scans = list(db.scans.find({"userId": FAKE_OFFICIAL["_id"]}, {"sourceImageId": 1, "sourceImages": 1}))
    fs = GridFS(db, collection="scan_images")
    for scan in scans:
        image_ids = {scan.get("sourceImageId")}
        image_ids.update(image.get("id") for image in scan.get("sourceImages", []) if image.get("id"))
        for image_id in image_ids - {None}:
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


def test_scan_upload_sha256_hash_matches_exact_uploaded_bytes(monkeypatch):
    """Phase 1, Requirement 6, through the real HTTP path: the persisted
    scan's sourceImageSha256 must equal hashlib.sha256 of the EXACT bytes
    uploaded - proving the hash reflects the real upload (computed before
    run_ocr()/preprocessing, which here returns a fixed, unrelated canned
    OCR text - the hash is correct regardless of what OCR "saw", since it
    never depends on OCR output at all)."""
    import hashlib

    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                image_bytes = REAL_IMAGE_WITH_EXIF.read_bytes()
                expected_hash = hashlib.sha256(image_bytes).hexdigest()
                response = client.post(
                    "/api/scans",
                    files={"file": ("Handwash_back.jpg", image_bytes, "image/jpeg")},
                    data={"product_name": "Hash Test Product", "category": "General"},
                )
                assert response.status_code == 200, response.text
                scan = response.json()
                assert scan["sourceImageSha256"] == expected_hash
                assert len(scan["sourceImageSha256"]) == 64
                assert scan["measurementReview"]["status"] == "review_required"
                assert scan["measurementReview"]["available"] is False
                assert scan["fontHeightMeasurements"][0]["estimated_height_mm"] is None
                assert scan["fontHeightMeasurements"][0]["ocr_evidence"]["region_ids"] == ["region_0"]

                database = get_database()
                package = database.evidence_packages.find_one({"package_id": scan["evidencePackageId"]})
                assert package is not None
                artifacts = {artifact["artifact_id"]: artifact for artifact in package["artifacts"]}
                assert artifacts["source_image_0"]["sha256"] == expected_hash
                assert artifacts["source_image_0"]["size"] == len(image_bytes)
                assert artifacts["authenticity"]["derived_from"] == ["source_image_0"]
                assert "fontHeightMeasurements" in artifacts
                assert package["merkle_root"] == scan["evidenceMerkleRoot"]

                authenticity_audit = database.audit_events.find_one({
                    "entityId": scan["id"],
                    "eventType": "image_authenticity_analysis",
                })
                assert authenticity_audit is not None
                assert authenticity_audit["sourceImageSha256"] == expected_hash
                assert authenticity_audit["eventHash"] in package["audit_refs"]
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_scan_upload_sniffs_mime_type_and_records_ela_evidence(monkeypatch):
    """The response should expose the actual sniffed MIME type and the
    additive ELA evidence object, even when the upload payload is a real
    camera JPEG with EXIF metadata. This proves both evidence recording and
    the privacy-safe handling of the metadata object."""
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                image_bytes = REAL_IMAGE_WITH_EXIF.read_bytes()
                response = client.post(
                    "/api/scans",
                    files={"file": ("Handwash_back.jpg", image_bytes, "image/jpeg")},
                    data={"product_name": "Evidence Test Product", "category": "General"},
                )
                assert response.status_code == 200, response.text
                scan = response.json()
                assert scan["sourceMimeType"] == "image/jpeg"
                assert scan["authenticity"]["ela"]["available"] is True
                assert scan["authenticity"]["ela"]["normalized_score"] is not None
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_multi_image_scan_records_panel_hashes(monkeypatch):
    """Multi-image scans must retain a per-panel SHA-256 list alongside the
    combined scan, so each uploaded side can be individually audited."""
    front_bytes = b"front-bytes-12345"
    back_bytes = b"back-bytes-67890"

    def fake_ocr_for_panel(image_path: str, yolo_service=None, preprocess_options=None) -> OCRResult:
        text = "MRP Rs. 99\nNET WEIGHT: 250 g\n"
        detections = [Detection(text=text, confidence=0.95, bbox=[0, 0, 10, 10], polygon=[[0, 0], [10, 0], [10, 10], [0, 10]])]
        return OCRResult(success=True, image=image_path, full_text=text, detections=detections, detection_count=1)

    monkeypatch.setattr(ocr_module, "_run_image_ocr", fake_ocr_for_panel)
    connect_to_mongodb()
    try:
        images = [
            {"contents": front_bytes, "filename": "front.png", "content_type": "image/png", "side": "Front"},
            {"contents": back_bytes, "filename": "back.png", "content_type": "image/png", "side": "Back"},
        ]
        asyncio.run(ocr_module._run_multi_analysis_job("TEST-JOB-001", images, "Test Product", "General", "ACME", {"_id": FAKE_OFFICIAL["_id"], "role": "official"}))
        scan = get_database().scans.find_one({"userId": FAKE_OFFICIAL["_id"]})
        assert scan is not None
        assert scan["panelHashes"] == [
            {"side": "Front", "fileName": "front.png", "sha256": hashlib.sha256(front_bytes).hexdigest()},
            {"side": "Back", "fileName": "back.png", "sha256": hashlib.sha256(back_bytes).hexdigest()},
        ]
        assert scan["sourceMimeType"] == "image/png"
        assert scan["measurementReview"]["status"] == "review_required"
        assert scan["fontHeightMeasurements"][0]["estimated_height_mm"] is None
        assert len(scan["sourceImages"]) == 2
        assert [image["sha256"] for image in scan["sourceImages"]] == [
            hashlib.sha256(front_bytes).hexdigest(),
            hashlib.sha256(back_bytes).hexdigest(),
        ]

        database = get_database()
        package = database.evidence_packages.find_one({"package_id": scan["evidencePackageId"]})
        assert package is not None
        artifacts = {artifact["artifact_id"]: artifact for artifact in package["artifacts"]}
        assert artifacts["source_image_0"]["sha256"] == hashlib.sha256(front_bytes).hexdigest()
        assert artifacts["source_image_1"]["sha256"] == hashlib.sha256(back_bytes).hexdigest()
        assert artifacts["authenticity_0"]["derived_from"] == ["source_image_0"]
        assert artifacts["authenticity_1"]["derived_from"] == ["source_image_1"]
        audit_events = list(database.audit_events.find({"entityId": scan["id"]}))
        assert {event["eventHash"] for event in audit_events if event["eventType"] in {"upload", "ocr_run", "image_authenticity_analysis"}}.issubset(set(package["audit_refs"]))
    finally:
        _cleanup(get_database())


def test_multi_image_scan_preserves_per_panel_tesseract_review_evidence(monkeypatch):
    import io
    from PIL import Image

    def png_bytes(color: str) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (64, 48), color=color).save(buffer, format="PNG")
        return buffer.getvalue()

    class FakeTesseract:
        enabled = True

        def run(self, _image):
            return {
                "success": True,
                "engine": "tesseract",
                "provenance": "secondary_review_only",
                "full_text": "MRP Rs. 999",
                "detection_count": 1,
                "detections": [{
                    "text": "MRP Rs. 999", "confidence": 0.99, "bbox": [1, 1, 60, 15],
                    "polygon": [[1, 1], [60, 1], [60, 15], [1, 15]], "source": "tesseract", "provenance": ["tesseract"],
                }],
            }

    def paddle_run(image_path: str, yolo_service=None, preprocess_options=None, progress_callback=None) -> OCRResult:
        return _fixed_ocr_run(image_path, yolo_service, preprocess_options)

    monkeypatch.setattr(ocr_module._service, "run", paddle_run)
    monkeypatch.setattr(ocr_module, "TESSERACT_ENABLED", True)
    monkeypatch.setattr(ocr_module, "TESSERACT_MODE", "secondary")
    monkeypatch.setattr(ocr_module, "_tesseract_service", FakeTesseract())
    connect_to_mongodb()
    try:
        ocr_module._analysis_jobs["TEST-JOB-TESSERACT-MULTI"] = {
            "id": "TEST-JOB-TESSERACT-MULTI", "status": "running", "stage": "INITIALIZING",
        }
        images = [
            {"contents": png_bytes("green"), "filename": "front-panel.png", "content_type": "image/png", "side": "Front", "capture_quality_requested": True},
            {"contents": png_bytes("blue"), "filename": "back-panel.png", "content_type": "image/png", "side": "Back", "capture_quality_requested": True},
        ]
        asyncio.run(ocr_module._run_multi_analysis_job(
            "TEST-JOB-TESSERACT-MULTI", images, "Tesseract Multi Test", "General", "ACME",
            {"_id": FAKE_OFFICIAL["_id"], "role": "official"},
        ))
        scan = get_database().scans.find_one({"userId": FAKE_OFFICIAL["_id"]})
        assert scan is not None
        assert len(scan["secondaryOcr"]) == 2
        assert [panel["side"] for panel in scan["secondaryOcr"]] == ["Front", "Back"]
        assert all(panel["result"]["used_for_compliance"] is False for panel in scan["secondaryOcr"])
        assert [item["side"] for item in scan["captureAssessments"]] == ["Front", "Back"]
        assert all(item["quality"]["source_image_sha256"] == scan["panelHashes"][index]["sha256"] for index, item in enumerate(scan["captureAssessments"]))
        mrp = scan["compliance"]["rule_results"][0]["required_fields"]["maximum_retail_price_mrp"]
        assert mrp["value"]["normalized_value"] == 99

        database = get_database()
        package = database.evidence_packages.find_one({"package_id": scan["evidencePackageId"]})
        assert package is not None
        artifacts = {artifact["artifact_id"]: artifact for artifact in package["artifacts"]}
        assert "secondaryOcr" in artifacts
        assert artifacts["secondaryOcr"]["artifact_type"] == "secondary_ocr_evidence"
        assert "captureAssessments" in artifacts
        assert artifacts["captureAssessments"]["artifact_type"] == "capture_quality_evidence"
        audit = database.audit_events.find_one({"entityId": scan["id"], "eventType": "secondary_ocr_run"})
        assert audit is not None
        assert audit["eventHash"] in package["audit_refs"]
    finally:
        ocr_module._analysis_jobs.pop("TEST-JOB-TESSERACT-MULTI", None)
        _cleanup(get_database())


def test_scan_upload_sha256_changes_for_different_bytes(monkeypatch):
    """Two different uploads must produce two different hashes - proves
    this isn't a constant/placeholder value."""
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                import io
                from PIL import Image
                buf_a, buf_b = io.BytesIO(), io.BytesIO()
                Image.new("RGB", (10, 10), color="red").save(buf_a, format="PNG")
                Image.new("RGB", (10, 10), color="blue").save(buf_b, format="PNG")
                response_a = client.post("/api/scans", files={"file": ("a.png", buf_a.getvalue(), "image/png")}, data={"product_name": "A", "category": "General"})
                response_b = client.post("/api/scans", files={"file": ("b.png", buf_b.getvalue(), "image/png")}, data={"product_name": "B", "category": "General"})
                assert response_a.status_code == 200 and response_b.status_code == 200
                assert response_a.json()["sourceImageSha256"] != response_b.json()["sourceImageSha256"]
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_opt_in_tesseract_result_is_packaged_but_not_used_for_compliance(monkeypatch):
    paddle_lines = ["MRP Rs. 99 (Inclusive of all taxes)", "NET WEIGHT: 250 g"]

    def paddle_ocr_run(image_path: str, yolo_service=None, preprocess_options=None) -> OCRResult:
        detections = [
            Detection(text=line, confidence=0.95, bbox=[0, 0, 100, 20], polygon=[[0, 0], [100, 0], [100, 20], [0, 20]])
            for line in paddle_lines
        ]
        return OCRResult(success=True, image=image_path, full_text="\n".join(paddle_lines), detections=detections, detection_count=len(detections))

    class FakeTesseract:
        enabled = True

        def run(self, _image):
            return {
                "success": True,
                "engine": "tesseract",
                "provenance": "secondary_review_only",
                "full_text": "MRP Rs. 999",
                "detection_count": 1,
                "detections": [{
                    "text": "MRP Rs. 999",
                    "confidence": 0.99,
                    "bbox": [0, 0, 100, 20],
                    "polygon": [[0, 0], [100, 0], [100, 20], [0, 20]],
                    "source": "tesseract",
                    "provenance": ["tesseract"],
                }],
            }

    monkeypatch.setattr(ocr_module._service, "run", paddle_ocr_run)
    monkeypatch.setattr(ocr_module, "TESSERACT_ENABLED", True)
    monkeypatch.setattr(ocr_module, "TESSERACT_MODE", "secondary")
    monkeypatch.setattr(ocr_module, "_tesseract_service", FakeTesseract())
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                image_bytes = REAL_IMAGE_WITH_EXIF.read_bytes()
                response = client.post(
                    "/api/scans",
                    files={"file": ("tesseract-secondary.jpg", image_bytes, "image/jpeg")},
                    data={"product_name": "Secondary OCR Test", "category": "General"},
                )
                assert response.status_code == 200, response.text
                scan = response.json()
                assert scan["secondaryOcr"]["status"] == "secondary_completed"
                assert scan["secondaryOcr"]["used_for_compliance"] is False
                assert scan["secondaryOcr"]["comparison"]["mode"] == "weighted_soft_vote"
                mrp = scan["compliance"]["rule_results"][0]["required_fields"]["maximum_retail_price_mrp"]
                assert mrp["value"]["normalized_value"] == 99

                database = get_database()
                package = database.evidence_packages.find_one({"package_id": scan["evidencePackageId"]})
                assert package is not None
                artifacts = {artifact["artifact_id"]: artifact for artifact in package["artifacts"]}
                assert "secondaryOcr" in artifacts
                assert artifacts["secondaryOcr"]["artifact_type"] == "secondary_ocr_evidence"
                assert len(artifacts["secondaryOcr"]["sha256"]) == 64
                assert package["merkle_root"] == scan["evidenceMerkleRoot"]
                secondary_audit = database.audit_events.find_one({"entityId": scan["id"], "eventType": "secondary_ocr_run"})
                assert secondary_audit is not None
                assert secondary_audit["sourceImageSha256"] == scan["sourceImageSha256"]
                assert secondary_audit["eventHash"] in package["audit_refs"]
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_camera_quality_is_evidence_only_and_survives_scan_packaging(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                image_buffer = BytesIO()
                Image.new("RGB", (10, 10), color="green").save(image_buffer, format="PNG")
                image_bytes = image_buffer.getvalue()
                response = client.post(
                    "/api/scans",
                    files={"file": ("camera-capture.png", image_bytes, "image/png")},
                    data={
                        "product_name": "Camera Quality Test",
                        "category": "General",
                        "image_side": "Front",
                        "capture_quality_requested": "true",
                    },
                )
                assert response.status_code == 200, response.text
                scan = response.json()
                assessment = scan["captureAssessments"][0]
                assert assessment["side"] == "Front"
                assert assessment["quality"]["acceptable"] is False
                assert assessment["quality"]["source_image_sha256"] == hashlib.sha256(image_bytes).hexdigest()
                assert assessment["quality"]["physical_calibration"] == {"status": "unavailable", "review_required": True}

                detections = _fixed_ocr_run("unused").detections
                expected = ocr_module._compliance_engine.evaluate({
                    "success": True,
                    "full_text": _FIXED_TEXT,
                    "detections": [detection.model_dump() for detection in detections],
                }, "e_commerce_product_listing")
                assert scan["compliance"]["overall_status"] == expected.overall_status
                assert scan["score"] == expected.compliance_score

                database = get_database()
                package = database.evidence_packages.find_one({"package_id": scan["evidencePackageId"]})
                assert package is not None
                artifacts = {artifact["artifact_id"]: artifact for artifact in package["artifacts"]}
                assert "captureAssessments" in artifacts
                assert artifacts["captureAssessments"]["artifact_type"] == "capture_quality_evidence"
                capture_audit = database.audit_events.find_one({"entityId": scan["id"], "eventType": "capture_evaluation"})
                assert capture_audit is not None
                assert capture_audit["sourceImageSha256"] == scan["sourceImageSha256"]
                assert capture_audit["eventHash"] in package["audit_refs"]
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_tesseract_fallback_runs_only_when_paddle_has_no_detections(monkeypatch):
    image_buffer = BytesIO()
    Image.new("RGB", (10, 10), color="white").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    calls = []

    class FakeTesseract:
        enabled = True

        def run(self, image):
            calls.append(image.size)
            return {
                "success": True,
                "engine": "tesseract",
                "provenance": "secondary_review_only",
                "full_text": "MRP Rs. 99",
                "detection_count": 1,
                "detections": [{"text": "MRP Rs. 99", "confidence": 0.9, "bbox": [0, 0, 10, 5], "polygon": [[0, 0], [10, 0], [10, 5], [0, 5]]}],
            }

    monkeypatch.setattr(ocr_module, "TESSERACT_ENABLED", True)
    monkeypatch.setattr(ocr_module, "TESSERACT_MODE", "fallback")
    monkeypatch.setattr(ocr_module, "_tesseract_service", FakeTesseract())
    empty_paddle = OCRResult(success=True, image="empty.png", full_text="", detections=[], detection_count=0)
    fallback = ocr_module._secondary_ocr_evidence(image_bytes, empty_paddle)
    assert fallback["status"] == "fallback_review_required"
    assert fallback["requires_manual_review"] is True
    assert fallback["used_for_compliance"] is False
    assert fallback.get("comparison") is None
    assert calls == [(10, 10)]

    calls.clear()
    paddle_with_text = OCRResult(success=True, image="text.png", full_text="MRP Rs. 99", detections=[], detection_count=1)
    assert ocr_module._secondary_ocr_evidence(image_bytes, paddle_with_text) is None
    assert calls == []
