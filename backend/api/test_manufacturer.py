"""Focused tests for the manufacturer upload -> analysis -> result flow.

Exercises POST /api/manufacturer/self-check and GET /api/manufacturer/
revisions(/{id}/image) end-to-end through the real FastAPI app, the real
ComplianceEngine (backend/services/compliance_engine.py - the SAME engine
the official /api/scans flow uses), and a real MongoDB (+ GridFS)
connection, mirroring exactly what the browser sends (frontend/assets/
auth-client.js LLManufacturer.selfCheck): a multipart file upload plus
product_id/product_name form fields.

The only thing swapped out is the actual PaddleOCR/YOLO neural-network
inference inside PaddleOCRService.run - already covered by backend/ocr/
test_ocr.py and by live verification done earlier for this feature. The
fake replacement reads the REAL bytes written to the temp file for THIS
request and echoes them back as the extracted text, so these tests prove
the upload -> save -> OCR-invocation -> ComplianceEngine -> result data flow
actually carries each request's own image through end to end, and that the
manufacturer report now reflects the SAME real, multi-field Legal Metrology
analysis the rest of LegalLense uses - not a 4-field placeholder, and not a
stale/previous result.
"""
from __future__ import annotations

from pathlib import Path

from bson import ObjectId
from fastapi.testclient import TestClient
from gridfs import GridFS

from backend.api import ocr as ocr_module
from backend.api.auth import manufacturer_user
from backend.database import get_database
from backend.main import app
from backend.ocr.schemas import Detection, OCRResult

FAKE_MANUFACTURER = {
    "_id": ObjectId(),
    "role": "manufacturer",
    "fullName": "Test Manufacturer",
    "email": "test-manufacturer-focused@example.com",
}

# A realistic label with 8 of the 8 LMPC-R6-MANDATORY-DECLARATIONS fields
# printed, all but the generic commodity name in a form the real engine's
# extraction recognizes confidently - independently confirmed by running
# ComplianceEngine().evaluate() directly against this text before writing
# these assertions (7 PASS, 1 NEEDS_MANUAL_VERIFICATION, overall
# REVIEW_REQUIRED, score 100 - low-confidence checks are excluded from the
# score rather than counted as failures).
IMAGE_A_BYTES = (
    b"IMAGE-A-MARKER\n"
    b"MANUFACTURED BY ACME FOODS PVT LTD, 12 INDUSTRIAL ROAD, PUNE - 411001\n"
    b"Country of Origin: India\n"
    b"NET WEIGHT: 250 g\n"
    b"MRP Rs. 99 (Inclusive of all taxes)\n"
    b"Unit Sale Price: Rs. 40 per 100g\n"
    b"Mfg Date: 08/2026\n"
    b"Consumer Care: 1800-123-4567\n"
    b"Email: care@acmefoods.example\n"
)
# A label with:
#   - an MRP heading present but no readable amount (P0 fix: this is now
#     NEEDS_MANUAL_VERIFICATION, not a confirmed FAIL - OCR failing to
#     read the numeral is exactly as plausible as the package genuinely
#     lacking one; see compliance_engine.py's _mrp_result comment)
#   - a consumer-care block with a phone number but no email - a
#     genuinely confirmed shortfall (the block WAS located; a required
#     sub-field is confirmably absent from it), independently confirmed to
#     still produce a real PARTIAL/NON_COMPLIANT result even after the MRP
#     fix, so tests can still prove the report surfaces real non-compliant
#     checks, not just passes/manual-review.
IMAGE_B_BYTES = (
    b"IMAGE-B-MARKER\n"
    b"MANUFACTURED BY BETA CORP, 5 BETA LANE, DELHI\n"
    b"Country of Origin: India\n"
    b"NET WEIGHT: 500 g\n"
    b"Maximum Retail Price: ABC\n"
    b"Consumer Care: 1800-999-0000\n"
)


def _echo_ocr_run(image_path: str, yolo_service=None, preprocess_options=None) -> OCRResult:
    """Stand-in for PaddleOCRService.run: echoes back whatever bytes were
    actually written to THIS request's own temp file, instead of a canned
    result - so a test can prove the real uploaded image drove the result,
    not a mock/previous one. Populates one high-confidence Detection per
    text line (mirroring what real PaddleOCR output looks like) since
    ComplianceEngine derives its confidence score from `detections`, not
    from `full_text` alone - an empty detections list would make every
    field NEEDS_MANUAL_VERIFICATION regardless of the text content.

    `preprocess_options` accepted (and ignored, like `yolo_service` already
    was) purely to match backend/api/ocr.py's real run_ocr() call signature
    (Accuracy Fix #2's OCR_PREPROCESSING_ENABLED flag) - this stand-in reads
    the temp file's raw bytes directly rather than decoding an actual image,
    so there is no real image for a preprocessing step to run against here."""
    text = Path(image_path).read_bytes().decode("utf-8", errors="ignore")
    lines = [line for line in text.splitlines() if line.strip()]
    detections = [Detection(text=line, confidence=0.95, bbox=[0, 0, 10, 10], polygon=[[0, 0], [10, 0], [10, 10], [0, 10]]) for line in lines]
    return OCRResult(success=True, image=image_path, full_text=text, detections=detections, detection_count=len(detections))


