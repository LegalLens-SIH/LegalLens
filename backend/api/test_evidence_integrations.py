from __future__ import annotations

from bson import ObjectId
from fastapi.testclient import TestClient

from backend.api import em_maap as em_maap_module
from backend.api import evidence as evidence_module
from backend.api.auth import current_user, official_user
from backend.database import get_database
from backend.main import app
from backend.services.evidence_package import EvidencePackage, build_evidence_package


OFFICIAL = {"_id": ObjectId(), "role": "official", "email": "evidence-integration@example.com"}


def _store_owned_package(db) -> EvidencePackage:
    scan_id = f"SCN-EVIDENCE-{ObjectId()}"
    package = build_evidence_package(
        evidence_id=scan_id,
        raw_artifacts={
            "ocr": {"artifact_type": "ocr_output", "value": {"text": "MRP Rs. 99"}, "source": "ocr"},
            "compliance": {"artifact_type": "compliance_findings", "value": {"status": "REVIEW_REQUIRED"}, "source": "compliance"},
        },
        audit_refs=[],
    )
    db.evidence_packages.insert_one(package.to_dict())
    db.scans.insert_one({"id": scan_id, "userId": OFFICIAL["_id"], "evidencePackageId": package.package_id})
    return package


def _cleanup(db, package: EvidencePackage) -> None:
    db.scans.delete_many({"id": package.evidence_id, "userId": OFFICIAL["_id"]})
    db.evidence_packages.delete_many({"package_id": package.package_id})
    db.timestamp_authority_records.delete_many({"package_id": package.package_id})
    db.em_maap_transmissions.delete_many({"evidence_package_id": package.package_id})


def test_package_timestamp_records_safe_unavailable_state_and_audit(monkeypatch):
    class UnavailableTSA:
        def request_timestamp(self, digest):
            return {"status": "not_configured", "timestamped": False, "digest": digest}

    monkeypatch.setattr(evidence_module, "RFC3161TimestampAuthority", UnavailableTSA)
    app.dependency_overrides[official_user] = lambda: OFFICIAL
    with TestClient(app) as client:
        database = get_database()
        package = _store_owned_package(database)
        try:
            response = client.post(f"/api/evidence-packages/{package.package_id}/timestamp")
            assert response.status_code == 200, response.text
            payload = response.json()["timestamp"]
            assert payload["merkle_root"] == package.merkle_root
            assert payload["timestamp_result"]["status"] == "not_configured"
            assert payload["timestamp_result"]["timestamped"] is False
            event = database.audit_events.find_one({"eventHash": payload["audit_event_hash"]})
            assert event is not None
            assert event["details"]["digest"] == package.merkle_root
        finally:
            _cleanup(database, package)
            app.dependency_overrides.pop(official_user, None)


def test_package_em_aap_transmission_derives_hashes_from_owned_package(monkeypatch):
    submitted = []

    class FakeClient:
        def submit(self, payload, *, idempotency_key):
            submitted.append((payload, idempotency_key))
            return {"status": "ACKNOWLEDGED", "attempt": 1, **payload}

    monkeypatch.setattr(em_maap_module.EMMAAPClient, "from_env", classmethod(lambda cls: FakeClient()))
    app.dependency_overrides[current_user] = lambda: OFFICIAL
    with TestClient(app) as client:
        database = get_database()
        package = _store_owned_package(database)
        try:
            response = client.post(f"/api/em-maap/transmit-package/{package.package_id}")
            assert response.status_code == 200, response.text
            sent, idempotency_key = submitted[0]
            expected_hashes = {artifact.artifact_id: artifact.sha256 for artifact in package.artifacts}
            assert sent["evidence_package_id"] == package.package_id
            assert sent["merkle_root"] == package.merkle_root
            assert sent["artifact_hashes"] == expected_hashes
            assert idempotency_key == f"{package.package_id}:{OFFICIAL['_id']}"
            assert response.json()["transmission"]["status"] == "ACKNOWLEDGED"
        finally:
            _cleanup(database, package)
            app.dependency_overrides.pop(current_user, None)