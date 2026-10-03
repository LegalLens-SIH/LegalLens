"""Public product-data lookup adapter for Open Food Facts.

This provider is intentionally supporting-only evidence. It never determines
compliance status and must not be treated as an authoritative legal registry.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.services.gs1_verification import normalize_gtin

PROVIDER_NAME = "Open Food Facts"
DEFAULT_BASE_URL = "https://world.openfoodfacts.org"
DEFAULT_USER_AGENT = "LegalLense/1.0 (+https://legal-lense.example)"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            cleaned = _clean_text(item)
            if cleaned:
                return cleaned
        return None
    if isinstance(value, (str, int, float)):
        cleaned = str(value).strip()
        return cleaned or None
    return None


class OpenFoodFactsProvider:
    """Public Open Food Facts lookup for product metadata by GTIN.

    The response is intentionally limited to supporting product identity fields
    and always carries an explicit non-authoritative status.
    """

    provider_name = PROVIDER_NAME
    provider_kind = "product_data_provider"

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 10.0,
        max_retries: int = 1,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
    ):
        self.enabled = enabled
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self._transport = transport
        self._configuration_error = None
        if not 0.25 <= self.timeout_seconds <= 30:
            self._configuration_error = "Open Food Facts timeout must be between 0.25 and 30 seconds"
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 3:
            self._configuration_error = "Open Food Facts max_retries must be between 0 and 3"

    @classmethod
    def from_env(cls) -> "OpenFoodFactsProvider":
        timeout_value = os.getenv("OPEN_FOOD_FACTS_TIMEOUT_SECONDS", os.getenv("OPENFOODFACTS_TIMEOUT_SECONDS", "10"))
        retries_value = os.getenv("OPEN_FOOD_FACTS_MAX_RETRIES", os.getenv("OPENFOODFACTS_MAX_RETRIES", "1"))
        try:
            timeout = float(timeout_value)
        except ValueError:
            timeout = 10.0
        try:
            max_retries = int(retries_value)
        except ValueError:
            max_retries = 1
        return cls(
            enabled=_env_bool("OPEN_FOOD_FACTS_ENABLED", _env_bool("OPENFOODFACTS_ENABLED", False)),
            base_url=os.getenv("OPEN_FOOD_FACTS_BASE_URL", os.getenv("OPENFOODFACTS_BASE_URL", DEFAULT_BASE_URL)),
            timeout_seconds=timeout,
            max_retries=max_retries,
            user_agent=os.getenv("OPEN_FOOD_FACTS_USER_AGENT", os.getenv("OPENFOODFACTS_USER_AGENT", DEFAULT_USER_AGENT)),
        )

    def availability(self) -> dict[str, Any]:
        if self._configuration_error:
            return {
                "status": "INVALID_CONFIGURATION",
                "provider": self.provider_name,
                "enabled": self.enabled,
                "available": False,
                "configured": False,
                "reason": self._configuration_error,
            }
        if not self.enabled:
            return {
                "status": "DISABLED",
                "provider": self.provider_name,
                "enabled": False,
                "available": False,
                "configured": True,
                "reason": "OPEN_FOOD_FACTS_ENABLED is false; no request was sent.",
            }
        return {
            "status": "READY",
            "provider": self.provider_name,
            "enabled": True,
            "available": True,
            "configured": True,
            "reason": "Open Food Facts is ready for supporting lookup requests.",
        }

    def _outcome(self, status: str, *, reason: str | None = None, queried_gtin: str | None = None, product_identity: dict[str, str | None] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "provider": self.provider_name,
            "provider_kind": self.provider_kind,
            "source": self.provider_name,
            "environment": "public_api",
            "queried_gtin": queried_gtin,
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "verified": False,
            "authoritative": False,
            "used_for_compliance": False,
            "reason": reason,
        }
        if product_identity is not None:
            payload["product_identity"] = product_identity
        return payload

    @staticmethod
    def _product_identity(payload: dict[str, Any] | None) -> dict[str, str | None] | None:
        if not isinstance(payload, dict):
            return None
        identity = {
            "brand": _clean_text(payload.get("brands") or payload.get("brand_owner")),
            "description": _clean_text(payload.get("product_name") or payload.get("product_name_en") or payload.get("generic_name") or payload.get("product_name_fr")),
            "net_content": _clean_text(payload.get("quantity") or payload.get("serving_size") or payload.get("packaging")),
        }
        if not any(value for value in identity.values() if value):
            return None
        return identity

    def verify_gtin(self, gtin: str) -> dict[str, Any]:
        validation = normalize_gtin(gtin)
        if not validation["valid"]:
            return self._outcome("INVALID_GTIN", reason=validation.get("error") or "Only a valid GTIN can be queried in Open Food Facts.", queried_gtin=gtin)
        if self._configuration_error:
            return self._outcome("INVALID_CONFIGURATION", reason=self._configuration_error, queried_gtin=validation["gtin"])
        if not self.enabled:
            return self._outcome("DISABLED", reason="OPEN_FOOD_FACTS_ENABLED is false; no public product lookup was sent.", queried_gtin=validation["gtin"])

        gtin_code = validation["gtin"]
        base_url = self.base_url.rstrip("/")
        url = f"{base_url}/api/v3/product/{gtin_code}"
        params = {"fields": "product_name,product_name_en,generic_name,product_name_fr,brands,brand_owner,quantity,serving_size,packaging"}
        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(self.timeout_seconds),
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                    transport=self._transport,
                    follow_redirects=True,
                ) as client:
                    response = client.get(url, params=params)
                if response.status_code == 429:
                    return self._outcome("RATE_LIMITED", reason="Open Food Facts rate-limited the request.", queried_gtin=gtin_code)
                if response.status_code == 404:
                    return self._outcome("NOT_FOUND", reason="Open Food Facts has no product record for this GTIN.", queried_gtin=gtin_code)
                if response.status_code >= 500:
                    return self._outcome("UNAVAILABLE", reason=f"Open Food Facts returned HTTP {response.status_code}.", queried_gtin=gtin_code)
                if response.status_code >= 400:
                    return self._outcome("HTTP_ERROR", reason=f"Open Food Facts returned HTTP {response.status_code}.", queried_gtin=gtin_code)
                try:
                    payload = response.json()
                except ValueError:
                    return self._outcome("MALFORMED_RESPONSE", reason="Open Food Facts did not return valid JSON.", queried_gtin=gtin_code)
                if not isinstance(payload, dict):
                    return self._outcome("MALFORMED_RESPONSE", reason="Open Food Facts returned a non-object payload.", queried_gtin=gtin_code)
                product = payload.get("product") if isinstance(payload, dict) else None
                if payload.get("status") == 0 or product is None:
                    return self._outcome("NOT_FOUND", reason="Open Food Facts did not find a product record.", queried_gtin=gtin_code)
                identity = self._product_identity(product)
                if identity is None:
                    return self._outcome("MALFORMED_RESPONSE", reason="Open Food Facts payload is missing usable product identity values.", queried_gtin=gtin_code)
                return {
                    **self._outcome("VERIFIED", reason=None, queried_gtin=gtin_code, product_identity=identity),
                    "verified": True,
                    "product_identity": identity,
                    "source_url": f"{url}?fields={','.join(params['fields'].split(','))}",
                }
            except httpx.TimeoutException:
                last_error = "Open Food Facts request timed out."
            except (httpx.TransportError, OSError):
                last_error = "Open Food Facts transport failed."
            if attempt >= self.max_retries:
                break
        return self._outcome("TIMEOUT" if last_error and "timed out" in last_error.lower() else "UNAVAILABLE", reason=last_error or "Open Food Facts request failed.", queried_gtin=gtin_code)
