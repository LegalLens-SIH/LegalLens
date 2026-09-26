from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from backend.services.audit import append_audit_event


class TimestampConfigError(RuntimeError):
    pass


class TimestampVerificationError(ValueError):
    pass


def timestamp_config() -> dict[str, Any]:
    return {
        "endpoint": os.getenv("TSA_ENDPOINT", "").strip(),
        "username": os.getenv("TSA_USERNAME", "").strip(),
        "password": os.getenv("TSA_PASSWORD", "").strip(),
        "cert_path": os.getenv("TSA_CERT_PATH", "").strip(),
        "timeout_seconds": float(os.getenv("TSA_TIMEOUT_SECONDS", "10")),
        "enabled": bool(os.getenv("TSA_ENDPOINT", "").strip()),
    }


class RFC3161TimestampAuthority:
    def __init__(self, *, provider: Callable[[str, str | None], dict[str, Any]] | None = None, config: dict[str, Any] | None = None):
        self.provider = provider or self._default_provider
        self.config = config or timestamp_config()

    def _default_provider(self, digest: str, nonce: str | None = None) -> dict[str, Any]:
        if not self.config.get("endpoint"):
            return {"status": "not_configured", "timestamped": False, "message": "TSA endpoint is not configured."}
        return {
            "status": "not_available",
            "timestamped": False,
            "message": "TSA endpoint is configured but no provider implementation is available in this runtime.",
            "digest": digest,
            "nonce": nonce,
        }

    def request_timestamp(self, digest: str, *, nonce: str | None = None, token_id: str | None = None) -> dict[str, Any]:
        if not digest:
            raise TimestampConfigError("A digest is required for timestamping.")
        request = {
            "digest": digest,
            "nonce": nonce,
            "request_id": token_id or hashlib.sha256(digest.encode("utf-8")).hexdigest()[:24],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        result = self.provider(digest, nonce)
        if isinstance(result, dict) and result.get("status") in {"not_configured", "not_available", "failed"}:
            result["timestamped"] = False
            result.setdefault("request", request)
            return result
        result.setdefault("request", request)
        result["timestamped"] = bool(result.get("token") or result.get("timestamp_token"))
        return result

    def verify(self, token: dict[str, Any], *, digest: str, expected_nonce: str | None = None) -> dict[str, Any]:
        if not isinstance(token, dict):
            raise TimestampVerificationError("Timestamp token is missing or malformed.")
        imprint = token.get("messageImprint") or token.get("imprint") or token.get("digest")
        if imprint != digest:
            raise TimestampVerificationError("Message imprint does not match the provided digest.")
        status = token.get("status") or token.get("tsa_status")
        if status not in {None, "ok", "success", "valid", "verified"}:
            raise TimestampVerificationError(f"TSA response status is not valid: {status!r}")
        nonce = token.get("nonce")
        if expected_nonce is not None and nonce is not None and nonce != expected_nonce:
            raise TimestampVerificationError("Timestamp nonce does not match the expected value.")
        if token.get("token") is None and token.get("timestamp_token") is None:
            raise TimestampVerificationError("Timestamp token is missing.")
        return {"valid": True, "message_imprint": imprint, "status": status or "ok", "nonce": nonce}


def record_timestamp_event(db, *, entity_id: str, entity_type: str, digest: str, timestamp_result: dict[str, Any], actor_role: str | None = None, user_id: Any | None = None) -> dict[str, Any]:
    details = {"digest": digest, "timestamp_result": timestamp_result}
    return append_audit_event(
        db,
        event_type="timestamp_authority",
        entity_type=entity_type,
        entity_id=str(entity_id),
        user_id=user_id,
        actor_role=actor_role,
        action=timestamp_result.get("status", "timestamp_not_available"),
        details=details,
    )
