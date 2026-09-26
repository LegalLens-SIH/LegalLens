"""
Integration tests for Phase 2A e-commerce listing verification
(backend/api/listing.py), through the real HTTP path. Mirrors
test_ocr_authenticity_integration.py's conventions (TestClient, real
MongoDB, dependency_overrides, PaddleOCRService.run monkeypatched).

`listing_module._fetcher.fetch` is monkeypatched for the happy-path/
comparison tests (mirroring GeminiService._call being the one isolated
network seam in test_gemini_service.py) - no real network call. The SSRF
end-to-end test deliberately does NOT monkeypatch it, proving the real
url_safety.py validation fires at the real API boundary, not just in unit
tests.
"""

from __future__ import annotations

import hashlib

from bson import ObjectId
from fastapi.testclient import TestClient
from gridfs import GridFS

from backend.api import listing as listing_module
from backend.api import ocr as ocr_module
from backend.api.auth import current_user, official_user
from backend.api.test_manufacturer import FAKE_MANUFACTURER
from backend.database import get_database
from backend.main import app
from backend.ocr.schemas import Detection, OCRResult
from backend.services.listing_retrieval import RetrievalOutcome

FAKE_OFFICIAL = {
    "_id": ObjectId(),
    "role": "official",
    "fullName": "Listing Verification Test Official",
    "email": "test-official-listing@example.com",
}

OTHER_OFFICIAL = {
    "_id": ObjectId(),
    "role": "official",
    "fullName": "Other Official",
    "email": "test-official-listing-other@example.com",
}

_FIXED_TEXT = "MRP Rs. 99 (Inclusive of all taxes)\nNET WEIGHT: 250 g\n"


def _fixed_ocr_run(image_path: str, yolo_service=None, preprocess_options=None) -> OCRResult:
    lines = [line for line in _FIXED_TEXT.splitlines() if line.strip()]
    detections = [Detection(text=line, confidence=0.95, bbox=[0, 0, 10, 10], polygon=[[0, 0], [10, 0], [10, 10], [0, 10]]) for line in lines]
    return OCRResult(success=True, image=image_path, full_text=_FIXED_TEXT, detections=detections, detection_count=len(detections))


def _cleanup(db, official_id) -> None:
    scans = list(db.scans.find({"userId": official_id}, {"sourceImageId": 1, "id": 1}))
    fs = GridFS(db, collection="scan_images")
    for scan in scans:
        image_id = scan.get("sourceImageId")
        if image_id:
            try:
                fs.delete(ObjectId(image_id))
            except Exception:
                pass
    db.scans.delete_many({"userId": official_id})
    db.evidence_packages.delete_many({"evidence_id": {"$in": [f"{scan['id']}:listing-verification" for scan in scans]}})
    db.history.delete_many({"userId": official_id})


def _tiny_png_bytes() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="green").save(buf, format="PNG")
    return buf.getvalue()


