from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.auth import current_user
from backend.database import get_database

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/events")
def list_audit_events(user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    records = list(db.audit_events.find({"userId": user["_id"]}).sort("createdAt", -1))
    for record in records:
        record["id"] = str(record.pop("_id"))
    return {"success": True, "items": records}


@router.get("/events/{event_id}")
def get_audit_event(event_id: str, user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    record = db.audit_events.find_one({"_id": __import__("bson").ObjectId(event_id)})
    if record is None:
        raise HTTPException(status_code=404, detail="Audit event not found")
    if record.get("userId") != user["_id"]:
        raise HTTPException(status_code=403, detail="You do not have access to that audit record")
    record["id"] = str(record.pop("_id"))
    return {"success": True, "event": record}


@router.patch("/events/{event_id}")
@router.put("/events/{event_id}")
@router.delete("/events/{event_id}")
def reject_audit_mutation(event_id: str, user: dict[str, Any] = Depends(current_user)):
    del user
    del event_id
    raise HTTPException(status_code=405, detail="Audit events are immutable and cannot be modified or deleted.")
