from __future__ import annotations

from datetime import datetime, timezone

import httpx

from backend.main import app
from backend.services.adversarial_validation import assert_authorized, detect_evidence_tampering, ensure_replay_safe, validate_ocr_quality, validate_url_safety_or_raise
from backend.services.bsa_certificate import generate_bsa_certificate
from backend.services.em_maap_client import EMMAAPClient
from backend.services.evidence_package import EvidenceArtifact, EvidencePackage, build_evidence_package
from backend.services.model_validation_report import ModelValidationReport
from backend.services.ruleset_validation_report import RulesetValidationReport
from backend.services.signoff import SignoffError, validate_signoff
from backend.services.timestamp_authority import RFC3161TimestampAuthority, TimestampConfigError


def test_evidence_package_tamper_detection_round_trip():
    package = build_evidence_package(
        evidence_id="case-1",
        raw_artifacts={
            "ocr": {"artifact_type": "ocr_output", "value": {"document_id": "case-1", "detections": [{"text": "hello"}]}, "source": "ocr"},
            "compliance": {"artifact_type": "compliance_findings", "value": {"overall_status": "COMPLIANT"}, "source": "compliance"},
        },
        ruleset_digest="rules-123",
        model_digests={"ocr-model": "model-abc"},
        audit_refs=["audit-1"],
    )
    artifact_map = {a.artifact_id: {"sha256": a.sha256} for a in package.artifacts}
    assert package.verify(artifact_map)["valid"] is True
    artifact_map["ocr"]["sha256"] = "00" * 32
    verification = package.verify(artifact_map)
    assert verification["valid"] is False
    assert "ocr" in verification["changed_artifacts"]


def test_evidence_package_detects_artifact_descriptor_tampering():
    package = build_evidence_package(
        evidence_id="source-image-case",
        raw_artifacts={
            "source_image": {
                "artifact_type": "source_image",
                "value": b"original image bytes",
                "source": "original_upload",
                "metadata": {"file_name": "label.png", "mime_type": "image/png"},
            },
        },
    )
    stored = package.to_dict()
    stored["artifacts"][0]["metadata"]["file_name"] = "different-label.png"

    tampered = EvidencePackage.model_validate(stored)

    assert tampered.verify()["valid"] is False


def test_evidence_package_detects_package_id_tampering():
    package = build_evidence_package(
        evidence_id="package-id-case",
        raw_artifacts={"ocr": {"artifact_type": "ocr_output", "value": {"text": "label"}}},
    )
    tampered = package.model_copy(update={"package_id": "evidence-tampered"})

    assert tampered.verify()["valid"] is False


def test_legacy_evidence_package_is_not_claimed_as_manifest_verified():
    package = build_evidence_package(
        evidence_id="legacy-package-case",
        raw_artifacts={"ocr": {"artifact_type": "ocr_output", "value": {"text": "label"}}},
    )
    legacy = package.model_copy(update={"package_version": "1.0.0"})

    verification = legacy.verify()

    assert verification["valid"] is False
    assert verification["manifest_bound"] is False
    assert verification["reason"] == "legacy_or_unsupported_package_manifest"


def test_tsa_never_trusts_injected_success_or_accepts_invalid_digest():
    def provider(digest: str, nonce: str | None = None):
        return {
            "status": "TIMESTAMPED", "verified": True, "timestamped": True,
            "message_imprint_sha256": digest, "nonce": nonce,
            "token_der_base64": "ZmFrZQ==", "request_der_base64": "ZmFrZQ==", "token_sha256": "0" * 64,
        }

    tsa = RFC3161TimestampAuthority(provider=provider)
    import pytest
    with pytest.raises(TimestampConfigError):
        tsa.request_timestamp("abc123", nonce="n-1")
    result = tsa.request_timestamp("a" * 64, nonce="n-1")
    assert result["timestamped"] is False
    assert result["status"] == "INVALID_TOKEN"

    failed = RFC3161TimestampAuthority(provider=lambda digest, nonce=None: {"status": "failed", "token": None})
    req = failed.request_timestamp("b" * 64)
    assert req["timestamped"] is False


def test_tsa_without_provider_configuration_reports_unavailable_without_timestamping():
    tsa = RFC3161TimestampAuthority(config={"endpoint": "", "openssl_path": None, "cert_path": ""})
    assert tsa.availability()["status"] == "TSA_UNAVAILABLE"
    result = tsa.request_timestamp("a" * 64)
    assert result["status"] == "TSA_UNAVAILABLE"
    assert result["timestamped"] is False


def test_bsa_certificate_generation_and_hashing():
    package = build_evidence_package(
        evidence_id="case-2",
        raw_artifacts={
            "artifact_a": {"artifact_type": "ocr_output", "value": {"text": "A"}, "source": "ocr"},
            "artifact_b": {"artifact_type": "compliance_findings", "value": {"overall_status": "REVIEW_REQUIRED"}, "source": "compliance"},
        },
        ruleset_digest="rules-xyz",
        model_digests={"vision": "v1"},
    )
    cert = generate_bsa_certificate(package, case_id="case-2", reviewer="Officer Test", signatory="Inspector")
    assert cert["pdf_sha256"]
    assert cert["pdf_bytes"].startswith(b"%PDF")
    assert cert["merkle_root"] == package.merkle_root


