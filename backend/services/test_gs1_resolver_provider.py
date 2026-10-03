from __future__ import annotations

import json
import sys

import httpx
import pytest

from backend.services.gs1_resolver_provider import GS1ResolverCEProvider
from backend.services.gs1_verification import GS1VerificationService


BASE = "http://localhost:8080"
GTIN13 = "4006381333931"
GTIN14 = "04006381333931"
SYNTHETIC_GTIN14 = "10012345678902"


def _provider(handler, **overrides):
    return GS1ResolverCEProvider(
        enabled=True,
        base_url=BASE,
        timeout_seconds=0.5,
        max_retries=1,
        transport=httpx.MockTransport(handler),
        **overrides,
    )


def test_disabled_provider_sends_no_request():
    def handler(_request):
        raise AssertionError("disabled resolver must not make a request")

    provider = GS1ResolverCEProvider(transport=httpx.MockTransport(handler))
    assert provider.availability()["status"] == "DISABLED"
    assert provider.verify_gtin(GTIN13)["status"] == "DISABLED"


def test_from_env_defaults_disabled_and_does_not_query(monkeypatch):
    for key in (
        "GS1_RESOLVER_ENABLED", "GS1_RESOLVER_BASE_URL", "GS1_RESOLVER_TIMEOUT_SECONDS",
        "GS1_RESOLVER_MAX_RETRIES", "GS1_RESOLVER_SYNTHETIC_GTINS",
    ):
        monkeypatch.delenv(key, raising=False)
    provider = GS1ResolverCEProvider.from_env()
    assert provider.availability()["status"] == "DISABLED"
    assert provider.verify_gtin(GTIN13)["status"] == "DISABLED"


def test_resolver_result_is_supporting_only_inside_existing_gs1_service():
    provider = _provider(lambda _request: httpx.Response(
        307, headers={"location": "https://synthetic.invalid/resource/4006381333931"},
    ))
    result = GS1VerificationService(provider).verify({"detections": [{
        "raw_value": GTIN13, "symbology": "EAN_13", "bbox": [1, 2, 3, 4],
    }]}, scan_id="SCN-TEST", image_id="IMG-TEST", report_id="RPT-TEST")
    barcode = result["barcodes"][0]
    assert result["status"] == "LOCAL_VALIDATION_ONLY"
    assert barcode["check_digit_valid"] is True
    assert barcode["external_verification"]["status"] == "RESOLVED"
    assert barcode["external_verification"]["verified"] is False
    assert barcode["external_verification"]["resources"][0]["href"].startswith("https://synthetic.invalid/")
    assert barcode["evidence"]["report_id"] == "RPT-TEST"
    assert result["authoritative"] is False and result["used_for_compliance"] is False


