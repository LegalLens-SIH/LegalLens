from __future__ import annotations

import httpx
import pytest

from backend.services.open_food_facts_provider import OpenFoodFactsProvider


def _provider(handler, **overrides):
    return OpenFoodFactsProvider(
        enabled=True,
        base_url="https://world.openfoodfacts.org",
        timeout_seconds=2.0,
        max_retries=0,
        user_agent="LegalLense/TestAgent",
        transport=httpx.MockTransport(handler),
        **overrides,
    )


def test_open_food_facts_provider_returns_verified_identity_from_api_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://world.openfoodfacts.org/api/v3/product/3017624010701")
        assert "fields=" in str(request.url)
        assert request.headers.get("User-Agent", "").startswith("LegalLense/")
        assert request.headers.get("Accept") == "application/json"
        return httpx.Response(200, json={
            "code": "3017624010701",
            "status": 1,
            "product": {
                "product_name": "Organic Oats",
                "brands": "Nature Foods",
                "quantity": "500 g",
            },
        })

    result = _provider(handler).verify_gtin("3017624010701")
    assert result["status"] == "VERIFIED"
    assert result["verified"] is True
    assert result["product_identity"] == {
        "brand": "Nature Foods",
        "description": "Organic Oats",
        "net_content": "500 g",
    }
    assert result["authoritative"] is False and result["used_for_compliance"] is False


def test_open_food_facts_provider_rejects_invalid_gtin_and_not_found():
    invalid = _provider(lambda request: httpx.Response(200, json={"status": 1})).verify_gtin("ABC")
    assert invalid["status"] == "INVALID_GTIN"
    assert invalid["verified"] is False

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": 0, "status_verbose": "product not found"})

    missing = _provider(handler).verify_gtin("3017624010701")
    assert missing["status"] == "NOT_FOUND"
    assert missing["verified"] is False


def test_open_food_facts_provider_handles_timeout_rate_limit_and_server_error():
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    timeout = _provider(timeout_handler).verify_gtin("3017624010701")
    assert timeout["status"] == "TIMEOUT"
    assert timeout["verified"] is False

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"status": 0, "status_verbose": "too many requests"})

    rate = _provider(rate_limited).verify_gtin("3017624010701")
    assert rate["status"] == "RATE_LIMITED"
    assert rate["verified"] is False

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": 0, "status_verbose": "internal error"})

    server = _provider(server_error).verify_gtin("3017624010701")
    assert server["status"] == "UNAVAILABLE"
    assert server["verified"] is False


def test_open_food_facts_provider_reports_malformed_json_and_disabled_state():
    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not-json}", headers={"Content-Type": "application/json"})

    malformed_result = _provider(malformed).verify_gtin("3017624010701")
    assert malformed_result["status"] == "MALFORMED_RESPONSE"
    assert malformed_result["verified"] is False

    provider = OpenFoodFactsProvider(enabled=False, base_url="https://world.openfoodfacts.org")
    disabled = provider.verify_gtin("3017624010701")
    assert disabled["status"] == "DISABLED"
    assert disabled["verified"] is False


def test_open_food_facts_provider_has_authority_safety_and_environment_fields():
    provider = OpenFoodFactsProvider(enabled=True, base_url="https://world.openfoodfacts.org")
    availability = provider.availability()
    assert availability["status"] == "READY"
    assert availability["available"] is True
    assert availability["provider"] == "Open Food Facts"
    result = provider.verify_gtin("3017624010701")
    assert result["authoritative"] is False
    assert result["used_for_compliance"] is False
    assert result["source"] == "Open Food Facts"