def test_em_maap_provider_contract_never_treats_unverified_response_as_success():
    submitted = []

    class MockProvider:
        def availability(self):
            return {"status": "SUCCESS", "configured": True, "available": True}

        def submit(self, payload, *, idempotency_key):
            submitted.append((payload, idempotency_key))
            return {"status": "SUCCESS", "transmitted": True, "acknowledged": False, "response": {"status": "accepted"}}

    client = EMMAAPClient(provider=MockProvider())
    result = client.submit(
        {"evidence_package_id": "pkg-1", "merkle_root": "a" * 64, "artifact_hashes": {"ocr": "b" * 64}},
        idempotency_key="key-1",
    )

    assert result["status"] == "PROVIDER_ERROR"
    assert result["acknowledged"] is False
    assert len(submitted) == 1
    assert submitted[0][1] == "key-1"


def test_em_maap_reports_unavailable_validation_auth_provider_and_bad_schema_states(monkeypatch):
    undocumented = EMMAAPClient(endpoint="https://example.test/submit", api_key="secret")
    assert undocumented.availability()["status"] == "API_NOT_DOCUMENTED"
    assert undocumented.submit({"evidence_package_id": "pkg"})["status"] == "API_NOT_DOCUMENTED"

    class NotConfiguredProvider:
        def availability(self):
            return {"status": "NOT_CONFIGURED", "configured": False, "available": False}

        def submit(self, _payload, *, idempotency_key):
            raise AssertionError("unavailable provider must not be called")

    client = EMMAAPClient(provider=NotConfiguredProvider())
    assert client.availability()["status"] == "NOT_CONFIGURED"
    assert client.submit({"evidence_package_id": "pkg"})["status"] == "NOT_CONFIGURED"
    assert EMMAAPClient().submit({})["status"] == "VALIDATION_FAILURE"


def test_em_maap_simulator_is_explicit_and_never_acknowledges():
    client = EMMAAPClient(simulator_enabled=True)

    assert client.availability()["status"] == "SIMULATED"
    result = client.submit({"evidence_package_id": "pkg-sim", "merkle_root": "a" * 64})

    assert result["status"] == "SIMULATED"
    assert result["simulated"] is True
    assert result["transmitted"] is False
    assert result["acknowledged"] is False
    assert "SIMULATED" in result["message"]


def test_em_maap_oversized_payload_returns_validation_state_without_transport():
    client = EMMAAPClient()

    result = client.submit({
        "evidence_package_id": "pkg-oversized",
        "artifact_hashes": {f"artifact-{index}-{'x' * 100}": "a" * 64 for index in range(10000)},
    })

    assert result["status"] == "VALIDATION_FAILURE"
    assert result["transmitted"] is False
    assert result["acknowledged"] is False
    assert result["message"] == "e-Maap request exceeds the 1 MiB limit."


def test_security_and_adversarial_checks():
    try:
        validate_url_safety_or_raise("http://localhost:8000")
        raise AssertionError("localhost URL should be rejected")
    except Exception:
        pass
    assert validate_ocr_quality(0.91)["safe"] is True
    assert validate_ocr_quality(0.49)["safe"] is False
    assert ensure_replay_safe("req-1", [{"record_key": "req-1"}])["duplicate"] is True
    try:
        assert_authorized("manufacturer", required_roles={"official"})
        raise AssertionError("Role check should fail")
    except PermissionError:
        pass
    manifest = [{"artifact_id": "ocr", "sha256": "aaa"}]
    payload = {"ocr": {"sha256": "bbb"}}
    assert detect_evidence_tampering({"merkle_root": "root"}, manifest, artifact_lookup=payload)["valid"] is False


def test_model_and_ruleset_reports_are_explicit_when_data_missing():
    model = ModelValidationReport.from_available()
    assert model.status == "not_evaluated"
    assert "No verified benchmark dataset" in model.note

    rules = RulesetValidationReport.from_ruleset(ruleset_identifier="test-rules", ruleset_version="1.0.0")
    assert rules.status == "requires_verified_legal_sources"
    assert "verified legal source" in rules.note.lower()


def test_signoff_rejects_tampered_merkle_root():
    evidence = {"merkle_root": "root-a"}
    try:
        validate_signoff(
            reviewer_identity={"_id": "user-1", "role": "official", "email": "o@example.com"},
            evidence_package_id="pkg-1",
            evidence_merkle_root="root-b",
            ruleset_digest="r-1",
            model_digests={"ocr": "m-1"},
            timestamp_reference="ts-1",
            certificate_reference="cert-1",
            review_decision="APPROVED",
            stored_evidence=evidence,
        )
        raise AssertionError("Tampered root should fail")
    except SignoffError:
        pass


def test_api_rejects_unstored_evidence_transmission_without_contacting_provider(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.api.auth import current_user
    app.dependency_overrides[current_user] = lambda: {"_id": "user-42", "role": "official", "email": "officer@example.com", "fullName": "Officer"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/em-maap/transmit",
                json={"evidence_id": "e-42", "merkle_root": "mr-42", "artifact_hashes": {"ocr": "hash-42"}, "payload": {"status": "PENDING"}},
            )
            assert response.status_code == 400, response.text
    finally:
        app.dependency_overrides.pop(current_user, None)
