from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import httpx


class EMMAAPConfigError(RuntimeError):
    pass


class EMMAAPClient:
    def __init__(self, *, endpoint: str | None = None, api_key: str | None = None, timeout: float = 10.0, max_retries: int = 3):
        self.endpoint = (endpoint or os.getenv("EMAAP_ENDPOINT", "")).strip()
        self.api_key = (api_key or os.getenv("EMAAP_API_KEY", "")).strip()
        self.timeout = timeout or float(os.getenv("EMAAP_TIMEOUT_SECONDS", "10"))
        self.max_retries = max_retries or int(os.getenv("EMAAP_MAX_RETRIES", "3"))

    @classmethod
    def from_env(cls) -> "EMMAAPClient":
        return cls(
            endpoint=os.getenv("EMAAP_ENDPOINT", "").strip(),
            api_key=os.getenv("EMAAP_API_KEY", "").strip(),
            timeout=float(os.getenv("EMAAP_TIMEOUT_SECONDS", "10")),
            max_retries=int(os.getenv("EMAAP_MAX_RETRIES", "3")),
        )

    def _normalized_payload(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("e-Maap payload must be a dictionary")
        normalized = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_package_id": payload.get("evidence_package_id") or payload.get("package_id"),
            "evidence_id": payload.get("evidence_id"),
            "merkle_root": payload.get("merkle_root"),
            "artifact_hashes": payload.get("artifact_hashes") or {},
            "status": "PENDING",
            "idempotency_key": idempotency_key or payload.get("idempotency_key") or hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest(),
        }
        return normalized

    def submit(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        if not self.endpoint:
            raise EMMAAPConfigError("EMAAP_ENDPOINT is not configured. External e-Maap synchronization is not available in this runtime.")
        if not self.api_key:
            raise EMMAAPConfigError("EMAAP_API_KEY is not configured. External e-Maap synchronization is not available in this runtime.")
        normalized = self._normalized_payload(payload, idempotency_key=idempotency_key)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Idempotency-Key": normalized["idempotency_key"]}
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = httpx.post(self.endpoint, json=normalized, headers=headers, timeout=self.timeout)
                if 200 <= response.status_code < 300:
                    body = response.json() if response.content else {}
                    result = {"status": "ACKNOWLEDGED", "attempt": attempt, "response": body, **normalized}
                    result["status"] = "ACKNOWLEDGED"
                    return result
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = RuntimeError(f"Retryable e-Maap response: {response.status_code}")
                    continue
                result = {"status": "FAILED", "attempt": attempt, "response": response.text, **normalized}
                result["status"] = "FAILED"
                return result
            except httpx.HTTPError as exc:
                last_error = exc
                continue
        result = {"status": "RETRYING" if last_error is not None else "FAILED", "attempt": self.max_retries, "error": str(last_error) if last_error else "unknown", **normalized}
        result["status"] = "RETRYING" if last_error is not None else "FAILED"
        return result
