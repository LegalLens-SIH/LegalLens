"""
HTTP layer for OCR. This module owns request validation, temp-file handling,
and response shaping ONLY - all actual OCR inference is delegated to the
existing, already-tested PaddleOCRService. No OCR/PaddleOCR logic is
duplicated here.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile

from backend.database import get_database
from backend.api.auth import official_user
from backend.ocr.gemini_service import (
    GEMINI_API_KEY,
    GEMINI_ENABLED,
    GeminiService,
    apply_suggestions_to_ocr_result,
    find_ambiguous_fields,
)
from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
from backend.ocr.preprocessing import PREPROCESSING_ENABLED, PreprocessOptions
from backend.ocr.schemas import OCRError, OCRResult
from backend.ocr.yolo_service import YOLO_ENABLED, YOLOService
from backend.services.compliance_engine import ComplianceEngine, normalize_ocr_result
from backend.services.structured_extraction import build_structured_extraction

logger = logging.getLogger("legallense.api.ocr")

router = APIRouter()

# One shared service instance: PaddleOCRService caches its underlying
# PaddleOCR engine internally, so the (slow) model load happens once per
# process, not once per request.
_service = PaddleOCRService()
_compliance_engine = ComplianceEngine()

# Same one-instance-per-process pattern as _service above: YOLOService caches
# the loaded YOLO26 model at the class level, so it is loaded once and reused
# for every request. Disabled entirely via YOLO_ENABLED=false, in which case
# OCR runs exactly as it did before YOLO26 was introduced.
_yolo_service = YOLOService() if YOLO_ENABLED else None

# Accuracy Fix #2 A/B flag - OCR_PREPROCESSING_ENABLED=false (default)
# preserves today's exact baseline behavior (no preprocess_options passed to
# PaddleOCRService.run() at all, same as before this flag existed). When
# true, applies denoise + CLAHE contrast enhancement ONLY - resize is
# deliberately EXCLUDED from this bundle despite being part of this
# project's own documented "preprocessing enabled" convention (README.md,
# test_ocr.py's --preprocess flag): 7 of the 10 real benchmark fixtures
# exceed the 2000px resize threshold and would be downscaled, and resizing
# down is a SPEED optimization (see resize_if_large's own docstring/
# README: "keeps inference fast on huge phone photos"), not an accuracy
# one - it can only ever reduce the pixel density available for the
# dense/small print this project's OCR failures are already concentrated
# in. Grayscale is also excluded, matching this project's own existing
# default (never part of the --preprocess bundle either).
_preprocess_options = PreprocessOptions(denoise=True, enhance_contrast=True) if PREPROCESSING_ENABLED else None

# Gemini is an AI/vision FALLBACK for ambiguous/low-confidence fields only -
# see backend/ocr/gemini_service.py. Disabled by default (GEMINI_ENABLED) and
# a no-op without an API key, in which case every scan behaves exactly as it
# did before Gemini was introduced. Constructing GeminiService is cheap (no
# network call happens until a request is actually made), same lazy pattern
# as _yolo_service above.
_gemini_service = GeminiService() if GEMINI_ENABLED else None

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB safety cap for local dev

# Uploads are written into the OS temp dir under their own subfolder so we
# never write into (or serve from) the project tree, and never trust a
# client-supplied path.
_UPLOAD_TMP_DIR = Path(tempfile.gettempdir()) / "legallense_ocr_uploads"
_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)


def _error_response(status_code: int, image: str, code: str, message: str) -> JSONResponse:
    result = OCRResult(success=False, image=image, error=OCRError(code=code, message=message))
    return JSONResponse(status_code=status_code, content=result.model_dump())


# There is no frontend control for retail/wholesale/instrument-audit
# selection anywhere in this project (new-compliance-scan.html hardcodes
# category to "General"; new-self-check.html's category dropdown is a
# commodity-TYPE selector - Food & Beverage/Cosmetics/Electronics/Home
# Care - a different axis entirely) - so this stays API-reachable-only via
# the `category` form field, matching the pre-existing pos_hardware_audit
# branch this wholesale branch was added alongside. Extracted to a plain
# function (rather than left inline in create_scan) purely so the profile-
# selection decision itself is directly unit-testable without needing to
# exercise the full multipart upload endpoint, which has no existing test
# coverage of its own to extend.
def _select_validation_profile(category: str) -> tuple[str, bool]:
    """Return (validation_profile, is_wholesale). is_wholesale tells the
    caller whether ocr_dict["package_type"] must be forced to "wholesale" -
    normalize_ocr_result (compliance_engine.py) defaults every OCR result to
    "retail", and LMPC-R24-WHOLESALE-DECLARATIONS is gated on
    package_type == "wholesale" regardless of which profile is evaluated."""
    normalized_category = category.strip().lower()
    if normalized_category in {"weighing_instrument", "instrument", "pos hardware"}:
        return "pos_hardware_audit", False
    if normalized_category in {"wholesale", "wholesale_package", "wholesale package"}:
        return "wholesale_package", True
    return "e_commerce_product_listing", False


def _scan_document(result: OCRResult, product_name: str, category: str, manufacturer: str, file_name: str) -> dict[str, Any]:
    return {
        "id": f"SCN-{uuid.uuid4().hex[:12].upper()}",
        "productName": product_name or file_name,
        "category": category or "General",
        "manufacturer": manufacturer or "Not specified",
        "date": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "score": None,
        "caseId": None,
        "fileName": file_name,
        "findings": [],
        "ocr": result.model_dump(),
    }


def _public_scan(scan: dict[str, Any]) -> dict[str, Any]:
    """Remove Mongo internals and make ownership IDs JSON serializable."""
    public = {key: value for key, value in scan.items() if key != "_id"}
    if public.get("userId") is not None:
        public["userId"] = str(public["userId"])
    return public


@router.post("/api/ocr", response_model=OCRResult)
async def run_ocr(file: Optional[UploadFile] = File(None)):
    """Accept one image (JPG/JPEG/PNG/WEBP), run it through PaddleOCRService, return OCRResult JSON."""

    if file is None or not file.filename:
        logger.warning("OCR request received with no file attached")
        return _error_response(400, "", "missing_file", "No file was uploaded")

    # Path(...).name strips any directory component the client might send
    # (e.g. "../../etc/passwd.jpg") - only the base filename is ever used,
    # and only for display; it is never used to build a filesystem path.
    original_name = Path(file.filename).name
    suffix = Path(original_name).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        logger.warning("Rejected unsupported file type '%s' for %s", suffix, original_name)
        return _error_response(
            400, original_name, "unsupported_file_type",
            f"Unsupported file type '{suffix}'. Supported: {sorted(ALLOWED_EXTENSIONS)}",
        )

    contents = await file.read()
    if not contents:
        logger.warning("Rejected empty upload for %s", original_name)
        return _error_response(400, original_name, "empty_upload", "Uploaded file is empty")

    if len(contents) > MAX_UPLOAD_BYTES:
        logger.warning("Rejected oversized upload for %s (%d bytes)", original_name, len(contents))
        return _error_response(
            400, original_name, "file_too_large",
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )

    # Random server-generated filename - never derived from client input.
    tmp_path = _UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"

    try:
        tmp_path.write_bytes(contents)
        logger.info("Saved upload '%s' (%d bytes) to temp file for OCR", original_name, len(contents))

        result = _service.run(str(tmp_path), yolo_service=_yolo_service, preprocess_options=_preprocess_options)
        # Swap the temp filename back out for the original, client-facing name,
        # and scrub the server-side temp path out of any error message so no
        # internal filesystem detail reaches the browser.
        result.image = original_name
        result.image_path = None
        if result.error is not None:
            result.error.message = result.error.message.replace(str(tmp_path), original_name)

        status_code = 200 if result.success else 422
        logger.info(
            "OCR request for '%s' complete: success=%s detections=%d",
            original_name, result.success, result.detection_count,
        )
        return JSONResponse(status_code=status_code, content=result.model_dump())

    except OCRInitializationError as exc:
        logger.exception("PaddleOCR engine failed to initialize")
        return _error_response(503, original_name, "engine_init_failed", "OCR engine failed to initialize")

    except Exception:
        logger.exception("Unexpected error while processing OCR request for %s", original_name)
        return _error_response(500, original_name, "internal_error", "Unexpected server error during OCR processing")

    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file %s", tmp_path, exc_info=True)


@router.post("/api/scans")
async def create_scan(
    file: Optional[UploadFile] = File(None),
    product_name: str = Form(""),
    category: str = Form("General"),
    manufacturer: str = Form("Not specified"),
    user: dict[str, Any] = Depends(official_user),
):
    """Run OCR and persist the original scan record in MongoDB."""
    original_contents = await file.read() if file is not None else b""
    if file is not None:
        await file.seek(0)
    response = await run_ocr(file)
    if response.status_code != 200:
        return response

    result = OCRResult.model_validate(response.body and __import__("json").loads(response.body))
    if not result.success:
        return response

    ocr_dict = result.model_dump()
    # Computed once and reused for both the Gemini ambiguous-field check and
    # structured extraction below (see find_ambiguous_fields' and
    # build_structured_extraction's `normalized` parameter) - a cheap, pure,
    # CPU-only function (no OCR/model re-run), but sharing it avoids even
    # that redundant recomputation in the common case where Gemini doesn't
    # end up changing anything.
    normalized = normalize_ocr_result(ocr_dict)

    gemini_fields_used: list[str] = []
    suggestions: list = []
    if _gemini_service is not None and GEMINI_API_KEY:
        try:
            ambiguous_fields = find_ambiguous_fields(ocr_dict, normalized=normalized)
        except Exception:
            logger.exception("Gemini ambiguous-field detection failed for %s; continuing without Gemini", result.image)
            ambiguous_fields = []
        if ambiguous_fields:
            try:
                suggestions = await _gemini_service.suggest_fields(
                    original_contents, ambiguous_fields, ocr_dict.get("regions"),
                )
            except Exception:
                # suggest_fields already catches and logs its own failures
                # (timeout, API error, quota, missing key) and returns []
                # rather than raising - this is defense in depth only, so a
                # truly unexpected error here still can't crash the scan.
                logger.exception("Gemini fallback raised unexpectedly for %s; continuing without Gemini", result.image)
                suggestions = []
            if suggestions:
                try:
                    ocr_dict = apply_suggestions_to_ocr_result(ocr_dict, suggestions)
                    normalized = normalize_ocr_result(ocr_dict)  # re-derive: values just changed
                    gemini_fields_used = [s.field for s in suggestions]
                    logger.info("Gemini fallback supplied %d field(s) for %s: %s", len(suggestions), result.image, gemini_fields_used)
                except Exception:
                    logger.exception("Failed to merge Gemini suggestions for %s; continuing with existing OCR result", result.image)

    try:
        structured_extraction = build_structured_extraction(ocr_dict, normalized=normalized, gemini_suggestions=suggestions or None)
    except Exception:
        logger.exception("Structured extraction failed for %s; scan continues without it", result.image)
        structured_extraction = None

    validation_profile, is_wholesale = _select_validation_profile(category)
    if is_wholesale:
        ocr_dict["package_type"] = "wholesale"
    compliance = _compliance_engine.evaluate(ocr_dict, validation_profile)
    scan = _scan_document(result, product_name.strip(), category.strip(), manufacturer.strip(), result.image)
    if gemini_fields_used:
        # Provenance only - never fed back into ComplianceEngine/NormalizedField,
        # which is entirely unaware this key exists; purely an audit trail for
        # the persisted scan record, same spirit as the "audit" block
        # ComplianceResult already attaches.
        scan["geminiFieldsUsed"] = gemini_fields_used
    if structured_extraction is not None:
        # New, additive, purely informational (see backend/models/
        # extraction.py) - never consulted by ComplianceEngine.evaluate()
        # above, which already ran on ocr_dict directly and is completely
        # unaware this key exists. Existing frontend code reads specific
        # known scan keys (compliance, detections, ...) and ignores unknown
        # ones, so this is backward compatible with no frontend changes.
        scan["structuredExtraction"] = structured_extraction.model_dump()
    status_map = {
        "COMPLIANT": "compliant",
        "NON_COMPLIANT": "flagged",
        "PARTIALLY_COMPLIANT": "partial",
        "NEEDS_MANUAL_VERIFICATION": "manual_review",
        # REVIEW_REQUIRED (compliance_engine.py's _aggregate_checks): no
        # confirmed violation, but one or more checks are low-confidence and
        # need manual review - reuses the existing "manual_review" scan
        # status bucket rather than introducing a new one, since that's
        # already exactly this concept in the scan-list UI.
        "REVIEW_REQUIRED": "manual_review",
        "NOT_APPLICABLE": "not_applicable",
    }
    scan.update({
        "userId": user["_id"],
        "success": True,
        "image": result.image,
        "full_text": result.full_text,
        "detections": [d.model_dump() for d in result.detections],
        "detection_count": result.detection_count,
        "preprocessing_applied": result.preprocessing_applied,
        "error": None,
        "compliance": compliance.model_dump(),
        "status": status_map[compliance.overall_status],
        "score": compliance.compliance_score,
    })
    database = get_database()
    image_id = GridFS(database, collection="scan_images").put(
        original_contents,
        filename=result.image,
        content_type=file.content_type if file is not None else None,
        scan_id=scan["id"],
        user_id=user["_id"],
    )
    scan["sourceImageId"] = str(image_id)
    scan["sourceImageUrl"] = f"/api/scans/{scan['id']}/image"
    database.scans.insert_one(scan)
    database.history.insert_one({
        "userId": user["_id"],
        "actionType": "scan_uploaded",
        "title": "Product label uploaded",
        "description": f"OCR scan created for {scan['productName']}.",
        "documentId": scan["id"],
        "metadata": {"fileName": result.image, "category": scan["category"]},
        "createdAt": datetime.now(timezone.utc),
    })
    logger.info("Persisted scan %s and source image %s to MongoDB", scan["id"], image_id)
    return _public_scan(scan)


@router.get("/api/scans")
def list_scans(user: dict[str, Any] = Depends(official_user)):
    """Return persisted scans newest first."""
    scans = get_database().scans.find({"userId": user["_id"]}, {"_id": 0}).sort("date", -1)
    return JSONResponse(
        content=[_public_scan(scan) for scan in scans],
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/api/scans/{scan_id}")
def get_scan(scan_id: str, user: dict[str, Any] = Depends(official_user)):
    """Return one persisted scan by its public ID."""
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"_id": 0})
    if scan is None:
        return JSONResponse(status_code=404, content={"detail": "Scan not found"})
    return JSONResponse(
        content=_public_scan(scan),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/api/scans/{scan_id}/image")
def get_scan_image(scan_id: str, user: dict[str, Any] = Depends(official_user)):
    """Stream the original label photo for an owned scan."""
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"sourceImageId": 1})
    if scan is None or not scan.get("sourceImageId"):
        raise HTTPException(status_code=404, detail="Source image not found")

    try:
        image = GridFS(get_database(), collection="scan_images").get(ObjectId(scan["sourceImageId"]))
    except NoFile as exc:
        raise HTTPException(status_code=404, detail="Source image not found") from exc

    return StreamingResponse(image, media_type=image.content_type or "application/octet-stream")


@router.get("/api/cases")
def list_cases(user: dict[str, Any] = Depends(official_user)):
    """Return persisted cases newest first."""
    cases = get_database().cases.find({"userId": user["_id"]}, {"_id": 0}).sort("date", -1)
    return [{**case, "userId": str(case["userId"])} if case.get("userId") is not None else case for case in cases]
