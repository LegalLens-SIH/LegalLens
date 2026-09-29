from __future__ import annotations

from pathlib import Path

from backend.api.evidence import _json_safe_timestamp
from backend.services import timestamp_authority as tsa_module
from backend.services.timestamp_authority import RFC3161TimestampAuthority, timestamp_config


def test_default_tsa_configuration_uses_sectigo_but_is_opt_in(monkeypatch):
    for key in (
        "TSA_ENABLED", "TSA_URL", "TSA_ENDPOINT", "TSA_TRUSTED_CERT_PATH", "TSA_CERT_PATH",
        "TSA_MIN_REQUEST_INTERVAL_SECONDS", "TSA_POLICY_OID",
    ):
        monkeypatch.delenv(key, raising=False)

    config = timestamp_config()

    assert config["endpoint"] == "https://timestamp.sectigo.com"
    assert config["enabled"] is False
    assert config["min_request_interval_seconds"] == 15
    assert config["cert_path"]


def test_tsa_requires_enable_flag_and_never_calls_provider_when_disabled():
    tsa = RFC3161TimestampAuthority(config={
        "endpoint": "https://timestamp.sectigo.com", "enabled": False,
        "openssl_path": "openssl", "cert_path": "trusted.pem",
    })

    status = tsa.availability()
    result = tsa.request_timestamp("a" * 64)

    assert status["status"] == "TSA_UNAVAILABLE"
    assert result["status"] == "TSA_UNAVAILABLE"
    assert result["timestamped"] is False


def test_tsa_availability_requires_openssl_and_a_real_trust_bundle(tmp_path):
    trust_bundle = tmp_path / "trust.pem"
    trust_bundle.write_text("test-only path", encoding="ascii")
    unavailable = RFC3161TimestampAuthority(config={
        "endpoint": "https://timestamp.sectigo.com", "enabled": True,
        "openssl_path": None, "cert_path": str(trust_bundle),
    })
    ready = RFC3161TimestampAuthority(config={
        "endpoint": "https://timestamp.sectigo.com", "enabled": True,
        "openssl_path": "openssl", "cert_path": str(trust_bundle),
    })

    assert unavailable.availability()["status"] == "TSA_UNAVAILABLE"
    assert ready.availability()["status"] == "READY"


def test_tsa_request_slot_enforces_configured_interval(monkeypatch):
    monkeypatch.setattr(tsa_module, "_last_tsa_request_at", None)
    monkeypatch.setattr(tsa_module.time, "monotonic", lambda: 100.0)
    tsa = RFC3161TimestampAuthority(config={"min_request_interval_seconds": 15})

    assert tsa._reserve_request_slot() == (True, 0.0)
    allowed, wait_seconds = tsa._reserve_request_slot()

    assert allowed is False
    assert wait_seconds == 15


def test_timestamp_api_summary_does_not_expose_raw_rfc3161_der():
    response = _json_safe_timestamp({
        "package_id": "pkg-1",
        "timestamp_result": {
            "status": "TIMESTAMPED",
            "timestamped": True,
            "token_der_base64": "private-token-bytes",
            "request_der_base64": "private-request-bytes",
            "token_sha256": "a" * 64,
            "timestamp": "2026-09-28 12:00:00 UTC",
        },
    })

    assert response["timestamp_result"]["status"] == "TIMESTAMPED"
    assert response["timestamp_result"]["token_sha256"] == "a" * 64
    assert "token_der_base64" not in response["timestamp_result"]
    assert "request_der_base64" not in response["timestamp_result"]
