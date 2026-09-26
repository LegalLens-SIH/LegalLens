from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.api.auth import official_user
from backend.database import get_database
from backend.services.evidence_package import EvidencePackage
from backend.services.timestamp_authority import RFC3161TimestampAuthority, record_timestamp_event

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
    authority = RFC3161TimestampAuthority()
    result = authority.request_timestamp(package.merkle_root)
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
    return {"success": True, "timestamp": record}