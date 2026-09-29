from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.auth import current_user
from backend.database import get_database
from backend.services.audit import append_audit_event
from backend.services.em_maap_client import EMMAAPClient, EMMAAPConfigError
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


@router.get("/provider-status")
def provider_status(user: dict[str, Any] = Depends(current_user)):
    try:
        return {"success": True, "provider": EMMAAPClient.from_env().availability()}
    except EMMAAPConfigError:
        return {"success": False, "provider": {"status": "PROVIDER_UNAVAILABLE", "configured": False, "available": False}}


def _owned_package(db, package_id: str, user: dict[str, Any]) -> EvidencePackage:
    package_doc = db.evidence_packages.find_one({"package_id": package_id})
    if package_doc is None:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    owner = db.scans.find_one({"userId": user["_id"], "$or": [
        {"evidencePackageId": package_id}, {"listingVerification.evidence_package_id": package_id},
        {"supplementalEvidencePackageIds": package_id},
    ]}, {"id": 1})
    if owner is None:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    try:
        package = EvidencePackage.model_validate(package_doc)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Stored evidence package is invalid") from exc
    if not package.verify()["valid"]:
        raise HTTPException(status_code=409, detail="Stored evidence package Merkle root is invalid")
    return package


def _submit_package(db, package: EvidencePackage, user: dict[str, Any]) -> dict[str, Any]:
    stable_key = f"{package.package_id}:{package.merkle_root}"
    prior = db.em_maap_transmissions.find_one({"userId": user["_id"], "idempotency_key": stable_key})
    if prior is not None:
        prior = _json_safe(prior)
        prior["id"] = prior.pop("_id", "")
        return {"success": prior.get("acknowledged") is True, "request_recorded": True, "replayed": True, "transmission": prior}
    artifact_hashes = {artifact.artifact_id: artifact.sha256 for artifact in package.artifacts}
    try:
        result = EMMAAPClient.from_env().submit({
            "evidence_package_id": package.package_id, "evidence_id": package.evidence_id,
            "merkle_root": package.merkle_root, "artifact_hashes": artifact_hashes,
        }, idempotency_key=stable_key)
    except EMMAAPConfigError:
        result = {"status": "CONFIGURATION_INVALID", "transmitted": False, "acknowledged": False, "message": "e-Maap configuration is invalid or unavailable."}
    record = {
        "userId": user["_id"], "role": user.get("role"), "status": result.get("status", "UNAVAILABLE"),
        "acknowledged": result.get("acknowledged") is True, "idempotency_key": stable_key,
        "evidence_package_id": package.package_id, "evidence_id": package.evidence_id,
        "merkle_root": package.merkle_root, "artifact_hashes": artifact_hashes,
        "request_hash": result.get("payload_sha256"), "response": result,
        "createdAt": datetime.now(timezone.utc),
    }
    inserted = db.em_maap_transmissions.insert_one(record)
    transmission_id = str(inserted.inserted_id)
    db.evidence_packages.update_one({"package_id": package.package_id}, {"$addToSet": {"transmission_refs": transmission_id}})
    audit_event = append_audit_event(db, event_type="em_maap_transmission", entity_type="evidence_sync", entity_id=package.package_id,
        user_id=user["_id"], actor_role=user.get("role"), action=record["status"],
        details={"transmission_id": transmission_id, "status": record["status"], "acknowledged": record["acknowledged"], "merkle_root": package.merkle_root, "request_hash": record["request_hash"], "response_sha256": result.get("response_sha256")})
    db.evidence_packages.update_one({"package_id": package.package_id}, {"$addToSet": {
        "transmission_refs": transmission_id, "audit_refs": audit_event["eventHash"],
    }})
    record = _json_safe(record)
    record["id"] = transmission_id
    return {"success": record["acknowledged"], "request_recorded": True, "replayed": False, "transmission": record}


@router.post("/transmit")
def transmit(payload: EmMaapTransmitRequest, user: dict[str, Any] = Depends(current_user)):
    if not payload.evidence_package_id:
        raise HTTPException(status_code=400, detail="A stored evidence_package_id is required")
    db = get_database()
    package = _owned_package(db, payload.evidence_package_id, user)
    if payload.merkle_root and payload.merkle_root != package.merkle_root:
        raise HTTPException(status_code=409, detail="Submitted Merkle root does not match the stored evidence package")
    return _submit_package(db, package, user)


@router.post("/transmit-package/{package_id}")
def transmit_stored_package(package_id: str, user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    package = _owned_package(db, package_id, user)
    return _submit_package(db, package, user)


@router.get("/status/{transmission_id}")
def get_status(transmission_id: str, user: dict[str, Any] = Depends(current_user)):
    try:
        object_id = ObjectId(transmission_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Transmission not found") from exc
    record = get_database().em_maap_transmissions.find_one({"_id": object_id, "userId": user["_id"]})
    if record is None:
        raise HTTPException(status_code=404, detail="Transmission not found")
    record = _json_safe(record)
    record["id"] = record.pop("_id")
    return {"success": True, "transmission": record}


@router.get("/package/{package_id}")
def get_package_transmissions(package_id: str, user: dict[str, Any] = Depends(current_user)):
    db = get_database()
    _owned_package(db, package_id, user)
    records = list(db.em_maap_transmissions.find({"evidence_package_id": package_id, "userId": user["_id"]}).sort("createdAt", -1).limit(20))
    safe = []
    for record in records:
        record = _json_safe(record)
        record["id"] = record.pop("_id")
        safe.append(record)
    try:
        availability = EMMAAPClient.from_env().availability()
    except EMMAAPConfigError:
        availability = {"status": "CONFIGURATION_INVALID", "configured": False, "available": False}
    return {"success": True, "configuration": availability, "transmissions": safe}
