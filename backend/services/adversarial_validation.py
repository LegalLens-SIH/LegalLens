from __future__ import annotations

from typing import Any

from backend.services.url_safety import validate_public_url


def detect_evidence_tampering(package: dict[str, Any], manifest: list[dict[str, Any]], *, artifact_lookup: dict[str, Any] | None = None) -> dict[str, Any]:
    changed: list[str] = []
    lookup = artifact_lookup or {}
    for entry in manifest:
        artifact_id = str(entry.get("artifact_id") or entry.get("artifactId") or "")
        if not artifact_id:
            continue
        artifact_hash = entry.get("sha256")
        current = lookup.get(artifact_id)
        if current is None:
            changed.append(artifact_id)
            continue
        if str(current.get("sha256") if isinstance(current, dict) else current) != str(artifact_hash):
            changed.append(artifact_id)
    return {"valid": not changed, "changed_artifacts": changed, "merkle_root": package.get("merkle_root")}


def ensure_replay_safe(record_key: str, existing_records: list[dict[str, Any]]) -> dict[str, Any]:
    prior = [item for item in existing_records if item.get("record_key") == record_key]
    if prior:
        return {"duplicate": True, "existing_record": prior[0]}
    return {"duplicate": False}


def assert_authorized(role: str, *, required_roles: set[str]) -> None:
    if role not in required_roles:
        raise PermissionError(f"Role '{role}' is not authorized for this operation.")


def validate_url_safety_or_raise(url: str) -> dict[str, Any]:
    safe_url = validate_public_url(url)
    return {"safe": True, "url": safe_url.url, "resolved_ips": safe_url.resolved_ips}


def validate_ocr_quality(confidence: float | None, *, minimum: float = 0.80) -> dict[str, Any]:
    if confidence is None:
        return {"status": "missing", "safe": False}
    if confidence < minimum:
        return {"status": "low_confidence", "safe": False, "confidence": float(confidence)}
    return {"status": "ok", "safe": True, "confidence": float(confidence)}
