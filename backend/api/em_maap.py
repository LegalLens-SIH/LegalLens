from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from pydantic import BaseModel, Field

from backend.api.auth import current_user
from backend.database import get_database
from backend.services.em_maap_client import EMMAAPClient, EMMAAPConfigError
from backend.services.audit import append_audit_event
from backend.services.evidence_package import EvidencePackage

router = APIRouter(prefix="/api/em-maap", tags=["em-maap"])


def _json_safe(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


class EmMaapTransmitRequest(BaseModel):
    evidence_package_id: str | None = None
    evidence_id: str | None = None
    merkle_root: str | None = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/transmit")
def transmit(payload: EmMaapTransmitRequest, user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    try:
        client = EMMAAPClient.from_env()
        payload_dict = {
            "evidence_package_id": payload.evidence_package_id,
            "evidence_id": payload.evidence_id,
            "merkle_root": payload.merkle_root,
            "artifact_hashes": payload.artifact_hashes,
            **payload.payload,
        }
        result = client.submit(payload_dict, idempotency_key=f"{payload.evidence_package_id or payload.evidence_id or 'manual'}:{user['_id']}")
        result_doc = {
            "userId": user["_id"],
            "role": user.get("role"),
            "status": result.get("status", "PENDING"),
            "evidence_package_id": payload.evidence_package_id,
            "evidence_id": payload.evidence_id,
            "merkle_root": payload.merkle_root,
            "artifact_hashes": payload.artifact_hashes,
            "response": result,
            "createdAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        }
        result_doc = _json_safe(result_doc)
        inserted = db.em_maap_transmissions.insert_one(result_doc)
        result_doc = _json_safe(result_doc)
        result_doc["id"] = str(inserted.inserted_id)
        result_doc.pop("_id", None)
        append_audit_event(
            db,
            event_type="em_maap_transmission",
            entity_type="evidence_sync",
            entity_id=str(payload.evidence_package_id or payload.evidence_id or inserted.inserted_id),
            user_id=user["_id"],
            actor_role=user.get("role"),
            action=result.get("status", "PENDING"),
            details={"transmission_id": str(inserted.inserted_id), "status": result.get("status", "PENDING"), "merkle_root": payload.merkle_root},
        )
        return {"success": True, "transmission": result_doc}
    except EMMAAPConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"e-Maap synchronization failed: {exc}") from exc


@router.post("/transmit-package/{package_id}")
def transmit_stored_package(package_id: str, user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    package_doc = db.evidence_packages.find_one({"package_id": package_id})
    if package_doc is None:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    owned_scan = db.scans.find_one({
        "userId": user["_id"],
        "$or": [
            {"evidencePackageId": package_id},
            {"listingVerification.evidence_package_id": package_id},
        ],
    }, {"id": 1})
    if owned_scan is None:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    try:
        package = EvidencePackage.model_validate(package_doc)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Stored evidence package is invalid") from exc
    if not package.verify()["valid"]:
        raise HTTPException(status_code=409, detail="Stored evidence package Merkle root is invalid")

    artifact_hashes = {artifact.artifact_id: artifact.sha256 for artifact in package.artifacts}
    try:
        client = EMMAAPClient.from_env()
        result = client.submit({
            "evidence_package_id": package.package_id,
            "evidence_id": package.evidence_id,
            "merkle_root": package.merkle_root,
            "artifact_hashes": artifact_hashes,
        }, idempotency_key=f"{package.package_id}:{user['_id']}")
    except EMMAAPConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"e-Maap synchronization failed: {exc}") from exc

    record_doc = {
        "userId": user["_id"],
        "role": user.get("role"),
        "status": result.get("status", "PENDING"),
        "evidence_package_id": package.package_id,
        "evidence_id": package.evidence_id,
        "merkle_root": package.merkle_root,
        "artifact_hashes": artifact_hashes,
        "response": result,
        "createdAt": datetime.now(timezone.utc),
    }
    inserted = db.em_maap_transmissions.insert_one(record_doc)
    append_audit_event(
        db,
        event_type="em_maap_transmission",
        entity_type="evidence_sync",
        entity_id=package.package_id,
        user_id=user["_id"],
        actor_role=user.get("role"),
        action=result.get("status", "PENDING"),
        details={"transmission_id": str(inserted.inserted_id), "merkle_root": package.merkle_root, "artifact_hashes": artifact_hashes},
    )
    response = _json_safe(record_doc)
    response["id"] = str(inserted.inserted_id)
    return {"success": True, "transmission": response}


@router.get("/status/{transmission_id}")
def get_status(transmission_id: str, user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    record = db.em_maap_transmissions.find_one({"_id": __import__("bson").ObjectId(transmission_id)})
    if record is None:
        raise HTTPException(status_code=404, detail="Transmission not found")
    if record.get("userId") != user["_id"]:
        raise HTTPException(status_code=403, detail="You do not have access to that transmission record")
    record = _json_safe(record)
    record["id"] = record.pop("_id")
    return {"success": True, "transmission": record}
