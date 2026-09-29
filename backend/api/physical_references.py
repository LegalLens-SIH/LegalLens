from __future__ import annotations

import hmac
import hashlib
import math
import os
from io import BytesIO
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
import cv2
from fastapi import APIRouter, Header, HTTPException, Depends
from gridfs import GridFS
from PIL import Image
import numpy as np
from pydantic import BaseModel, Field

from backend.api.auth import official_user
from backend.database import get_database
from backend.services.audit import append_audit_event
from backend.services.evidence_package import build_evidence_package, record_evidence_package
from backend.services.evidence_package import build_evidence_package, record_evidence_package
from backend.services.physical_reference_registry import PhysicalReferenceRegistration, calibrate_registered_dimensions, propose_reference_geometry, reference_record

router = APIRouter(prefix="/api/calibration-references", tags=["physical-calibration"])


class ReferenceCalibrationRequest(BaseModel):
    source_image_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    geometry: dict[str, Any]
    pixel_uncertainty: dict[str, float]
    geometry_reviewed: bool = False
    partial_or_distorted: bool = False


def _geometry_measurements(
    reference: dict[str, Any],
    geometry: dict[str, Any],
    *,
    image_dimensions: tuple[int, int],
) -> dict[str, float]:
    image_width, image_height = image_dimensions
    kind = reference.get("geometry_kind")
    if kind in {"rectangle", "aruco_square"}:
        polygon = geometry.get("polygon")
        if not isinstance(polygon, list) or len(polygon) != 4:
            raise HTTPException(status_code=422, detail="Rectangular reference geometry requires four reviewed corner points")
        try:
            points = [(float(point[0]), float(point[1])) for point in polygon]
        except (TypeError, ValueError, IndexError) as exc:
            raise HTTPException(status_code=422, detail="Reference corner geometry is invalid") from exc
        if any(not math.isfinite(value) for point in points for value in point):
            raise HTTPException(status_code=422, detail="Reference geometry contains non-finite values")
        if any(x < 0 or x > image_width or y < 0 or y > image_height for x, y in points):
            raise HTTPException(status_code=422, detail="Reference geometry extends outside the source image")
        cross_products = []
        for index in range(4):
            origin, first, second = points[index], points[(index + 1) % 4], points[(index + 2) % 4]
            cross_products.append((first[0] - origin[0]) * (second[1] - first[1]) - (first[1] - origin[1]) * (second[0] - first[0]))
        if any(abs(value) < 1e-6 for value in cross_products) or not (all(value > 0 for value in cross_products) or all(value < 0 for value in cross_products)):
            raise HTTPException(status_code=422, detail="Reference polygon is degenerate or self-intersecting")
        edges = [math.hypot(points[(index + 1) % 4][0] - points[index][0], points[(index + 1) % 4][1] - points[index][1]) for index in range(4)]
        if min(edges) <= 0:
            raise HTTPException(status_code=422, detail="Reference geometry is degenerate")
        if kind == "aruco_square":
            return {"side": sum(edges) / len(edges)}
        return {"width": (edges[0] + edges[2]) / 2, "height": (edges[1] + edges[3]) / 2}
    if kind == "circle":
        circle = geometry.get("circle")
        if not isinstance(circle, dict):
            raise HTTPException(status_code=422, detail="Circular reference geometry requires center and radius")
        try:
            center = [float(value) for value in circle["center"]]
            radius = float(circle["radius_px"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Circular reference geometry is invalid") from exc
        if (len(center) != 2 or not all(math.isfinite(value) for value in (*center, radius)) or radius <= 0
            or center[0] - radius < 0 or center[0] + radius > image_width
            or center[1] - radius < 0 or center[1] + radius > image_height):
            raise HTTPException(status_code=422, detail="Circular reference geometry is invalid")
        return {"diameter": radius * 2}
    raise HTTPException(status_code=409, detail="Reference registry geometry kind is unsupported")


def _authorize_registry_key(provision_key: str | None) -> str:
    expected = os.getenv("OFFICIAL_PROVISION_KEY", "").strip()
    if not expected or not provision_key or not hmac.compare_digest(expected, provision_key):
        raise HTTPException(status_code=403, detail="Reference registry administration is not authorized")
    return expected


@router.post("")
def register_physical_reference(
    registration: PhysicalReferenceRegistration,
    x_reference_registry_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize_registry_key(x_reference_registry_key)
    database = get_database()
    if database.physical_reference_registry.find_one({"reference_id": registration.reference_id}):
        raise HTTPException(status_code=409, detail="reference_id is already registered")
    record = reference_record(registration, registered_by="server_provision_key")
    result = database.physical_reference_registry.insert_one(record)
    event = append_audit_event(
        database,
        event_type="physical_reference_registry",
        entity_type="physical_reference",
        entity_id=registration.reference_id,
        actor_role="system",
        action="physical_reference_registered",
        details={
            "reference_type": record["reference_type"],
            "status": record["status"],
            "calibration_eligible": record["calibration_eligible"],
            "source_evidence_sha256": record["source_evidence_sha256"],
            "verification_document_sha256": record.get("verification_document_sha256"),
        },
    )
    record["id"] = str(result.inserted_id)
    record["audit_event_hash"] = event["eventHash"]
    return {"success": True, "reference": record}


@router.get("")
def list_physical_references(x_reference_registry_key: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize_registry_key(x_reference_registry_key)
    records = list(get_database().physical_reference_registry.find({}, {"_id": 1, "reference_id": 1, "reference_type": 1, "object_description": 1, "geometry_kind": 1, "nominal_dimension_mm": 1, "measured_dimension_mm": 1, "measurement_method": 1, "measurement_instrument": 1, "instrument_calibration_reference": 1, "measurement_date": 1, "uncertainty_mm": 1, "verifier": 1, "verification_document_sha256": 1, "source_authority": 1, "source_document": 1, "source_version_date": 1, "source_evidence_sha256": 1, "status": 1, "calibration_eligible": 1, "registered_date": 1, "valid_until": 1}))
    output = []
    for record in records:
        record["id"] = str(record.pop("_id"))
        for key in ("measurement_date", "valid_until"):
            if record.get(key) is not None and hasattr(record[key], "isoformat"):
                record[key] = record[key].isoformat()
        output.append(record)
    return {"success": True, "references": output}


@router.post("/{reference_id}/reject")
def reject_physical_reference(
    reference_id: str,
    reason: str,
    x_reference_registry_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize_registry_key(x_reference_registry_key)
    if not reason.strip() or len(reason) > 1000:
        raise HTTPException(status_code=422, detail="A rejection reason of 1-1000 characters is required")
    database = get_database()
    record = database.physical_reference_registry.find_one({"reference_id": reference_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Physical reference not found")
    database.physical_reference_registry.update_one(
        {"_id": record["_id"]},
        {"$set": {"status": "REJECTED", "calibration_eligible": False, "rejection_reason": reason.strip()}},
    )
    event = append_audit_event(
        database,
        event_type="physical_reference_registry",
        entity_type="physical_reference",
        entity_id=reference_id,
        actor_role="system",
        action="physical_reference_rejected",
        details={"reason": reason.strip(), "calibration_eligible": False},
    )
    return {"success": True, "reference_id": reference_id, "status": "REJECTED", "audit_event_hash": event["eventHash"]}


@router.post("/detect/{scan_id}/{reference_id}")
def detect_registered_reference_geometry(
    scan_id: str,
    reference_id: str,
    source_image_sha256: str,
    user: dict[str, Any] = Depends(official_user),
) -> dict[str, Any]:
    if len(source_image_sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in source_image_sha256):
        raise HTTPException(status_code=422, detail="source_image_sha256 must be a SHA-256 digest")
    database = get_database()
    scan = database.scans.find_one({"id": scan_id, "userId": user["_id"]})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    reference = database.physical_reference_registry.find_one({"reference_id": reference_id})
    if reference is None:
        raise HTTPException(status_code=404, detail="Physical reference is not registered")
    source_image_id = scan.get("sourceImageId") if str(scan.get("sourceImageSha256") or "").lower() == source_image_sha256.lower() else None
    for item in scan.get("sourceImages", []):
        if isinstance(item, dict) and str(item.get("sha256") or "").lower() == source_image_sha256.lower():
            source_image_id = item.get("id")
            break
    if not source_image_id:
        raise HTTPException(status_code=409, detail="Source image hash does not belong to this scan")
    try:
        source_bytes = GridFS(database, collection="scan_images").get(ObjectId(source_image_id)).read(15 * 1024 * 1024 + 1)
        if not source_bytes or len(source_bytes) > 15 * 1024 * 1024 or hashlib.sha256(source_bytes).hexdigest() != source_image_sha256.lower():
            raise ValueError("source bytes differ from requested digest")
        with Image.open(BytesIO(source_bytes)) as source_image:
            source_image.verify()
        with Image.open(BytesIO(source_bytes)) as source_image:
            image_dimensions = source_image.size
        decoded = cv2.imdecode(np.frombuffer(source_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("image decoder rejected source bytes")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Source image could not be verified for geometry detection") from exc
    result = propose_reference_geometry(decoded, reference)
    result["source_image_sha256"] = source_image_sha256.lower()
    result["source_image_dimensions"] = {"width": image_dimensions[0], "height": image_dimensions[1]}
    event = append_audit_event(
        database,
        event_type="physical_reference_geometry_proposal",
        entity_type="scan",
        entity_id=scan_id,
        user_id=user["_id"],
        actor_role=user.get("role"),
        action=result["status"],
        source_image_sha256=source_image_sha256.lower(),
        details={"reference_id": reference_id, "proposal_count": len(result["proposals"]), "geometry_reviewed": False},
    )
    result["audit_event_hash"] = event["eventHash"]
    reference_summary = {
        key: reference.get(key)
        for key in (
            "reference_id", "reference_type", "geometry_kind", "object_description",
            "nominal_dimension_mm", "measured_dimension_mm", "uncertainty_mm",
            "source_authority", "source_document", "source_version_date", "status",
            "calibration_eligible", "source_evidence_sha256",
        )
    }
    package = build_evidence_package(
        evidence_id=f"{scan_id}:reference-proposals:{reference_id}:{source_image_sha256[:12]}",
        raw_artifacts={
            "reference_geometry_proposals": {
                "artifact_type": "physical_reference_geometry_proposals",
                "value": result,
                "source": "opencv_candidate_detection_unreviewed",
                "metadata": {"reference": reference_summary},
                "derived_from": [scan.get("evidencePackageId")] if scan.get("evidencePackageId") else [],
            },
        },
        audit_refs=[event["eventHash"]],
    )
    package_audit = record_evidence_package(database, package, entity_id=scan_id, entity_type="scan")
    result["evidence_package_id"] = package.package_id
    result["evidence_merkle_root"] = package.merkle_root
    result["evidence_package_audit_hash"] = package_audit["eventHash"]
    database.scans.update_one(
        {"_id": scan["_id"]},
        {
            "$push": {"physicalReferenceProposals": {"$each": [result], "$slice": -10}},
            "$addToSet": {"supplementalEvidencePackageIds": package.package_id},
        },
    )
    return result


@router.post("/calibrate/{scan_id}/{reference_id}")
def calibrate_scan_with_registered_reference(
    scan_id: str,
    reference_id: str,
    request: ReferenceCalibrationRequest,
    user: dict[str, Any] = Depends(official_user),
) -> dict[str, Any]:
    database = get_database()
    scan = database.scans.find_one({"id": scan_id, "userId": user["_id"]})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    reference = database.physical_reference_registry.find_one({"reference_id": reference_id})
    if reference is None:
        raise HTTPException(status_code=404, detail="Physical reference is not registered")
    if reference.get("geometry_kind") == "aruco_square" and request.geometry.get("marker_id") != reference.get("marker_id"):
        raise HTTPException(status_code=409, detail="Detected ArUco marker ID does not match the registered physical marker")
    valid_until = reference.get("valid_until")
    if isinstance(valid_until, str):
        valid_until = datetime.fromisoformat(valid_until).date()
    elif isinstance(valid_until, datetime):
        valid_until = valid_until.date()
    if valid_until and valid_until < datetime.now(timezone.utc).date():
        database.physical_reference_registry.update_one({"_id": reference["_id"]}, {"$set": {"status": "EXPIRED", "calibration_eligible": False}})
        reference.update({"status": "EXPIRED", "calibration_eligible": False})
    source_image_id = None
    if str(scan.get("sourceImageSha256") or "").lower() == request.source_image_sha256.lower():
        source_image_id = scan.get("sourceImageId")
    for item in scan.get("sourceImages", []):
        if isinstance(item, dict) and str(item.get("sha256") or "").lower() == request.source_image_sha256.lower():
            source_image_id = item.get("id")
            break
    if not source_image_id:
        raise HTTPException(status_code=409, detail="Calibration source hash does not belong to this scan")
    try:
        source_file = GridFS(database, collection="scan_images").get(ObjectId(source_image_id))
        source_bytes = source_file.read(15 * 1024 * 1024 + 1)
        if not source_bytes or len(source_bytes) > 15 * 1024 * 1024 or hashlib.sha256(source_bytes).hexdigest() != request.source_image_sha256.lower():
            raise ValueError("source digest mismatch")
        with Image.open(BytesIO(source_bytes)) as source_image:
            source_image.verify()
        with Image.open(BytesIO(source_bytes)) as source_image:
            image_dimensions = source_image.size
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Calibration source image could not be verified") from exc
    try:
        observed = _geometry_measurements(reference, request.geometry, image_dimensions=image_dimensions)
    except HTTPException:
        raise
    calibration = calibrate_registered_dimensions(
        reference,
        observed_dimensions_px=observed,
        pixel_uncertainty=request.pixel_uncertainty,
        source_image_sha256=request.source_image_sha256.lower(),
        geometry_source="official_manual_geometry_review",
        geometry_reviewed=request.geometry_reviewed,
        partial_or_distorted=request.partial_or_distorted,
    )
    calibration["geometry"] = request.geometry
    calibration["source_image_dimensions"] = {"width": image_dimensions[0], "height": image_dimensions[1]}
    calibration["recorded_at"] = datetime.now(timezone.utc).isoformat()
    calibration["recorded_by"] = str(user["_id"])
    event = append_audit_event(
        database,
        event_type="physical_reference_calibration",
        entity_type="scan",
        entity_id=scan_id,
        user_id=user["_id"],
        actor_role=user.get("role"),
        action=calibration["status"],
        source_image_sha256=request.source_image_sha256.lower(),
        details={"calibration": calibration, "used_for_compliance": False},
    )
    package = build_evidence_package(
        evidence_id=f"{scan_id}:physical-reference:{reference_id}:{event['eventHash'][:12]}",
        raw_artifacts={
            "physical_reference_calibration": {
                "artifact_type": "physical_reference_calibration",
                "value": calibration,
                "source": "registered_physical_reference_and_official_review",
                "derived_from": [scan.get("evidencePackageId")] if scan.get("evidencePackageId") else [],
            },
        },
        audit_refs=[event["eventHash"]],
    )
    package_audit = record_evidence_package(database, package, entity_id=scan_id, entity_type="scan")
    database.scans.update_one({"_id": scan["_id"]}, {"$push": {"physicalReferenceCalibrations": calibration}, "$addToSet": {"supplementalEvidencePackageIds": package.package_id}})
    return {
        "success": True,
        "calibration": calibration,
        "evidence_package_id": package.package_id,
        "evidence_merkle_root": package.merkle_root,
        "audit_event_hash": event["eventHash"],
        "package_audit_hash": package_audit["eventHash"],
    }
