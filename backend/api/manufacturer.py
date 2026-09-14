"""Manufacturer-owned products and immutable self-check revisions."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from pydantic import BaseModel, Field

from backend.api.auth import manufacturer_user
from backend.api.ocr import run_ocr
from backend.database import get_database
from backend.ocr.schemas import OCRResult
from backend.services.compliance_engine import ComplianceEngine

SELF_CHECK_IMAGES_COLLECTION = "self_check_images"
# Manufacturer self-checks are always a retail packaged-commodity label
# (the manufacturer's own draft artwork before printing) - the same
# validation profile the official /api/scans flow uses for a retail listing.
# Not the wholesale or pos_hardware_audit profiles, which apply to different
# document types this UI never collects.
SELF_CHECK_VALIDATION_PROFILE = "e_commerce_product_listing"

router = APIRouter(prefix="/api/manufacturer", tags=["manufacturer"])
# One shared engine instance, same one-per-process pattern as backend/api/
# ocr.py's _compliance_engine - reuses the existing rule engine rather than
# building a second, independent compliance implementation.
_compliance_engine = ComplianceEngine()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductRequest(BaseModel):
    productName: str = Field(min_length=1, max_length=160)
    sku: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)


def public_product(product: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in product.items() if key != "_id" and key != "manufacturerId"}


def _public_decision(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """officialDecision/decisionHistory entries (see backend/api/official.py's
    record_decision) carry a raw ObjectId officialId - stringified here since
    this is the shared shape both this endpoint AND official.py's own views
    of a revision return, matching this codebase's existing convention of
    never returning a raw ObjectId (_public_scan, public_user)."""
    if entry is None:
        return None
    entry = dict(entry)
    if entry.get("officialId") is not None:
        entry["officialId"] = str(entry["officialId"])
    return entry


def public_revision(revision: dict[str, Any]) -> dict[str, Any]:
    """Strip Mongo-internal fields and attach the URL to fetch this revision's own uploaded image, if any."""
    public = {key: value for key, value in revision.items() if key != "manufacturerId" and key != "_id"}
    if public.get("sourceImageId"):
        public["sourceImageUrl"] = f"/api/manufacturer/revisions/{public['revisionId']}/image"
    if "officialDecision" in public:
        public["officialDecision"] = _public_decision(public["officialDecision"])
    if "decisionHistory" in public:
        public["decisionHistory"] = [_public_decision(entry) for entry in public["decisionHistory"]]
    return public


@router.get("/products")
def list_products(user: dict[str, Any] = Depends(manufacturer_user)):
    products = get_database().products.find({"manufacturerId": user["_id"]}).sort("updatedAt", -1)
    return {"success": True, "items": [public_product(product) for product in products]}


@router.post("/products")
def create_product(payload: ProductRequest, user: dict[str, Any] = Depends(manufacturer_user)):
    db = get_database()
    now = utcnow()
    product = {"productId": f"PRD-{uuid.uuid4().hex[:12].upper()}", "manufacturerId": user["_id"], "productName": payload.productName.strip(), "sku": payload.sku.strip(), "description": payload.description.strip(), "latestStatus": None, "latestScore": None, "createdAt": now, "updatedAt": now}
    db.products.insert_one(product)
    db.history.insert_one({"userId": user["_id"], "manufacturerId": user["_id"], "actionType": "product_created", "title": "Product created", "description": product["productName"], "documentId": product["productId"], "createdAt": now})
    return {"success": True, "product": public_product(product)}


