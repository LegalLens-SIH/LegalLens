"""Optional adapter for a local GS1 Digital Link Resolver CE development service.

Resolver CE resolves identifiers to configured resources; it is not a GS1
product registry and does not prove GTIN ownership or product authenticity.
"""

from __future__ import annotations

import ipaddress
import os
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from backend.services.gs1_verification import normalize_gtin

MAX_RESPONSE_BYTES = 128 * 1024
ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PROVIDER_NAME = "GS1 Digital Link Resolver CE"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _validate_local_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Resolver base URL is empty")
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError as exc:
        raise ValueError("Resolver base URL is malformed") from exc
    if (
        parts.scheme != "http"
        or parts.hostname not in ALLOWED_LOCAL_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Resolver base URL must be an HTTP localhost/loopback origin without path or credentials")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Resolver base URL port is outside the valid range")
    host = "[::1]" if parts.hostname == "::1" else parts.hostname
    return f"http://{host}" + (f":{port}" if port is not None else "")


def _safe_resource(value: str, base_url: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    resolved = urljoin(base_url + "/", value)
    try:
        parts = urlsplit(resolved)
        _ = parts.port
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return None
        try:
            address = ipaddress.ip_address(parts.hostname.strip("[]"))
        except ValueError:
            address = None
        if address is not None and (
            address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified
        ):
            return None
        host = parts.hostname.lower()
        if host == "localhost" or host.endswith(".localhost"):
            return None
        return resolved
    except ValueError:
        return None


class GS1ResolverCEProvider:
    """Local-only, read-only adapter using documented Digital Link resolution."""

    provider_name = PROVIDER_NAME
    provider_kind = "digital_link_resolver"

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str = "http://localhost:8080",
        timeout_seconds: float = 3.0,
        max_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
        synthetic_gtins: set[str] | None = None,
    ):
        self.enabled = enabled
        self._transport = transport
        self._configuration_error = None
        try:
            self.base_url = _validate_local_base_url(base_url).rstrip("/")
        except ValueError as exc:
            self.base_url = ""
            self._configuration_error = str(exc)
        if not 0.25 <= timeout_seconds <= 10:
            self._configuration_error = "Resolver timeout must be between 0.25 and 10 seconds"
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 2:
            self._configuration_error = "Resolver max retries must be between 0 and 2"
        self.timeout_seconds = min(10.0, max(0.25, timeout_seconds))
        self.max_retries = min(2, max(0, max_retries)) if isinstance(max_retries, int) else 0
        self.synthetic_gtins = {
            str(value).strip().zfill(14)
            for value in (synthetic_gtins or set())
            if str(value).strip().isascii() and str(value).strip().isdigit() and len(str(value).strip()) in {8, 12, 13, 14}
        }

    @classmethod
    def from_env(cls) -> "GS1ResolverCEProvider":
        try:
            timeout = float(os.getenv("GS1_RESOLVER_TIMEOUT_SECONDS", "3"))
            retries = int(os.getenv("GS1_RESOLVER_MAX_RETRIES", "1"))
        except ValueError:
            timeout = 3.0
            retries = 1
            provider = cls(
                enabled=_env_bool("GS1_RESOLVER_ENABLED"),
                base_url=os.getenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080"),
                synthetic_gtins=set(os.getenv("GS1_RESOLVER_SYNTHETIC_GTINS", "").split(",")),
            )
            provider._configuration_error = "Resolver timeout/retry configuration is malformed"
            return provider
        return cls(
            enabled=_env_bool("GS1_RESOLVER_ENABLED"),
            base_url=os.getenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080"),
            timeout_seconds=timeout,
            max_retries=retries,
            synthetic_gtins=set(os.getenv("GS1_RESOLVER_SYNTHETIC_GTINS", "").split(",")),
        )

    def availability(self) -> dict[str, object]:
        if self._configuration_error:
            return {
                "status": "INVALID_CONFIGURATION",
                "provider": self.provider_name,
                "enabled": self.enabled,
                "configured": False,
                "available": False,
                "environment": "development",
                "reason": self._configuration_error,
            }
        if not self.enabled:
            return {
                "status": "DISABLED",
                "provider": self.provider_name,
                "enabled": False,
                "configured": True,
                "available": False,
                "environment": "development",
                "reason": "GS1_RESOLVER_ENABLED is false; no resolver request was sent.",
            }
        return {
            "status": "ENABLED",
            "provider": self.provider_name,
            "enabled": True,
            "configured": True,
            "available": True,
            "environment": "development",
            "reason": "Resolver requests are enabled for a trusted local endpoint; this is not registry verification.",
        }

    def _outcome(self, status: str, *, resources: list[dict[str, str]] | None = None, reason: str | None = None, queried_gtin14: str | None = None) -> dict[str, object]:
        return {
            "status": status,
            "provider": self.provider_name,
            "verified": False,
            "provider_kind": self.provider_kind,
            "source": "GS1 Digital Link Resolver CE",
            "environment": "development",
            "queried_gtin14": queried_gtin14,
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "fixture_status": "synthetic" if queried_gtin14 in self.synthetic_gtins else "not_marked_synthetic",
            "resources": resources or [],
            "reason": reason,
        }

    def _read_response(self, response: httpx.Response, gtin14: str) -> dict[str, object]:
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                return self._outcome("RESPONSE_TOO_LARGE", reason="Resolver response exceeded the size limit.", queried_gtin14=gtin14)
        if response.status_code == 307:
            location = _safe_resource(response.headers.get("location", ""), self.base_url)
            if location is None:
                return self._outcome("MALFORMED_RESPONSE", reason="Resolver redirect did not contain a safe HTTP(S) resource.", queried_gtin14=gtin14)
            return self._outcome("RESOLVED", resources=[{"href": location}], queried_gtin14=gtin14)
        if response.status_code == 300:
            try:
                payload = httpx.Response(response.status_code, content=bytes(body)).json()
            except ValueError:
                return self._outcome("MALFORMED_RESPONSE", reason="Resolver multiple-links response was not valid JSON.", queried_gtin14=gtin14)
            linkset = payload.get("linkset") if isinstance(payload, dict) else None
            if not isinstance(linkset, list) or len(linkset) > 100:
                return self._outcome("MALFORMED_RESPONSE", reason="Resolver multiple-links response had an invalid linkset.", queried_gtin14=gtin14)
            resources: list[dict[str, str]] = []
            for link in linkset:
                href = _safe_resource(link.get("href"), self.base_url) if isinstance(link, dict) else None
                if href is None:
                    return self._outcome("MALFORMED_RESPONSE", reason="Resolver linkset contained an invalid resource URL.", queried_gtin14=gtin14)
                resources.append({"href": href})
            return self._outcome("RESOLVED" if resources else "NOT_FOUND", resources=resources, queried_gtin14=gtin14)
        if response.status_code == 404:
            return self._outcome("NOT_FOUND", reason="The local resolver has no resource for this Digital Link.", queried_gtin14=gtin14)
        if response.status_code == 400:
            return self._outcome("INVALID_DIGITAL_LINK", reason="Resolver rejected the Digital Link request.", queried_gtin14=gtin14)
        if response.status_code in {401, 403}:
            return self._outcome("AUTHENTICATION_FAILURE", reason="Resolver rejected the request.", queried_gtin14=gtin14)
        if response.status_code >= 500:
            return self._outcome("UNAVAILABLE", reason=f"Resolver returned HTTP {response.status_code}.", queried_gtin14=gtin14)
        return self._outcome("HTTP_ERROR", reason=f"Resolver returned unexpected HTTP {response.status_code}.", queried_gtin14=gtin14)

    def verify_gtin(self, gtin: str) -> dict[str, object]:
        validation = normalize_gtin(gtin)
        if not validation["valid"]:
            return self._outcome("INVALID_GTIN", reason="Only a locally valid GTIN can be resolved.")
        if self._configuration_error:
            return self._outcome("INVALID_CONFIGURATION", reason=self._configuration_error)
        if not self.enabled:
            return self._outcome("DISABLED", reason="GS1_RESOLVER_ENABLED is false; no resolver request was sent.")
        gtin14 = str(validation["gtin"]).zfill(14)
        url = f"{self.base_url}/01/{gtin14}"
        kwargs: dict[str, object] = {
            "timeout": httpx.Timeout(self.timeout_seconds),
            "follow_redirects": False,
            "trust_env": False,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        try:
            with httpx.Client(**kwargs) as client:
                with client.stream("GET", url) as response:
                    return self._read_response(response, gtin14)
        except httpx.InvalidURL:
            return self._outcome("MALFORMED_RESPONSE", reason="Resolver returned an invalid redirect target.", queried_gtin14=gtin14)
        except httpx.TimeoutException as exc:
            raise TimeoutError("Resolver request timed out") from exc
        except httpx.TransportError as exc:
            raise ConnectionError("Resolver connection failed") from exc