def test_untrusted_configured_url_fails_closed_without_request():
    def handler(_request):
        raise AssertionError("unsafe base URL must not reach transport")

    provider = GS1ResolverCEProvider(
        enabled=True,
        base_url="https://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    assert provider.availability()["status"] == "INVALID_CONFIGURATION"
    assert provider.verify_gtin(GTIN13)["status"] == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("base_url", [
    "http://user:pass@localhost:8080",
    "http://localhost:8080/other-path",
    "https://localhost:8080",
    "http://192.168.1.2:8080",
])
def test_only_loopback_http_origin_is_accepted(base_url):
    provider = GS1ResolverCEProvider(enabled=True, base_url=base_url)
    assert provider.availability()["status"] == "INVALID_CONFIGURATION"


def test_documented_digital_link_redirect_is_local_resolution_not_registry_verification():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://synthetic.invalid/resource/10012345678902"})

    provider = _provider(handler, synthetic_gtins={"10012345678902"})
    result = provider.verify_gtin(SYNTHETIC_GTIN14)
    assert requests[0].method == "GET"
    assert requests[0].url.path == f"/01/{SYNTHETIC_GTIN14}"
    assert requests[0].headers["accept"] == "*/*"
    assert "authorization" not in requests[0].headers
    assert result["status"] == "RESOLVED"
    assert result["verified"] is False
    assert result["provider_kind"] == "digital_link_resolver"
    assert result["source"] == "GS1 Digital Link Resolver CE"
    assert result["environment"] == "development"
    assert result["fixture_status"] == "synthetic"
    assert result["queried_gtin14"] == SYNTHETIC_GTIN14
    assert result["resources"] == [{"href": "https://synthetic.invalid/resource/10012345678902"}]


def test_digital_link_300_linkset_is_normalized_and_non_redirect_resources_are_bounded():
    payload = {"linkset": [
        {"href": "https://synthetic.invalid/a", "linktype": "gs1:pip"},
        {"href": "https://synthetic.invalid/b", "linktype": "gs1:recipeInfo"},
    ]}
    provider = _provider(lambda _request: httpx.Response(300, json=payload))
    result = provider.verify_gtin(GTIN13)
    assert result["status"] == "RESOLVED"
    assert result["verified"] is False
    assert result["resources"] == [
        {"href": "https://synthetic.invalid/a"},
        {"href": "https://synthetic.invalid/b"},
    ]


def test_documented_404_is_not_found():
    provider = _provider(lambda _request: httpx.Response(404))
    result = provider.verify_gtin(GTIN13)
    assert result["status"] == "NOT_FOUND"
    assert result["resources"] == []
    assert result["verified"] is False


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/admin",
    "http://localhost/private",
    "javascript:alert(1)",
])
def test_resolved_resources_reject_unsafe_or_non_http_targets(location):
    provider = _provider(lambda _request: httpx.Response(307, headers={"location": location}))
    result = provider.verify_gtin(GTIN13)
    assert result["status"] == "MALFORMED_RESPONSE"
    assert result["resources"] == []
    assert result["verified"] is False


@pytest.mark.parametrize("payload", [{}, {"linkset": "not-a-list"}, {"linkset": [{"href": "javascript:alert(1)"}]}])
def test_malformed_linkset_is_rejected_without_success(payload):
    calls = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(300, json=payload)

    provider = _provider(handler)
    result = provider.verify_gtin(GTIN13)
    assert result["status"] == "MALFORMED_RESPONSE"
    assert result["verified"] is False
    assert len(calls) == 1


def test_unexpected_http_error_is_explicit():
    provider = _provider(lambda _request: httpx.Response(500))
    assert provider.verify_gtin(GTIN13)["status"] == "UNAVAILABLE"


def test_response_body_limit_is_enforced():
    provider = _provider(lambda _request: httpx.Response(300, content=json.dumps({"linkset": []}) + " " * 140000))
    result = provider.verify_gtin(GTIN13)
    assert result["status"] == "RESPONSE_TOO_LARGE"


def test_transient_connection_failure_uses_bounded_retry_and_malformed_does_not():
    calls = []

    def transient(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(307, headers={"location": "https://synthetic.invalid/a"})

    provider = _provider(transient)
    result = GS1VerificationService(provider).verify({"detections": [{"raw_value": GTIN13, "symbology": "EAN_13"}]})
    assert result["barcodes"][0]["external_verification"]["status"] == "RESOLVED"
    assert len(calls) == 2

    malformed_calls = []

    def malformed(_request):
        malformed_calls.append(1)
        return httpx.Response(300, json={"linkset": "invalid"})

    result = GS1VerificationService(_provider(malformed)).verify({"detections": [{"raw_value": GTIN13, "symbology": "EAN_13"}]})
    assert result["barcodes"][0]["external_verification"]["status"] == "MALFORMED_RESPONSE"
    assert len(malformed_calls) == 1


def test_synthetic_fixture_metadata_has_no_registry_claim():
    provider = _provider(lambda _request: httpx.Response(404), synthetic_gtins={"10012345678902"})
    result = provider.verify_gtin("10012345678902")
    assert result["fixture_status"] == "synthetic"
    assert result["source"] == "GS1 Digital Link Resolver CE"
    assert result["verified"] is False


def test_fixture_seed_script_defaults_to_dry_run(monkeypatch, capsys):
    from scripts import seed_gs1_resolver_ce_fixture

    monkeypatch.setattr(sys, "argv", ["seed_gs1_resolver_ce_fixture"])
    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry run sent HTTP")))
    assert seed_gs1_resolver_ce_fixture.main() == 0
    output = capsys.readouterr().out
    assert "status: synthetic" in output
    assert "Dry run only" in output
