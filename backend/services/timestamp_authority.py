from __future__ import annotations

import hashlib
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from backend.services.audit import append_audit_event


_rate_limit_lock = threading.Lock()
_last_tsa_request_at: float | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _default_trust_store() -> str:
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return ssl.get_default_verify_paths().cafile or ""


class TimestampConfigError(RuntimeError):
    pass


class TimestampVerificationError(ValueError):
    pass


def timestamp_config() -> dict[str, Any]:
    try:
        timeout = float(os.getenv("TSA_TIMEOUT_SECONDS", "10"))
        retries = int(os.getenv("TSA_MAX_RETRIES", "2"))
        min_interval = float(os.getenv("TSA_MIN_REQUEST_INTERVAL_SECONDS", "15"))
    except ValueError as exc:
        raise TimestampConfigError("TSA timeout/retry configuration is invalid") from exc
    if not 1 <= timeout <= 60 or not 0 <= retries <= 2 or not 15 <= min_interval <= 3600:
        raise TimestampConfigError("TSA timeout, retry count, or rate limit is outside the supported bounds")
    endpoint = os.getenv("TSA_URL", "").strip() or os.getenv("TSA_ENDPOINT", "").strip() or "https://timestamp.sectigo.com"
    trust_path = os.getenv("TSA_TRUSTED_CERT_PATH", "").strip() or os.getenv("TSA_CERT_PATH", "").strip() or _default_trust_store()
    policy_oid = os.getenv("TSA_POLICY_OID", "").strip()
    if policy_oid and not re.fullmatch(r"[0-2](?:\.(?:0|[1-9][0-9]*))+", policy_oid):
        raise TimestampConfigError("TSA policy OID is invalid")
    return {
        "endpoint": endpoint,
        "enabled": _env_bool("TSA_ENABLED"),
        "username": os.getenv("TSA_USERNAME", "").strip(),
        "password": os.getenv("TSA_PASSWORD", ""),
        "cert_path": trust_path,
        "openssl_path": os.getenv("TSA_OPENSSL_PATH", "").strip() or shutil.which("openssl"),
        "timeout_seconds": timeout,
        "max_retries": retries,
        "min_request_interval_seconds": min_interval,
        "policy_oid": policy_oid,
    }