def _create_scan(client: TestClient) -> dict:
    response = client.post(
        "/api/scans",
        files={"file": ("a.png", _tiny_png_bytes(), "image/png")},
        data={"product_name": "Listing Test Product", "category": "General"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_successful_verification_persists_comparisons_and_never_touches_compliance(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                scan = _create_scan(client)
                original_compliance = scan["compliance"]

                def fake_fetch(url: str) -> RetrievalOutcome:
                    html = (
                        "<html><head><title>t</title>"
                        '<script type="application/ld+json">'
                        '{"@type": "Product", "name": "Test Product", '
                        '"additionalProperty": [{"name": "Net Quantity", "value": "500 g"}, '
                        '{"name": "MRP", "value": "99"}]}'
                        "</script></head><body>x</body></html>"
                    )
                    return RetrievalOutcome(
                        status="SUCCESS", requested_url=url, final_url=url, html=html,
                        content_type="text/html", http_status=200,
                        body_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
                    )

                monkeypatch.setattr(listing_module._fetcher, "fetch", fake_fetch)

                response = client.post(f"/api/scans/{scan['id']}/listing-verification", json={"url": "https://shop.example/p/1"})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["listing"]["retrieval_status"] == "SUCCESS"
                assert result["listing"]["mrp"] == "99"
                mrp_comparison = next(c for c in result["comparisons"] if c["field"] == "maximum_retail_price_mrp")
                assert mrp_comparison["outcome"] == "MATCH"
                assert mrp_comparison["is_legal_violation"] is False
                quantity_comparison = next(c for c in result["comparisons"] if c["field"] == "net_quantity")
                assert quantity_comparison["outcome"] == "MISMATCH"  # physical 250g vs listing 500g
                assert "disclaimer" in result and "NOT" in result["disclaimer"]
                assert result["source_page_sha256"] == hashlib.sha256(
                    (
                        "<html><head><title>t</title>"
                        '<script type="application/ld+json">'
                        '{"@type": "Product", "name": "Test Product", '
                        '"additionalProperty": [{"name": "Net Quantity", "value": "500 g"}, '
                        '{"name": "MRP", "value": "99"}]}'
                        "</script></head><body>x</body></html>"
                    ).encode("utf-8")
                ).hexdigest()
                assert result["evidence_package_id"]
                assert len(result["evidence_merkle_root"]) == 64
                database = get_database()
                package = database.evidence_packages.find_one({"package_id": result["evidence_package_id"]})
                assert package is not None
                package_artifacts = {artifact["artifact_id"]: artifact for artifact in package["artifacts"]}
                assert package_artifacts["source_page_digest"]["artifact_type"] == "external_page_sha256"
                verification_audit = database.audit_events.find_one({"entityId": scan["id"], "eventType": "listing_verification"})
                assert verification_audit is not None
                assert verification_audit["eventHash"] in package["audit_refs"]
                assert result["evidence_merkle_root"] == package["merkle_root"]

                # Re-fetch the scan and prove compliance is byte-for-byte unchanged.
                get_response = client.get(f"/api/scans/{scan['id']}")
                assert get_response.status_code == 200
                assert get_response.json()["compliance"] == original_compliance
                assert get_response.json()["listingVerification"]["listing"]["mrp"] == "99"
            finally:
                _cleanup(get_database(), FAKE_OFFICIAL["_id"])
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_retrieval_failure_returns_safe_result_never_a_compliance_failure(monkeypatch):
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                scan = _create_scan(client)

                def failing_fetch(url: str) -> RetrievalOutcome:
                    return RetrievalOutcome(status="HTTP_ERROR", requested_url=url, http_status=404, error_message="HTTP 404")

                monkeypatch.setattr(listing_module._fetcher, "fetch", failing_fetch)

                response = client.post(f"/api/scans/{scan['id']}/listing-verification", json={"url": "https://shop.example/missing"})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["listing"]["retrieval_status"] == "HTTP_ERROR"
                assert result["comparisons"] == []
                assert result["discrepancy_count"] == 0

                get_response = client.get(f"/api/scans/{scan['id']}")
                assert get_response.json()["compliance"]["overall_status"] == scan["compliance"]["overall_status"]
            finally:
                _cleanup(get_database(), FAKE_OFFICIAL["_id"])
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_ssrf_blocked_end_to_end_with_no_mocking(monkeypatch):
    """No fetch monkeypatch here - proves the real url_safety.py validation
    fires at the real API boundary and the endpoint never attempts an
    actual connection to a private address."""
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                scan = _create_scan(client)
                response = client.post(f"/api/scans/{scan['id']}/listing-verification", json={"url": "http://127.0.0.1:8000/admin"})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["listing"]["retrieval_status"] == "SSRF_BLOCKED"
                assert result["comparisons"] == []
            finally:
                _cleanup(get_database(), FAKE_OFFICIAL["_id"])
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_unsupported_scheme_rejected_end_to_end():
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                scan = _create_scan(client)
                response = client.post(f"/api/scans/{scan['id']}/listing-verification", json={"url": "javascript:alert(1)"})
                assert response.status_code == 200, response.text
                assert response.json()["listing"]["retrieval_status"] == "UNSUPPORTED_SCHEME"
            finally:
                _cleanup(get_database(), FAKE_OFFICIAL["_id"])
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_verification_for_unowned_scan_returns_404(monkeypatch):
    """One official cannot run listing verification against another
    official's scan - ownership is enforced, not just visibility."""
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                scan = _create_scan(client)
            finally:
                pass
            app.dependency_overrides[official_user] = lambda: OTHER_OFFICIAL
            try:
                response = client.post(f"/api/scans/{scan['id']}/listing-verification", json={"url": "https://shop.example/p/1"})
                assert response.status_code == 404
                get_response = client.get(f"/api/scans/{scan['id']}/listing-verification")
                assert get_response.status_code == 404
            finally:
                _cleanup(get_database(), FAKE_OFFICIAL["_id"])
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_missing_scan_id_returns_404():
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            response = client.post("/api/scans/SCN-DOES-NOT-EXIST/listing-verification", json={"url": "https://shop.example/p/1"})
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_manufacturer_forbidden_from_listing_verification(monkeypatch):
    """Backend-enforced authorization, not merely a hidden UI button -
    overrides `current_user` (not `official_user`) so the real
    require_role() check actually runs, per test_official.py's established
    convention for this exact kind of cross-role test."""
    monkeypatch.setattr(ocr_module._service, "run", _fixed_ocr_run)
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            try:
                scan = _create_scan(client)
                app.dependency_overrides.pop(official_user, None)
                app.dependency_overrides[current_user] = lambda: FAKE_MANUFACTURER
                response = client.post(f"/api/scans/{scan['id']}/listing-verification", json={"url": "https://shop.example/p/1"})
                assert response.status_code == 403
            finally:
                app.dependency_overrides.pop(current_user, None)
                app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
                _cleanup(get_database(), FAKE_OFFICIAL["_id"])
    finally:
        app.dependency_overrides.pop(official_user, None)


def test_url_too_long_is_rejected_by_request_validation():
    app.dependency_overrides[official_user] = lambda: FAKE_OFFICIAL
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/scans/SCN-WHATEVER/listing-verification",
                json={"url": "https://shop.example/" + "a" * 3000},
            )
            assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(official_user, None)
