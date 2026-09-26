from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    if hasattr(value, "dict"):
        return _json_ready(value.dict())
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_ready(value), sort_keys=True, default=str)


def append_audit_event(
    db,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    user_id: Any | None = None,
    actor_role: str | None = None,
    action: str | None = None,
    file_name: str | None = None,
    source_image_sha256: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an immutable append-only audit event in the audit_events collection."""
    now = datetime.now(timezone.utc)
    previous = db.audit_events.find_one({}, sort=[("createdAt", -1)])
    previous_hash = previous.get("eventHash") if previous else None

    record = {
        "eventType": event_type,
        "action": action or event_type,
        "entityType": entity_type,
        "entityId": entity_id,
        "userId": user_id,
        "actorRole": actor_role,
        "fileName": file_name,
        "sourceImageSha256": source_image_sha256,
        "details": _json_ready(details or {}),
        "createdAt": now,
        "previousHash": previous_hash,
        "immutable": True,
    }
    event_hash = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
    record["eventHash"] = event_hash
    db.audit_events.insert_one(record)
    return record
