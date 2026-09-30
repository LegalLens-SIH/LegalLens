from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Protocol


class EMMAAPConfigError(RuntimeError):
    pass


class EMaapProvider(Protocol):
    """Contract for a separately documented and approved provider adapter."""

    def availability(self) -> dict[str, Any]: ...

    def submit(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")


class EMMAAPClient:
    """Validate package payloads; a documented adapter owns external transport."""

    def __init__(self, *, endpoint: str | None = None, api_key: str | None = None,
                 timeout: float | None = None, max_retries: int | None = None,
                 provider: EMaapProvider | None = None, simulator_enabled: bool | None = None):
        self.endpoint = (endpoint if endpoint is not None else os.getenv("EMAAP_ENDPOINT", "")).strip()
        self.api_key = (api_key if api_key is not None else os.getenv("EMAAP_API_KEY", "")).strip()
        self.provider = provider
        self.simulator_enabled = (
            os.getenv("EMAAP_SIMULATOR_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
            if simulator_enabled is None else simulator_enabled
        )
        try:
            self.timeout = float(timeout if timeout is not None else os.getenv("EMAAP_TIMEOUT_SECONDS", "10"))
            self.max_retries = int(max_retries if max_retries is not None else os.getenv("EMAAP_MAX_RETRIES", "2"))
        except (TypeError, ValueError) as exc:
            raise EMMAAPConfigError("e-Maap timeout/retry configuration is invalid") from exc
        if not 1 <= self.timeout <= 60 or not 0 <= self.max_retries <= 5:
            raise EMMAAPConfigError("e-Maap timeout or retry count is outside the supported bounds")

    @classmethod
    def from_env(cls) -> "EMMAAPClient":
        return cls()

    def availability(self) -> dict[str, Any]:
        if self.provider is None:
            if self.simulator_enabled:
                return {
                    "status": "SIMULATED",
                    "configured": True,
                    "available": False,
                    "simulated": True,
                    "portal_url": "https://emaap.gov.in/about_us",
                }
            return {
                "status": "API_NOT_DOCUMENTED",
                "configured": False,
                "available": False,
                "portal_url": "https://emaap.gov.in/about_us",
            }
        try:
            provider_status = self.provider.availability()
        except Exception:
            provider_status = {"status": "PROVIDER_UNAVAILABLE", "configured": False, "available": False}
        if not isinstance(provider_status, dict):
            provider_status = {"status": "PROVIDER_UNAVAILABLE", "configured": False, "available": False}
        status = provider_status.get("status")
        allowed_statuses = {"NOT_CONFIGURED", "AUTHENTICATION_REQUIRED", "PROVIDER_UNAVAILABLE", "SUCCESS", "PROVIDER_ERROR"}
        if status not in allowed_statuses:
            status = "PROVIDER_UNAVAILABLE"
        return {
            "status": status,
            "configured": provider_status.get("configured") is True,
            "available": status == "SUCCESS" and provider_status.get("available") is True,
            "portal_url": "https://emaap.gov.in/about_us",
        }

    def _normalized_payload(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("e-Maap payload must be an object")
        artifact_hashes = payload.get("artifact_hashes") or {}
        if not isinstance(artifact_hashes, dict) or len(artifact_hashes) > 10000:
            raise ValueError("artifact_hashes must be a bounded object")
        for artifact_id, digest in artifact_hashes.items():
            if not isinstance(artifact_id, str) or not artifact_id or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                raise ValueError("artifact hash entries must contain SHA-256 values")
        root = payload.get("merkle_root")
        if root is not None and (not isinstance(root, str) or len(root) != 64 or any(c not in "0123456789abcdefABCDEF" for c in root)):
            raise ValueError("merkle_root must be a SHA-256 value")
        base = {
            "schema_version": 1,
            "evidence_package_id": payload.get("evidence_package_id") or payload.get("package_id"),
            "evidence_id": payload.get("evidence_id"),
            "merkle_root": root.lower() if root else None,
            "artifact_hashes": dict(sorted(artifact_hashes.items())),
        }
        if not base["evidence_package_id"] and not base["evidence_id"]:
            raise ValueError("An evidence package or evidence identifier is required")
        payload_sha256 = hashlib.sha256(_canonical_json(base)).hexdigest()
        stable_idempotency = idempotency_key or payload.get("idempotency_key") or hashlib.sha256((str(base["evidence_package_id"] or base["evidence_id"]) + ":" + payload_sha256).encode()).hexdigest()
        if len(str(stable_idempotency)) > 200:
            raise ValueError("idempotency key exceeds 200 characters")
        return {**base, "payload_sha256": payload_sha256, "idempotency_key": str(stable_idempotency)}

    def submit(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        try:
            normalized = self._normalized_payload(payload, idempotency_key=idempotency_key)
        except (TypeError, ValueError) as exc:
            return {"status": "VALIDATION_FAILURE", "transmitted": False, "acknowledged": False, "message": str(exc)}
        body = _canonical_json(normalized)
        if len(body) > 1024 * 1024:
            return {
                **normalized,
                "status": "VALIDATION_FAILURE",
                "transmitted": False,
                "acknowledged": False,
                "message": "e-Maap request exceeds the 1 MiB limit.",
            }
        if self.provider is None:
            if self.simulator_enabled:
                return {
                    **normalized,
                    "status": "SIMULATED",
                    "simulated": True,
                    "transmitted": False,
                    "acknowledged": False,
                    "message": "SIMULATED: no e-Maap request was sent and no acknowledgement was received.",
                }
            return {**normalized, "status": "API_NOT_DOCUMENTED", "transmitted": False, "acknowledged": False, "message": "No documented public e-Maap API adapter is available; no request was sent."}
        provider_status = self.availability()["status"]
        if provider_status != "SUCCESS":
            return {**normalized, "status": provider_status, "transmitted": False, "acknowledged": False, "message": "The configured e-Maap provider is not available for transmission."}
        try:
            response = self.provider.submit(normalized, idempotency_key=normalized["idempotency_key"])
        except Exception as exc:
            return {**normalized, "status": "PROVIDER_UNAVAILABLE", "transmitted": False, "acknowledged": False, "message": f"Provider adapter failed ({type(exc).__name__})."}
        if not isinstance(response, dict):
            return {**normalized, "status": "PROVIDER_ERROR", "transmitted": True, "acknowledged": False, "message": "Provider adapter returned a malformed response."}
        acknowledged = response.get("status") == "SUCCESS" and response.get("acknowledged") is True
        response_status = response.get("status")
        if response_status not in {"SUCCESS", "PROVIDER_ERROR", "PROVIDER_UNAVAILABLE", "AUTHENTICATION_REQUIRED"}:
            response_status = "PROVIDER_ERROR"
        return {
            **normalized,
            **response,
            "status": "SUCCESS" if acknowledged else "PROVIDER_ERROR" if response_status == "SUCCESS" else response_status,
            "transmitted": response.get("transmitted") is True,
            "acknowledged": acknowledged,
        }
