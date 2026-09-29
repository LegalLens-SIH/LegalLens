from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.auth import official_user
from backend.database import get_database
from backend.services.evidence_package import EvidencePackage
from backend.services.timestamp_authority import RFC3161TimestampAuthority, TimestampConfigError, record_timestamp_event

router = APIRouter(prefix="/api/evidence-packages", tags=["evidence"])


def _owned_package(db, package_id: str, user: dict[str, Any]) -> dict[str, Any]:
    package = db.evidence_packages.find_one({"package_id": package_id})
    if package is None:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    scan = db.scans.find_one({
        "userId": user["_id"],
        "$or": [
            {"evidencePackageId": package_id},
            {"listingVerification.evidence_package_id": package_id},
            {"supplementalEvidencePackageIds": package_id},
        ],
    }, {"id": 1})
    if scan is None:
        raise HTTPException(status_code=404, detail="Evidence package not found")
    try:
        parsed = EvidencePackage.model_validate(package)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Stored evidence package is invalid") from exc
    if not parsed.verify()["valid"]:
        raise HTTPException(status_code=409, detail="Stored evidence package Merkle root is invalid")
    return package


@router.post("/{package_id}/timestamp")
def timestamp_evidence_package(package_id: str, user: dict[str, Any] = Depends(official_user)) -> dict[str, Any]:
    database = get_database()
    package_doc = _owned_package(database, package_id, user)
    package = EvidencePackage.model_validate(package_doc)
    try:
        authority = RFC3161TimestampAuthority()
        result = authority.request_timestamp(package.merkle_root)
    except TimestampConfigError:
        result = {"status": "UNAVAILABLE", "timestamped": False, "message": "TSA configuration is invalid or unavailable."}
    audit_event = record_timestamp_event(
        database,
        entity_id=package_id,
        entity_type="evidence_package",
        digest=package.merkle_root,
        timestamp_result=result,
        actor_role=user.get("role"),
        user_id=user["_id"],
    )
    record = {
        "package_id": package_id,
        "evidence_id": package.evidence_id,
        "merkle_root": package.merkle_root,
        "timestamp_result": result,
        "audit_event_hash": audit_event["eventHash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    inserted = database.timestamp_authority_records.insert_one(record)
    record["id"] = str(inserted.inserted_id)
    record.pop("_id", None)
    database.evidence_packages.update_one({"package_id": package_id}, {"$addToSet": {
        "timestamp_refs": str(inserted.inserted_id), "audit_refs": audit_event["eventHash"],
    }})
    return {"success": result.get("timestamped") is True, "recorded": True, "timestamp": _json_safe_timestamp(record)}


@router.get("/{package_id}/timestamps")
def list_evidence_timestamps(package_id: str, user: dict[str, Any] = Depends(official_user)) -> dict[str, Any]:
    database = get_database()
    _owned_package(database, package_id, user)
    records = list(database.timestamp_authority_records.find({"package_id": package_id}).sort("created_at", -1).limit(20))
    return {"success": True, "timestamps": [_json_safe_timestamp(record) for record in records]}


@router.get("/{package_id}/timestamp-status")
def evidence_timestamp_status(package_id: str, user: dict[str, Any] = Depends(official_user)) -> dict[str, Any]:
    _owned_package(get_database(), package_id, user)
    try:
        status = RFC3161TimestampAuthority().availability()
    except TimestampConfigError:
        status = {"status": "UNAVAILABLE", "configured": False, "available": False}
    return {"success": True, "timestamp_authority": status}


def _json_safe_timestamp(record: dict[str, Any]) -> dict[str, Any]:
    output = dict(record)
    output["id"] = str(output.pop("_id", ""))
    if output.get("created_at") is not None:
        output["created_at"] = output["created_at"].isoformat() if hasattr(output["created_at"], "isoformat") else str(output["created_at"])
    # The token remains in protected server storage and evidence packages;
    # list responses expose its digest and verification summary only.
    result = output.get("timestamp_result") or {}
    if isinstance(result, dict):
        output["timestamp_result"] = {key: value for key, value in result.items() if key not in {"token_der_base64", "request_der_base64"}}
    return output