@router.post("/self-check")
async def create_self_check(
    file: UploadFile = File(...),
    product_id: str = Form(""),
    product_name: str = Form(""),
    user: dict[str, Any] = Depends(manufacturer_user),
):
    db = get_database()
    product = db.products.find_one({"productId": product_id, "manufacturerId": user["_id"]}) if product_id else None
    if product_id and product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    # Read the exact bytes the manufacturer uploaded now, before run_ocr()
    # consumes the stream, so THIS revision's own image can be persisted -
    # mirrors the same read-then-seek(0)-then-run_ocr() pattern the official
    # /api/scans flow already uses in backend/api/ocr.py's create_scan.
    original_contents = await file.read()
    await file.seek(0)
    ocr_response = await run_ocr(file)
    if ocr_response.status_code != 200:
        return ocr_response
    result = OCRResult.model_validate(json.loads(ocr_response.body))
    # Reuse the same real Legal Metrology rule engine the official /api/scans
    # flow already uses (backend/services/compliance_engine.py), instead of
    # the previous 4-check placeholder implementation - so a manufacturer
    # sees every applicable declaration check (manufacturer/packer/importer,
    # country of origin, generic name, net quantity, MRP, unit sale price,
    # mfg date, consumer care details, ...), each with a real PASS/
    # NON_COMPLIANT/NEEDS_MANUAL_VERIFICATION/NOT_APPLICABLE status - not a
    # second, independent compliance implementation.
    compliance = _compliance_engine.evaluate(result.model_dump(), SELF_CHECK_VALIDATION_PROFILE)
    now = utcnow()
    resolved_product_name = product["productName"] if product else product_name.strip() or result.image
    if product is None:
        product = {"productId": f"PRD-{uuid.uuid4().hex[:12].upper()}", "manufacturerId": user["_id"], "productName": resolved_product_name, "sku": "PENDING", "description": "", "createdAt": now}
        db.products.insert_one(product)
    version = db.productRevisions.count_documents({"productId": product["productId"], "manufacturerId": user["_id"]}) + 1
    revision = {
        "revisionId": f"REV-{uuid.uuid4().hex[:12].upper()}", "productId": product["productId"], "manufacturerId": user["_id"],
        "version": version, "imageReference": result.image, "ocrResult": result.model_dump(), "complianceResult": compliance.model_dump(),
        "issues": compliance.missing_fields, "suggestions": compliance.recommendations, "complianceScore": compliance.compliance_score, "createdAt": now,
        # Official review workflow (additive, orthogonal to complianceResult above -
        # never derived from or fed back into the deterministic engine's own
        # output). None/empty here means "private draft, not yet submitted to
        # any official" - see backend/api/official.py, which refuses to serve
        # any revision still in this state.
        "reviewStatus": None, "submittedAt": None, "officialDecision": None, "decisionHistory": [],
    }
    # Persist the actual uploaded image against this specific revision (own
    # GridFS bucket, same mechanism /api/scans already uses for the official
    # flow) so the report page can later render the real photo instead of
    # generic placeholder artwork - and so an older revision keeps its own
    # image forever instead of only ever being able to show the latest one.
    image_id = GridFS(db, collection=SELF_CHECK_IMAGES_COLLECTION).put(
        original_contents,
        filename=result.image,
        content_type=file.content_type,
        revisionId=revision["revisionId"],
        manufacturerId=user["_id"],
    )
    revision["sourceImageId"] = str(image_id)
    db.productRevisions.insert_one(revision)
    # The product-portfolio summary (manufacturer-dashboard.html, /api/auth/
    # dashboard) still speaks the pre-existing simple "compliant" /
    # "needs_fix" vocabulary for latestStatus - preserved as-is here so those
    # unrelated pages keep working unchanged; the full real engine detail
    # (including the NEEDS_MANUAL_VERIFICATION / NOT_APPLICABLE states) lives
    # on the revision's own complianceResult above, which is what the report
    # page renders.
    latest_status = "compliant" if compliance.overall_status == "COMPLIANT" else "needs_fix"
    db.products.update_one({"productId": product["productId"], "manufacturerId": user["_id"]}, {"$set": {"latestStatus": latest_status, "latestScore": compliance.compliance_score, "updatedAt": now}})
    db.history.insert_one({"userId": user["_id"], "manufacturerId": user["_id"], "actionType": "self_check_performed", "title": "Self-check completed", "description": f"Revision {version} for {resolved_product_name}", "documentId": revision["revisionId"], "createdAt": now})
    return {"success": True, "revision": public_revision(revision)}


@router.post("/revisions/{revision_id}/submit")
def submit_revision_for_review(revision_id: str, user: dict[str, Any] = Depends(manufacturer_user)):
    """Explicitly hand one owned revision to the official review queue.

    Self-checks are a private iteration tool by design (a manufacturer may
    run several before anything is fit to show an inspector) - so submission
    is a deliberate, separate action, never automatic on self-check creation.
    """
    db = get_database()
    revision = db.productRevisions.find_one({"revisionId": revision_id, "manufacturerId": user["_id"]})
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    if revision.get("reviewStatus") in ("PENDING_OFFICIAL_REVIEW", "UNDER_OFFICIAL_REVIEW"):
        raise HTTPException(status_code=409, detail="Already submitted and awaiting official review")
    now = utcnow()
    db.productRevisions.update_one({"revisionId": revision_id}, {"$set": {"reviewStatus": "PENDING_OFFICIAL_REVIEW", "submittedAt": now}})
    db.history.insert_one({
        "userId": user["_id"], "manufacturerId": user["_id"], "actionType": "revision_submitted_for_review",
        "title": "Submitted for official review", "description": f"Revision {revision.get('version')} ({revision.get('imageReference')})",
        "documentId": revision_id, "createdAt": now,
    })
    revision.update({"reviewStatus": "PENDING_OFFICIAL_REVIEW", "submittedAt": now})
    return {"success": True, "revision": public_revision(revision)}


@router.get("/revisions")
def list_revisions(user: dict[str, Any] = Depends(manufacturer_user)):
    revisions = get_database().productRevisions.find({"manufacturerId": user["_id"]}).sort("createdAt", -1)
    return {"success": True, "items": [public_revision(revision) for revision in revisions]}


@router.get("/revisions/{revision_id}/image")
def get_self_check_image(revision_id: str, user: dict[str, Any] = Depends(manufacturer_user)):
    """Stream the exact image the manufacturer uploaded for one owned revision."""
    db = get_database()
    revision = db.productRevisions.find_one({"revisionId": revision_id, "manufacturerId": user["_id"]}, {"sourceImageId": 1})
    if revision is None or not revision.get("sourceImageId"):
        raise HTTPException(status_code=404, detail="Source image not found")
    try:
        image = GridFS(db, collection=SELF_CHECK_IMAGES_COLLECTION).get(ObjectId(revision["sourceImageId"]))
    except NoFile as exc:
        raise HTTPException(status_code=404, detail="Source image not found") from exc
    return StreamingResponse(image, media_type=image.content_type or "application/octet-stream")