def _cleanup(db) -> None:
    revisions = list(db.productRevisions.find({"manufacturerId": FAKE_MANUFACTURER["_id"]}, {"sourceImageId": 1}))
    fs = GridFS(db, collection="self_check_images")
    for revision in revisions:
        image_id = revision.get("sourceImageId")
        if image_id:
            try:
                fs.delete(ObjectId(image_id))
            except Exception:
                pass
    db.productRevisions.delete_many({"manufacturerId": FAKE_MANUFACTURER["_id"]})
    db.products.delete_many({"manufacturerId": FAKE_MANUFACTURER["_id"]})
    db.history.delete_many({"manufacturerId": FAKE_MANUFACTURER["_id"]})


def test_self_check_uses_real_compliance_engine_not_four_field_placeholder(monkeypatch):
    """Requirement: the manufacturer report must use the SAME real
    ComplianceEngine result the rest of LegalLense uses (rule_results with
    per-field PASS/FAIL/NEEDS_MANUAL_VERIFICATION status, explanation, and
    confidence), covering every applicable Legal Metrology declaration - not
    the old 4-check (product name / net quantity / MRP / manufacturer)
    placeholder shape ({overallResult, fields, issues, confidence})."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    try:
        with TestClient(app) as client:
            try:
                response = client.post(
                    "/api/manufacturer/self-check",
                    files={"file": ("label.png", IMAGE_A_BYTES, "image/png")},
                    data={"product_id": "", "product_name": "Acme Cookies"},
                )
                assert response.status_code == 200, response.text
                revision = response.json()["revision"]
                report = revision["complianceResult"]

                # Real ComplianceResult shape, not the old placeholder shape.
                assert "rule_results" in report
                assert "overall_status" in report
                assert "compliance_score" in report
                assert "fields" not in report
                assert "overallResult" not in report

                assert report["validation_profile"] == "e_commerce_product_listing"
                assert report["overall_status"] == "REVIEW_REQUIRED"
                assert report["compliance_score"] == 100
                assert revision["complianceScore"] == 100

                required_fields = report["rule_results"][0]["required_fields"]
                # Every applicable Legal Metrology declaration check is
                # present - not just product name / net quantity / MRP /
                # manufacturer.
                assert set(required_fields.keys()) == {
                    "manufacturer_packer_importer_details",
                    "country_of_origin",
                    "common_generic_name_of_commodity",
                    "net_quantity",
                    "month_and_year_of_manufacture_or_packing",
                    "maximum_retail_price_mrp",
                    "unit_sale_price",
                    "consumer_care_details",
                }
                assert required_fields["manufacturer_packer_importer_details"]["status"] == "PASS"
                assert "ACME FOODS" in required_fields["manufacturer_packer_importer_details"]["value"]
                assert required_fields["country_of_origin"]["status"] == "PASS"
                assert required_fields["net_quantity"]["status"] == "PASS"
                assert required_fields["maximum_retail_price_mrp"]["status"] == "PASS"
                # Not blindly marked applicable/passed: a genuinely
                # undetected field stays NEEDS_MANUAL_VERIFICATION, visible
                # in the report - not hidden, not faked as a pass.
                assert required_fields["common_generic_name_of_commodity"]["status"] == "NEEDS_MANUAL_VERIFICATION"

                # Summary counts reflect the actual engine result, never hardcoded.
                summary = report["summary"]
                assert summary["compliant"] == 7
                assert summary["low_confidence"] == 1
                assert summary["non_compliant"] == 0
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_self_check_surfaces_non_compliant_checks_not_just_passes(monkeypatch):
    """Requirement: the report must show FAILED/non-compliant checks, not
    hide them - and the overall status hierarchy (NON_COMPLIANT >
    REVIEW_REQUIRED > COMPLIANT) must hold. Also exercises the P0 MRP fix:
    IMAGE_B_BYTES' MRP heading has no readable amount, which must now
    register as NEEDS_MANUAL_VERIFICATION (not a confirmed violation),
    while its consumer-care block (phone present, email confirmably
    absent) still produces a genuine confirmed PARTIAL - proving the
    report still surfaces real violations after the fix, just not this
    particular MRP case."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    try:
        with TestClient(app) as client:
            try:
                response = client.post(
                    "/api/manufacturer/self-check",
                    files={"file": ("label-b.png", IMAGE_B_BYTES, "image/png")},
                    data={"product_id": "", "product_name": "Beta Widget"},
                )
                assert response.status_code == 200, response.text
                revision = response.json()["revision"]
                report = revision["complianceResult"]

                assert report["overall_status"] == "NON_COMPLIANT"
                mrp = report["rule_results"][0]["required_fields"]["maximum_retail_price_mrp"]
                assert mrp["status"] == "NEEDS_MANUAL_VERIFICATION"
                assert mrp["explanation"]
                care = report["rule_results"][0]["required_fields"]["consumer_care_details"]
                assert care["status"] == "PARTIAL"
                assert "consumer_care_details" in " ".join(revision["issues"])
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_self_check_result_contains_and_serves_the_exact_uploaded_image(monkeypatch):
    """Requirement: upload image A -> analyze image A -> result contains
    image A and extracted data from image A. Also proves a second upload
    (image B) never reuses or overwrites the first revision's own stored
    image/report - each revision keeps its own real data forever."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    try:
        with TestClient(app) as client:
            try:
                response_a = client.post(
                    "/api/manufacturer/self-check",
                    files={"file": ("label-a.png", IMAGE_A_BYTES, "image/png")},
                    data={"product_id": "", "product_name": "Product A"},
                )
                assert response_a.status_code == 200, response_a.text
                revision_a = response_a.json()["revision"]
                assert "IMAGE-A-MARKER" in revision_a["ocrResult"]["full_text"]
                assert "IMAGE-B-MARKER" not in revision_a["ocrResult"]["full_text"]
                assert revision_a["complianceResult"]["overall_status"] == "REVIEW_REQUIRED"
                assert revision_a.get("sourceImageUrl")

                response_b = client.post(
                    "/api/manufacturer/self-check",
                    files={"file": ("label-b.png", IMAGE_B_BYTES, "image/png")},
                    data={"product_id": "", "product_name": "Product B"},
                )
                assert response_b.status_code == 200, response_b.text
                revision_b = response_b.json()["revision"]
                assert "IMAGE-B-MARKER" in revision_b["ocrResult"]["full_text"]
                assert "IMAGE-A-MARKER" not in revision_b["ocrResult"]["full_text"]
                assert revision_b["complianceResult"]["overall_status"] == "NON_COMPLIANT"
                assert revision_b.get("sourceImageUrl")

                assert revision_a["revisionId"] != revision_b["revisionId"]
                assert revision_a["sourceImageUrl"] != revision_b["sourceImageUrl"]

                # Each revision's own image endpoint returns exactly ITS OWN
                # uploaded bytes - never the other revision's, and never a
                # previous/stale report's image.
                image_a = client.get(revision_a["sourceImageUrl"])
                assert image_a.status_code == 200
                assert image_a.content == IMAGE_A_BYTES

                image_b = client.get(revision_b["sourceImageUrl"])
                assert image_b.status_code == 200
                assert image_b.content == IMAGE_B_BYTES

                # Re-fetching revision A's image again (after B was uploaded)
                # still returns image A untouched - proves the newer upload
                # did not overwrite or alias the older revision's image or report.
                image_a_again = client.get(revision_a["sourceImageUrl"])
                assert image_a_again.content == IMAGE_A_BYTES
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_self_check_image_endpoint_requires_ownership(monkeypatch):
    """A manufacturer must never be able to fetch another manufacturer's
    uploaded image just by knowing/guessing a revision id."""
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/manufacturer/self-check",
                files={"file": ("label-a.png", IMAGE_A_BYTES, "image/png")},
                data={"product_id": "", "product_name": "Product A"},
            )
            revision = response.json()["revision"]

            other_manufacturer = {"_id": ObjectId(), "role": "manufacturer", "fullName": "Other Mfg", "email": "other-mfg-focused@example.com"}
            app.dependency_overrides[manufacturer_user] = lambda: other_manufacturer
            other_response = client.get(revision["sourceImageUrl"])
            assert other_response.status_code == 404

            _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_self_check_rejects_unsupported_file_type(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _echo_ocr_run)
    app.dependency_overrides[manufacturer_user] = lambda: FAKE_MANUFACTURER
    try:
        with TestClient(app) as client:
            try:
                response = client.post(
                    "/api/manufacturer/self-check",
                    files={"file": ("label.pdf", b"%PDF-1.4 fake", "application/pdf")},
                    data={"product_id": "", "product_name": "Acme Cookies"},
                )
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "unsupported_file_type"
            finally:
                _cleanup(get_database())
    finally:
        app.dependency_overrides.pop(manufacturer_user, None)


def test_self_check_requires_manufacturer_authentication():
    app.dependency_overrides.pop(manufacturer_user, None)
    with TestClient(app) as client:
        response = client.post(
            "/api/manufacturer/self-check",
            files={"file": ("label.png", IMAGE_A_BYTES, "image/png")},
        )
    assert response.status_code == 401
