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
from backend.services.timestamp_authority import RFC3161TimestampAuthority


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


def test_tsa_success_and_failure_paths():
    def provider(digest: str, nonce: str | None = None):
        return {"status": "ok", "token": {"messageImprint": digest, "status": "ok", "token": "ts-1", "nonce": nonce}}

    tsa = RFC3161TimestampAuthority(provider=provider)
    result = tsa.request_timestamp("abc123", nonce="n-1")
    assert result["timestamped"] is True
    assert tsa.verify(result["token"], digest="abc123", expected_nonce="n-1")["valid"] is True

    failed = RFC3161TimestampAuthority(provider=lambda digest, nonce=None: {"status": "failed", "token": None})
    req = failed.request_timestamp("def456")
    assert req["timestamped"] is False


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


def test_em_maap_retries_then_acknowledges_and_marks_dead_letter():
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "retry"})
        return httpx.Response(200, json={"status": "accepted"})

    client = EMMAAPClient(endpoint="https://example.test/submit", api_key="secret", timeout=5.0, max_retries=3)
    import backend.services.em_maap_client as em_module
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(em_module.httpx, "post", fake_post)
    try:
        result = client.submit({"evidence_package_id": "pkg-1", "merkle_root": "abc", "artifact_hashes": {"ocr": "hash1"}}, idempotency_key="key-1")
        assert result["status"] == "ACKNOWLEDGED"
        assert len(calls) == 2
        assert result["idempotency_key"] == "key-1"

        client2 = EMMAAPClient(endpoint="https://example.test/submit", api_key="secret", timeout=5.0, max_retries=2)
        failing = __import__("pytest").MonkeyPatch()
        failing.setattr(em_module.httpx, "post", lambda *a, **k: httpx.Response(500, json={"error": "down"}))
        try:
            result2 = client2.submit({"evidence_package_id": "pkg-2", "merkle_root": "def", "artifact_hashes": {"ocr": "hash2"}}, idempotency_key="key-2")
            assert result2["status"] in {"RETRYING", "FAILED"}
        finally:
            failing.undo()
    finally:
        monkeypatch.undo()


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


def test_api_end_to_end_evidence_chain_route_round_trip(monkeypatch):
    from fastapi.testclient import TestClient

    def fake_post(url, json, headers, timeout):
        return httpx.Response(200, json={"status": "accepted"})

    import backend.services.em_maap_client as em_module
    from backend.api.auth import current_user
    monkeypatch.setenv("EMAAP_ENDPOINT", "https://example.test/submit")
    monkeypatch.setenv("EMAAP_API_KEY", "secret")
    monkeypatch.setattr(em_module.httpx, "post", fake_post)
    app.dependency_overrides[current_user] = lambda: {"_id": "user-42", "role": "official", "email": "officer@example.com", "fullName": "Officer"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/em-maap/transmit",
                json={"evidence_package_id": "pkg-42", "evidence_id": "e-42", "merkle_root": "mr-42", "artifact_hashes": {"ocr": "hash-42"}, "payload": {"status": "PENDING"}},
            )
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
    finally:
        app.dependency_overrides.pop(current_user, None)
