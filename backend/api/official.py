"""Official / Legal Metrology Inspector review of manufacturer self-check submissions.

Product principle: AI (the deterministic ComplianceEngine, and the optional
Gemini fallback) is an ASSISTANT only. The official is the final human
decision-maker. This module therefore never reads OR writes a revision's
`complianceResult`/`ocrResult` - it only ever displays them, and records a
completely separate, sibling `officialDecision`/`decisionHistory`. Nothing
here can influence the deterministic engine's own output, and nothing there
can influence a decision recorded here.

Every query below filters on `reviewStatus: {"$ne": None}` - a manufacturer's
self-check is a private iteration tool by design (see manufacturer.py's
create_self_check) until they explicitly submit it; an official must never
be able to browse/guess into an unsubmitted draft.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from pydantic import BaseModel, Field

from backend.api.auth import official_user
from backend.api.manufacturer import SELF_CHECK_IMAGES_COLLECTION, public_revision
from backend.database import get_database

router = APIRouter(prefix="/api/official", tags=["official"])

DecisionAction = Literal["APPROVED", "REJECTED", "NEEDS_CORRECTION", "REQUIRES_PHYSICAL_INSPECTION"]
OPEN_STATUSES = ("PENDING_OFFICIAL_REVIEW", "UNDER_OFFICIAL_REVIEW")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DecisionRequest(BaseModel):
    action: DecisionAction
    remarks: str = Field(min_length=1, max_length=2000)


def _submitted_filter(revision_id: str) -> dict[str, Any]:
    """A revision is visible to officials only once explicitly submitted -
    `reviewStatus: {"$ne": None}` correctly excludes both an explicit `None`
    and a document that predates this field entirely (missing key)."""
    return {"revisionId": revision_id, "reviewStatus": {"$ne": None}}


def _identity_from_user_doc(user_doc: dict[str, Any] | None) -> dict[str, Any]:
    if user_doc is None:
        return {"manufacturerName": "Unknown", "manufacturerCompany": "", "manufacturerEmail": ""}
    return {
        "manufacturerName": user_doc.get("fullName", "Unknown"),
        "manufacturerCompany": user_doc.get("companyName", ""),
        "manufacturerEmail": user_doc.get("email", ""),
    }


def _manufacturer_identity(db, manufacturer_id: Any) -> dict[str, Any]:
    return _identity_from_user_doc(db.users.find_one({"_id": manufacturer_id}, {"fullName": 1, "companyName": 1, "email": 1}))


def _public_official_revision(revision: dict[str, Any], manufacturer_identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same shape manufacturer.py's own public_revision() already produces
    for the manufacturer's own view - reused, not forked (including its
    ObjectId-stringification of officialDecision/decisionHistory) - except
    the image URL is repointed at this module's own (non-ownership-scoped)
    stream route."""
    public = public_revision(revision)
    if public.get("sourceImageId"):
        public["sourceImageUrl"] = f"/api/official/revisions/{public['revisionId']}/image"
    if manufacturer_identity:
        public.update(manufacturer_identity)
    return public


@router.get("/review-queue")
def review_queue(status: str = "open", user: dict[str, Any] = Depends(official_user)):
    """List submitted revisions awaiting (or under) official review, newest first."""
    db = get_database()
    query: dict[str, Any] = {"reviewStatus": {"$in": list(OPEN_STATUSES)}} if status == "open" else {"reviewStatus": status}
    revisions = list(db.productRevisions.find(query).sort("submittedAt", -1))
    manufacturer_ids = {revision["manufacturerId"] for revision in revisions}
    identities = {
        manufacturer["_id"]: _identity_from_user_doc(manufacturer)
        for manufacturer in db.users.find({"_id": {"$in": list(manufacturer_ids)}}, {"fullName": 1, "companyName": 1, "email": 1})
    }
    return {
        "success": True,
        "items": [
            _public_official_revision(revision, identities.get(revision["manufacturerId"], _identity_from_user_doc(None)))
            for revision in revisions
        ],
    }


@router.get("/revisions/{revision_id}")
def get_revision(revision_id: str, user: dict[str, Any] = Depends(official_user)):
    db = get_database()
    revision = db.productRevisions.find_one(_submitted_filter(revision_id))
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    # Best-effort claim signal only - no locking/assignment enforcement, any
    # official may still act on the case. Conditioned on the CURRENT status so
    # a later re-open can never regress an already-decided case backward.
    db.productRevisions.update_one(
        {"revisionId": revision_id, "reviewStatus": "PENDING_OFFICIAL_REVIEW"},
        {"$set": {"reviewStatus": "UNDER_OFFICIAL_REVIEW"}},
    )
    revision = db.productRevisions.find_one(_submitted_filter(revision_id))
    identity = _manufacturer_identity(db, revision["manufacturerId"])
    return {"success": True, "revision": _public_official_revision(revision, identity)}


@router.get("/revisions/{revision_id}/image")
def get_revision_image(revision_id: str, user: dict[str, Any] = Depends(official_user)):
    """Stream a submitted revision's source image - same GridFS bucket
    manufacturer.py's own get_self_check_image uses, without the ownership
    filter (officials review across all manufacturers)."""
    db = get_database()
    revision = db.productRevisions.find_one(_submitted_filter(revision_id), {"sourceImageId": 1})
    if revision is None or not revision.get("sourceImageId"):
        raise HTTPException(status_code=404, detail="Source image not found")
    try:
        image = GridFS(db, collection=SELF_CHECK_IMAGES_COLLECTION).get(ObjectId(revision["sourceImageId"]))
    except NoFile as exc:
        raise HTTPException(status_code=404, detail="Source image not found") from exc
    return StreamingResponse(image, media_type=image.content_type or "application/octet-stream")


@router.post("/revisions/{revision_id}/decision")
def record_decision(revision_id: str, payload: DecisionRequest, user: dict[str, Any] = Depends(official_user)):
    """Persist one official decision. Never touches complianceResult/
    ocrResult - a decision is a sibling fact recorded alongside the AI
    assessment, never a replacement for or derivation of it. Re-deciding
    (e.g. escalation, correction) is allowed: every call appends to
    decisionHistory and only the most recent entry is ever overwritten in
    officialDecision - history itself is append-only."""
    db = get_database()
    revision = db.productRevisions.find_one(_submitted_filter(revision_id))
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    now = utcnow()
    entry = {
        "officialId": user["_id"],
        "officialName": user.get("fullName", "Official"),
        "action": payload.action,
        "remarks": payload.remarks.strip(),
        "decidedAt": now,
    }
    db.productRevisions.update_one(
        {"revisionId": revision_id},
        {"$push": {"decisionHistory": entry}, "$set": {"officialDecision": entry, "reviewStatus": "DECISION_RECORDED"}},
    )
    db.history.insert_one({
        "userId": user["_id"], "actionType": "official_decision_recorded",
        "title": f"Official decision recorded: {payload.action}",
        "description": payload.remarks.strip(), "documentId": revision_id, "createdAt": now,
    })
    revision = db.productRevisions.find_one({"revisionId": revision_id})
    identity = _manufacturer_identity(db, revision["manufacturerId"])
    return {"success": True, "revision": _public_official_revision(revision, identity)}