class RFC3161TimestampAuthority:
    """RFC 3161 client. A timestamp is accepted only after OpenSSL verifies its token."""

    def __init__(self, *, provider: Callable[[str, str | None], dict[str, Any]] | None = None, config: dict[str, Any] | None = None):
        self.config = config or timestamp_config()
        self._uses_default_provider = provider is None
        self.provider = provider or self._default_provider

    def availability(self) -> dict[str, Any]:
        if not self.config.get("enabled", bool(self.config.get("endpoint"))):
            return {"status": "TSA_UNAVAILABLE", "configured": bool(self.config.get("endpoint")), "enabled": False, "available": False, "reason": "timestamping_disabled"}
        if not self.config.get("endpoint"):
            return {"status": "TSA_UNAVAILABLE", "configured": False, "enabled": True, "available": False, "reason": "endpoint_not_configured"}
        endpoint = urlsplit(str(self.config.get("endpoint")))
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment:
            return {"status": "TSA_UNAVAILABLE", "configured": True, "enabled": True, "available": False, "reason": "unsafe_endpoint_configuration"}
        trust_path = Path(str(self.config.get("cert_path") or ""))
        trust_available = trust_path.is_file()
        ready = bool(self.config.get("openssl_path") and trust_available)
        return {
            "status": "READY" if ready else "TSA_UNAVAILABLE",
            "configured": True,
            "enabled": True,
            "available": ready,
            "provider": endpoint.hostname,
            "openssl_available": bool(self.config.get("openssl_path")),
            "trust_anchor_available": trust_available,
            "minimum_request_interval_seconds": self.config.get("min_request_interval_seconds", 15.0),
        }

    def _reserve_request_slot(self) -> tuple[bool, float]:
        global _last_tsa_request_at
        interval = float(self.config.get("min_request_interval_seconds", 15.0))
        now = time.monotonic()
        with _rate_limit_lock:
            if _last_tsa_request_at is not None:
                remaining = interval - (now - _last_tsa_request_at)
                if remaining > 0:
                    return False, remaining
            _last_tsa_request_at = now
        return True, 0.0

    def _default_provider(self, digest: str, nonce: str | None = None) -> dict[str, Any]:
        endpoint = str(self.config.get("endpoint") or "").strip()
        openssl = self.config.get("openssl_path")
        ca_path = str(self.config.get("cert_path") or "").strip()
        if not self.config.get("enabled", bool(endpoint)):
            return {"status": "TSA_UNAVAILABLE", "timestamped": False, "message": "TSA_ENABLED is false."}
        if not endpoint:
            return {"status": "TSA_UNAVAILABLE", "timestamped": False, "message": "TSA endpoint is not configured."}
        endpoint_parts = urlsplit(endpoint)
        if endpoint_parts.scheme != "https" or not endpoint_parts.hostname or endpoint_parts.username or endpoint_parts.password or endpoint_parts.fragment:
            return {"status": "UNAVAILABLE", "timestamped": False, "message": "TSA endpoint must be a credential-safe HTTPS URL without embedded credentials or fragments."}
        if not openssl or not ca_path or not Path(ca_path).is_file():
            return {"status": "UNAVAILABLE", "timestamped": False, "message": "OpenSSL and a trusted TSA verification CA file are required."}
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest or ""):
            raise TimestampConfigError("RFC 3161 requires a 32-byte SHA-256 digest in hexadecimal form")
        timeout = float(self.config.get("timeout_seconds", 10))
        retries = min(2, max(0, int(self.config.get("max_retries", 1))))
        policy_oid = str(self.config.get("policy_oid") or "").strip()
        with tempfile.TemporaryDirectory(prefix="legallense-tsa-") as temp_dir:
            query_path = Path(temp_dir) / "request.tsq"
            reply_path = Path(temp_dir) / "reply.tsr"
            try:
                query_command = [str(openssl), "ts", "-query", "-digest", digest.lower(), "-sha256", "-cert"]
                if policy_oid:
                    query_command.extend(["-tspolicy", policy_oid])
                query_command.extend(["-out", str(query_path)])
                generated = subprocess.run(
                    query_command,
                    capture_output=True, timeout=timeout, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return {"status": "UNAVAILABLE", "timestamped": False, "message": "RFC 3161 request generation failed or timed out."}
            if generated.returncode != 0 or not query_path.is_file():
                return {"status": "UNAVAILABLE", "timestamped": False, "message": "OpenSSL could not generate an RFC 3161 request."}
            try:
                query_text = subprocess.run([str(openssl), "ts", "-query", "-in", str(query_path), "-text"], capture_output=True, text=True, timeout=timeout, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return {"status": "UNAVAILABLE", "timestamped": False, "message": "RFC 3161 request metadata could not be read."}
            if query_text.returncode != 0:
                return {"status": "UNAVAILABLE", "timestamped": False, "message": "RFC 3161 request metadata could not be read."}
            nonce_match = re.search(r"Nonce:\s*(0x[0-9a-fA-F]+|[0-9]+)", query_text.stdout or "")
            request_nonce = nonce_match.group(1).lower() if nonce_match else nonce
            if not request_nonce:
                return {"status": "UNAVAILABLE", "timestamped": False, "message": "OpenSSL did not generate a verifiable RFC 3161 nonce."}
            if nonce is not None and request_nonce != nonce.lower():
                return {"status": "UNAVAILABLE", "timestamped": False, "message": "Configured OpenSSL generated its own nonce; caller-supplied nonce injection is unsupported."}
            auth = None
            username, password = self.config.get("username"), self.config.get("password")
            if username or password:
                if not username or not password:
                    return {"status": "UNAVAILABLE", "timestamped": False, "message": "Both TSA username and password must be configured."}
                auth = (username, password)
            last_reason = "TSA request failed"
            for attempt in range(1, retries + 2):
                slot_available, wait_seconds = self._reserve_request_slot()
                if not slot_available:
                    if attempt == 1:
                        return {
                            "status": "RATE_LIMITED",
                            "timestamped": False,
                            "retry_after_seconds": round(wait_seconds, 2),
                            "message": "TSA request interval has not elapsed; retry later.",
                        }
                    time.sleep(wait_seconds)
                    slot_available, _ = self._reserve_request_slot()
                    if not slot_available:
                        return {"status": "RATE_LIMITED", "timestamped": False, "message": "TSA request was rate limited."}
                try:
                    with httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False, verify=True, auth=auth) as client:
                        with client.stream("POST", endpoint, content=query_path.read_bytes(), headers={"Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"}) as response:
                            if response.status_code != 200:
                                last_reason = f"TSA HTTP response {response.status_code}"
                                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                                    break
                                continue
                            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                            if content_type != "application/timestamp-reply":
                                return {"status": "INVALID_RESPONSE", "timestamped": False, "message": "TSA returned an unexpected response content type."}
                            chunks = bytearray()
                            for chunk in response.iter_bytes():
                                chunks.extend(chunk)
                                if len(chunks) > 1024 * 1024:
                                    last_reason = "TSA response exceeded the 1 MiB limit"
                                    chunks.clear()
                                    break
                            if not chunks:
                                break
                            reply_path.write_bytes(chunks)
                    verified = subprocess.run(
                        [str(openssl), "ts", "-verify", "-queryfile", str(query_path), "-in", str(reply_path), "-CAfile", ca_path],
                        capture_output=True, text=True, timeout=timeout, check=False,
                    )
                    if verified.returncode != 0:
                        return {"status": "INVALID_TOKEN", "timestamped": False, "message": "TSA token signature, imprint, nonce, or trust chain verification failed."}
                    token_text = subprocess.run([str(openssl), "ts", "-reply", "-in", str(reply_path), "-text"], capture_output=True, text=True, timeout=timeout, check=False)
                    if token_text.returncode != 0:
                        return {"status": "INVALID_TOKEN", "timestamped": False, "message": "TSA token metadata could not be parsed."}
                    output = token_text.stdout
                    if "Status: Granted" not in output and "Status: Granted with modifications" not in output:
                        return {"status": "INVALID_TOKEN", "timestamped": False, "message": "TSA response did not grant a timestamp."}
                    timestamp_match = re.search(r"Time stamp:\s*(.+)", output)
                    policy_match = re.search(r"Policy OID:\s*([^\r\n]+)", output)
                    serial_match = re.search(r"Serial number:\s*([^\r\n]+)", output)
                    tsa_match = re.search(r"^TSA:\s*([^\r\n]+)", output, re.MULTILINE)
                    signer_match = re.search(r"^Signer:\s*([^\r\n]+)", output, re.MULTILINE)
                    imprint_match = re.search(r"(?:Hash|Message data):\s*([0-9a-fA-F]{64})", output)
                    if imprint_match and imprint_match.group(1).lower() != digest.lower():
                        return {"status": "INVALID_TOKEN", "timestamped": False, "message": "Verified TSA token does not contain the requested SHA-256 imprint."}
                    if policy_oid and (policy_match is None or policy_match.group(1).strip() != policy_oid):
                        return {"status": "INVALID_TOKEN", "timestamped": False, "message": "TSA token policy does not match the configured policy OID."}
                    returned_nonce_match = re.search(r"Nonce:\s*(0x[0-9a-fA-F]+|[0-9]+)", output)
                    returned_nonce = returned_nonce_match.group(1).lower() if returned_nonce_match else None
                    if request_nonce is not None and returned_nonce != request_nonce:
                        return {"status": "INVALID_TOKEN", "timestamped": False, "message": "TSA token nonce does not match the generated request."}
                    return {
                        "status": "TIMESTAMPED", "timestamped": True, "verified": True,
                        "message_imprint_sha256": digest.lower(), "nonce": returned_nonce,
                        "tsa_url": endpoint,
                        "timestamp": timestamp_match.group(1).strip() if timestamp_match else None,
                        "policy_oid": policy_match.group(1).strip() if policy_match else None,
                        "serial_number": serial_match.group(1).strip() if serial_match else None,
                        "tsa_identity": tsa_match.group(1).strip() if tsa_match else None,
                        "signer_certificate": signer_match.group(1).strip() if signer_match else None,
                        "token_der_base64": __import__("base64").b64encode(reply_path.read_bytes()).decode("ascii"),
                        "request_der_base64": __import__("base64").b64encode(query_path.read_bytes()).decode("ascii"),
                        "token_sha256": hashlib.sha256(reply_path.read_bytes()).hexdigest(),
                        "trust_anchor_file": Path(ca_path).name,
                        "trust_anchor_sha256": hashlib.sha256(Path(ca_path).read_bytes()).hexdigest(),
                        "signature_chain_verified": True, "attempt": attempt,
                    }
                except httpx.HTTPError as exc:
                    last_reason = f"TSA transport failed ({type(exc).__name__})"
                except (OSError, subprocess.TimeoutExpired):
                    last_reason = "TSA verification timed out or could not run"
                if attempt <= retries:
                    time.sleep(min(float(self.config.get("min_request_interval_seconds", 15.0)), 60.0))
            return {"status": "UNAVAILABLE", "timestamped": False, "message": last_reason}

    def request_timestamp(self, digest: str, *, nonce: str | None = None, token_id: str | None = None) -> dict[str, Any]:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise TimestampConfigError("A valid SHA-256 digest is required for timestamping")
        request = {"digest_algorithm": "sha256", "digest": digest.lower(), "nonce": nonce,
                   "request_id": token_id or hashlib.sha256(digest.encode("ascii")).hexdigest()[:24],
                   "created_at": datetime.now(timezone.utc).isoformat()}
        if self._uses_default_provider and not self.config.get("enabled", bool(self.config.get("endpoint"))):
            return {"status": "TSA_UNAVAILABLE", "timestamped": False, "message": "TSA_ENABLED is false.", "request": request}
        try:
            result = self.provider(digest.lower(), nonce)
        except Exception as exc:
            result = {"status": "UNAVAILABLE", "timestamped": False, "message": f"TSA provider failed ({type(exc).__name__})."}
        if not isinstance(result, dict):
            result = {"status": "UNAVAILABLE", "timestamped": False, "message": "TSA provider returned an invalid response."}
        # No provider-supplied boolean is trusted. Only the concrete transport
        # implementation can set verified=True after OpenSSL validation.
        valid = result.get("status") == "TIMESTAMPED" and result.get("verified") is True and result.get("message_imprint_sha256") == digest.lower() and bool(result.get("token_der_base64"))
        if valid:
            try:
                self.verify(result, digest=digest.lower(), expected_nonce=result.get("nonce"))
            except TimestampVerificationError:
                valid = False
        result["timestamped"] = valid
        result["request"] = request
        if not valid and result.get("status") == "TIMESTAMPED":
            result["status"] = "INVALID_TOKEN"
        if not valid and result.get("status") not in {"TSA_UNAVAILABLE", "UNAVAILABLE", "INVALID_TOKEN"}:
            result["status"] = "UNAVAILABLE"
        return result

    def verify(self, token: dict[str, Any], *, digest: str, expected_nonce: str | None = None) -> dict[str, Any]:
        if not isinstance(token, dict) or token.get("timestamped") is not True or token.get("message_imprint_sha256") != digest.lower():
            raise TimestampVerificationError("A timestamp token matching the package digest is required")
        if expected_nonce is not None and token.get("nonce") != expected_nonce:
            raise TimestampVerificationError("Timestamp nonce does not match the expected value")
        if not self.config.get("openssl_path") or not Path(str(self.config.get("cert_path") or "")).is_file():
            raise TimestampVerificationError("OpenSSL or the configured TSA trust anchor is unavailable")
        try:
            query_bytes = __import__("base64").b64decode(token["request_der_base64"], validate=True)
            reply_bytes = __import__("base64").b64decode(token["token_der_base64"], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise TimestampVerificationError("Stored RFC 3161 request/token is missing or malformed") from exc
        if len(query_bytes) > 64 * 1024 or not 0 < len(reply_bytes) <= 1024 * 1024:
            raise TimestampVerificationError("Stored RFC 3161 request/token exceeds size limits")
        with tempfile.TemporaryDirectory(prefix="legallense-tsa-verify-") as directory:
            query_path, reply_path = Path(directory) / "request.tsq", Path(directory) / "reply.tsr"
            query_path.write_bytes(query_bytes); reply_path.write_bytes(reply_bytes)
            try:
                result = subprocess.run([str(self.config["openssl_path"]), "ts", "-verify", "-queryfile", str(query_path), "-in", str(reply_path), "-CAfile", str(self.config["cert_path"])], capture_output=True, text=True, timeout=float(self.config.get("timeout_seconds", 10)), check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TimestampVerificationError("RFC 3161 token verification could not complete") from exc
            if result.returncode != 0:
                raise TimestampVerificationError("RFC 3161 token signature, imprint, nonce, or trust chain verification failed")
        if hashlib.sha256(reply_bytes).hexdigest() != token.get("token_sha256"):
            raise TimestampVerificationError("Stored RFC 3161 token digest does not match its contents")
        return {"valid": True, "message_imprint_sha256": digest.lower(), "nonce": token.get("nonce"), "timestamp": token.get("timestamp"), "policy_oid": token.get("policy_oid"), "token_sha256": token.get("token_sha256")}


def record_timestamp_event(db, *, entity_id: str, entity_type: str, digest: str, timestamp_result: dict[str, Any], actor_role: str | None = None, user_id: Any | None = None) -> dict[str, Any]:
    details = {"digest": digest, "timestamp_status": timestamp_result.get("status"), "timestamped": timestamp_result.get("timestamped") is True, "token_sha256": timestamp_result.get("token_sha256"), "tsa_url": timestamp_result.get("tsa_url"), "request_id": (timestamp_result.get("request") or {}).get("request_id")}
    return append_audit_event(db, event_type="timestamp_authority", entity_type=entity_type, entity_id=str(entity_id), user_id=user_id, actor_role=actor_role, action=timestamp_result.get("status", "UNAVAILABLE"), details=details)
