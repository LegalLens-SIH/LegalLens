"""
HTTP layer for OCR. This module owns request validation, temp-file handling,
and response shaping ONLY - all actual OCR inference is delegated to the
existing, already-tested PaddleOCRService. No OCR/PaddleOCR logic is
duplicated here.
"""

from __future__ import annotations

import logging
import asyncio
from contextvars import ContextVar
from io import BytesIO
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from starlette.datastructures import Headers

from backend.database import get_database
from backend.api.auth import official_user
from backend.services.authenticity_service import build_authenticity_evidence
from backend.services.image_integrity import compute_image_sha256
from backend.ocr.gemini_service import (
    GEMINI_API_KEY,
    GEMINI_ENABLED,
    GeminiService,
    apply_suggestions_to_ocr_result,
    find_ambiguous_fields,
)
from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
from backend.ocr.preprocessing import MAX_SIDE_DEFAULT, PREPROCESSING_ENABLED, PreprocessOptions
from backend.ocr.schemas import OCRError, OCRResult
from backend.ocr.yolo_service import YOLO_ENABLED, YOLOService
from backend.services.compliance_engine import ComplianceEngine, normalize_ocr_result
from backend.services.ocr_modes import DEFAULT_OCR_MODE, InvalidOCRModeError, forced_preprocess_options_for_mode, parse_ocr_mode
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

# Phase 2B production tuning (2026-09-17): resize (downscale to
# PreprocessOptions.max_side=2000px before OCR) is now the UNCONDITIONAL
# baseline for every OCR call, regardless of ocr_mode - benchmarked across
# this project's real 10-fixture image set at ~3.3x faster (258s -> 79s
# mean end-to-end per image; see training/benchmark_results/phase2_* and
# backend/ocr/README.md's "Production OCR performance tuning" section for
# the full methodology, per-image numbers, and the one confirmed accuracy
# trade-off: a glare-damaged fixture (Ferrero_back.jpg) lost a previously-
# correct country_of_origin read at this resize ceiling. Per this
# project's existing "OCR non-detection is not confirmed physical
# absence" principle (see compliance_engine.py), a field lost to resize
# still only ever produces NEEDS_MANUAL_VERIFICATION, never a fabricated
# FAIL - it is a manual-review case, not treated as a confirmed absence.
#
# Previously this was an opt-in-only denoise+contrast bundle gated behind
# OCR_PREPROCESSING_ENABLED (an unrelated, separate toggle - see below);
# resize was deliberately excluded from it for exactly the accuracy
# reason above. That reasoning still holds for THIS specific trade-off,
# but real end-to-end benchmarking (not just the a-priori concern) showed
# the net effect across the real fixture set nets out positive (one field
# regressed, one improved, zero new FALSE_PASS) at a large enough speed
# win to justify making it the default. denoise/enhance_contrast remain
# EXACTLY as before: still opt-in only via OCR_PREPROCESSING_ENABLED,
# completely unrelated to and unaffected by this change.
_preprocess_options = PreprocessOptions(
    resize=True,
    max_side=MAX_SIDE_DEFAULT,
    denoise=PREPROCESSING_ENABLED,
    enhance_contrast=PREPROCESSING_ENABLED,
)

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

_analysis_jobs: dict[str, dict[str, Any]] = {}
_active_progress_job: ContextVar[str | None] = ContextVar("active_progress_job", default=None)

ANALYSIS_STAGES = {
    "INITIALIZING": (10, "Initializing analysis"),
    "IMAGE_UPLOADED": (20, "Image uploaded successfully"),
    "IMAGE_PREPROCESSING": (34, "Preprocessing image"),
    "OBJECT_DETECTION": (50, "Detecting product label"),
    "OCR_PROCESSING": (65, "Extracting text using OCR"),
    "TEXT_STRUCTURING": (78, "Processing extracted information"),
    "DECLARATION_IDENTIFICATION": (88, "Identifying product declarations"),
    "RULE_VALIDATION": (95, "Checking Legal Metrology rules"),
    "REPORT_GENERATION": (98, "Generating compliance results"),
    "COMPLETED": (100, "Analysis completed"),
}

MAX_ANALYSIS_IMAGES = 8


def _report_progress(stage: str, *, error: str | None = None, result: dict[str, Any] | None = None) -> None:
    job_id = _active_progress_job.get()
    if not job_id or job_id not in _analysis_jobs:
        return
    progress, label = ANALYSIS_STAGES.get(stage, (0, stage))
    update = {"stage": stage, "progress": progress, "label": label}
    if error:
        update.update({"status": "failed", "error": error})
    elif result is not None:
        update.update({"status": "completed", "result": result})
    else:
        update["status"] = "running"
    _analysis_jobs[job_id].update(update)


def _report_analysis_update(progress: int, stage: str, label: str, **extra: Any) -> None:
    job_id = _active_progress_job.get()
    if not job_id or job_id not in _analysis_jobs:
        return
    _analysis_jobs[job_id].update({"status": "running", "progress": progress, "stage": stage, "label": label, **extra})


def _deduplicate_detections(results: list[OCRResult]) -> list[Any]:
    """Keep the first high-confidence occurrence of repeated OCR lines."""
    unique: list[Any] = []
    seen: set[str] = set()
    for result in results:
        for detection in result.detections:
            key = " ".join(detection.text.casefold().split())
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(detection)
    return unique


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


async def _run_ocr_core(file: Optional[UploadFile], preprocess_options: Optional[PreprocessOptions]):
    """Shared OCR-execution body for the /api/ocr route and create_scan
    below - extracted ONLY so create_scan can pass a per-request,
    mode-resolved `preprocess_options` (Phase 2B) without changing
    run_ocr's public FastAPI signature/contract (adding a non-Form/File
    parameter there would turn it into an implicit request-body field and
    silently change /api/ocr's existing API). Behavior is byte-for-byte
    identical to what this function's body was before this refactor -
    run_ocr (below) calls it with the exact same module-level
    `_preprocess_options` it always used."""

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

        run_options = {
            "yolo_service": _yolo_service,
            "preprocess_options": preprocess_options,
        }
        if _active_progress_job.get():
            run_options["progress_callback"] = _report_progress
        result = _service.run(str(tmp_path), **run_options)
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


@router.post("/api/ocr", response_model=OCRResult)
async def run_ocr(file: Optional[UploadFile] = File(None)):
    """Accept one image (JPG/JPEG/PNG/WEBP), run it through PaddleOCRService, return OCRResult JSON."""
    return await _run_ocr_core(file, _preprocess_options)


@router.post("/api/scans")
async def create_scan(
    file: Optional[UploadFile] = File(None),
    product_name: str = Form(""),
    category: str = Form("General"),
    manufacturer: str = Form("Not specified"),
    ocr_mode: str = Form(DEFAULT_OCR_MODE),
    user: dict[str, Any] = Depends(official_user),
):
    """Run OCR and persist the original scan record in MongoDB."""
    _report_progress("INITIALIZING")

    try:
        resolved_ocr_mode = parse_ocr_mode(ocr_mode)
    except InvalidOCRModeError as exc:
        return _error_response(
            400,
            file.filename if file is not None else "",
            "invalid_ocr_mode",
            str(exc),
        )

    # Phase 2B: "standard" defers to the EXISTING module-level
    # _preprocess_options (which now unconditionally includes resize as of
    # the 2026-09-17 tuning pass, plus denoise/enhance_contrast only when
    # the pre-existing, unrelated OCR_PREPROCESSING_ENABLED env flag is
    # set) so a caller that never sends ocr_mode - or explicitly sends
    # "standard" - gets that same production baseline. Only high_accuracy
    # forces a specific, benchmarked PreprocessOptions bundle regardless
    # of that env flag ("fast" was retired - see ocr_modes.py).
    effective_preprocess_options = (
        _preprocess_options if resolved_ocr_mode == "standard" else forced_preprocess_options_for_mode(resolved_ocr_mode)
    )

    original_contents = await file.read() if file is not None else b""
    if file is not None:
        await file.seek(0)
    _report_progress("IMAGE_UPLOADED")
    # Authenticity Verification Layer, Section 3 (EXIF only): independent
    # evidence collection, run against the SAME raw upload bytes OCR is
    # about to process - deliberately BEFORE the OCR call below, mirroring
    # the Upload -> [EXIF | OCR] -> ... -> Report architecture this feature
    # specifies (parallel to OCR, not sequenced after it). Never raises
    # (see authenticity_service.extract_exif_evidence's docstring), so no
    # try/except is needed here, and this line can never affect whether
    # OCR/compliance below runs. Its result is attached to `scan` further
    # down, alongside (never consulted by) compliance - see that comment.
    authenticity = build_authenticity_evidence(original_contents) if original_contents else None
    # Phase 1, Requirement 6: hashed from the SAME raw upload bytes, before
    # run_ocr()/preprocessing touches them - the hash corresponds to the
    # exact analyzed upload, never a resized/preprocessed derivative.
    source_image_sha256 = compute_image_sha256(original_contents) if original_contents else None
    response = await _run_ocr_core(file, effective_preprocess_options)
    if response.status_code != 200:
        return response

    result = OCRResult.model_validate(response.body and __import__("json").loads(response.body))
    if not result.success:
        return response

    _report_progress("OCR_PROCESSING")

    ocr_dict = result.model_dump()
    # Computed once and reused for both the Gemini ambiguous-field check and
    # structured extraction below (see find_ambiguous_fields' and
    # build_structured_extraction's `normalized` parameter) - a cheap, pure,
    # CPU-only function (no OCR/model re-run), but sharing it avoids even
    # that redundant recomputation in the common case where Gemini doesn't
    # end up changing anything.
    normalized = normalize_ocr_result(ocr_dict)
    _report_progress("TEXT_STRUCTURING")

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

    _report_progress("DECLARATION_IDENTIFICATION")

    validation_profile, is_wholesale = _select_validation_profile(category)
    if is_wholesale:
        ocr_dict["package_type"] = "wholesale"
    compliance = _compliance_engine.evaluate(ocr_dict, validation_profile)
    _report_progress("RULE_VALIDATION")
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
    if authenticity is not None:
        # Independent evidence only (Authenticity Verification Layer,
        # Section 3 - EXIF) - never consulted by ComplianceEngine.evaluate()
        # above, which already ran on ocr_dict directly and is completely
        # unaware this key exists; never interpreted as "fake"/"genuine"
        # here or anywhere else. Same additive, backward-compatible
        # attachment pattern as structuredExtraction above.
        scan["authenticity"] = authenticity.model_dump()
    if source_image_sha256 is not None:
        # Phase 1, Requirement 6: integrity/provenance evidence only - the
        # exact hash of the analyzed upload, never consulted by
        # ComplianceEngine and never an authenticity/fraud signal.
        scan["sourceImageSha256"] = source_image_sha256
    # Phase 2B: evidence/traceability only - which OCR mode actually
    # produced this scan's OCR result, never consulted by ComplianceEngine.
    scan["ocrMode"] = resolved_ocr_mode
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
    _report_progress("REPORT_GENERATION")
    return _public_scan(scan)


async def _run_analysis_job(
    job_id: str,
    contents: bytes,
    filename: str,
    content_type: str | None,
    product_name: str,
    category: str,
    manufacturer: str,
    user: dict[str, Any],
) -> None:
    token = _active_progress_job.set(job_id)
    try:
        headers = Headers({"content-type": content_type}) if content_type else None
        upload = UploadFile(file=BytesIO(contents), filename=filename, headers=headers)
        result = await create_scan(upload, product_name, category, manufacturer, user)
        if isinstance(result, JSONResponse):
            body = result.body.decode("utf-8") if result.body else "{}"
            payload = __import__("json").loads(body)
            message = payload.get("error", {}).get("message", "Analysis failed")
            _report_progress(_analysis_jobs[job_id].get("stage", "OCR_PROCESSING"), error=message)
            return
        _report_progress("COMPLETED", result=result)
    except Exception as exc:
        logger.exception("Analysis job %s failed", job_id)
        _report_progress(_analysis_jobs[job_id].get("stage", "REPORT_GENERATION"), error=str(exc))
    finally:
        _active_progress_job.reset(token)


def _status_for_compliance(status: str) -> str:
    return {
        "COMPLIANT": "compliant",
        "NON_COMPLIANT": "flagged",
        "PARTIALLY_COMPLIANT": "partial",
        "NEEDS_MANUAL_VERIFICATION": "manual_review",
        "REVIEW_REQUIRED": "manual_review",
        "NOT_APPLICABLE": "not_applicable",
    }.get(status, "manual_review")


def _run_image_ocr(contents: bytes, filename: str, progress_callback: Any) -> OCRResult:
    suffix = Path(filename).suffix.lower()
    tmp_path = _UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        tmp_path.write_bytes(contents)
        result = _service.run(
            str(tmp_path),
            yolo_service=_yolo_service,
            preprocess_options=_preprocess_options,
            progress_callback=progress_callback,
        )
        result.image = filename
        result.image_path = None
        if result.error is not None:
            result.error.message = result.error.message.replace(str(tmp_path), filename)
        return result
    except Exception as exc:
        logger.exception("OCR failed for multi-image upload %s", filename)
        return OCRResult(success=False, image=filename, error=OCRError(code="internal_error", message=str(exc)))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file %s", tmp_path, exc_info=True)


async def _run_multi_analysis_job(
    job_id: str,
    images: list[dict[str, Any]],
    product_name: str,
    category: str,
    manufacturer: str,
    user: dict[str, Any],
) -> None:
    token = _active_progress_job.set(job_id)
    image_results: list[dict[str, Any]] = []
    try:
        total = len(images)
        _report_analysis_update(12, "IMAGE_VALIDATION", f"Validating {total} uploaded images", imageCount=total)
        for index, image in enumerate(images):
            image_number = index + 1
            start = 18 + round(index / total * 57)
            _report_analysis_update(start, "IMAGE_PREPROCESSING", f"Preparing image {image_number} of {total}", currentImage=image_number, currentSide=image["side"])

            def image_progress(stage: str, image_number: int = image_number, side: str = image["side"]) -> None:
                offsets = {"IMAGE_PREPROCESSING": 0, "OBJECT_DETECTION": 10, "OCR_PROCESSING": 28}
                progress = 18 + round(index / total * 57) + offsets.get(stage, 0)
                _report_analysis_update(progress, stage, f"{ANALYSIS_STAGES[stage][1]}: image {image_number} of {total}", currentImage=image_number, currentSide=side)

            result = _run_image_ocr(image["contents"], image["filename"], image_progress)
            image_results.append({"image": image, "result": result})
            if not result.success:
                logger.warning("Image %d/%d failed: %s", image_number, total, result.error.message if result.error else "unknown error")

        successful = [item for item in image_results if item["result"].success]
        if not successful:
            _report_progress("OCR_PROCESSING", error="No uploaded image could be processed")
            return

        _report_analysis_update(78, "TEXT_COMBINING", "Combining OCR results from all product sides", processedImages=len(successful), failedImages=total - len(successful))
        results = [item["result"] for item in successful]
        detections = _deduplicate_detections(results)
        combined = OCRResult(
            success=True,
            image=results[0].image,
            full_text="\n".join(detection.text for detection in detections),
            detections=detections,
            detection_count=len(detections),
            preprocessing_applied=any(result.preprocessing_applied for result in results),
            object_detections=[detection for result in results for detection in result.object_detections],
        )
        _report_analysis_update(85, "TEXT_STRUCTURING", "Structuring combined product information", processedImages=len(successful), failedImages=total - len(successful))
        ocr_dict = combined.model_dump()
        normalized = normalize_ocr_result(ocr_dict)
        structured_extraction = build_structured_extraction(ocr_dict, normalized=normalized)
        _report_analysis_update(91, "DECLARATION_IDENTIFICATION", "Identifying product declarations across all sides", processedImages=len(successful), failedImages=total - len(successful))
        validation_profile, is_wholesale = _select_validation_profile(category)
        if is_wholesale:
            ocr_dict["package_type"] = "wholesale"
        compliance = _compliance_engine.evaluate(ocr_dict, validation_profile)
        _report_analysis_update(95, "RULE_VALIDATION", "Checking Legal Metrology rules", processedImages=len(successful), failedImages=total - len(successful))

        scan = _scan_document(combined, product_name.strip(), category.strip(), manufacturer.strip(), combined.image)
        scan.update({
            "userId": user["_id"], "success": True, "image": combined.image,
            "full_text": combined.full_text, "detections": [d.model_dump() for d in combined.detections],
            "detection_count": combined.detection_count, "preprocessing_applied": combined.preprocessing_applied,
            "error": None, "compliance": compliance.model_dump(), "status": _status_for_compliance(compliance.overall_status),
            "score": compliance.compliance_score, "structuredExtraction": structured_extraction.model_dump(),
            "imagesProcessed": len(successful), "imagesRequested": total,
            "imageResults": [{"fileName": item["image"]["filename"], "side": item["image"]["side"], "success": item["result"].success, "detectionCount": item["result"].detection_count, "error": item["result"].error.model_dump() if item["result"].error else None} for item in image_results],
        })
        database = get_database()
        source_images = []
        for item in successful:
            image = item["image"]
            image_id = GridFS(database, collection="scan_images").put(image["contents"], filename=image["filename"], content_type=image["content_type"], scan_id=scan["id"], user_id=user["_id"])
            source_images.append({"id": str(image_id), "fileName": image["filename"], "side": image["side"], "detectionCount": item["result"].detection_count})
        scan["sourceImages"] = source_images
        if source_images:
            scan["sourceImageId"] = source_images[0]["id"]
            scan["sourceImageUrl"] = f"/api/scans/{scan['id']}/image"
            for source_image in source_images:
                source_image["url"] = f"/api/scans/{scan['id']}/images/{source_images.index(source_image)}"
        database.scans.insert_one(scan)
        database.history.insert_one({"userId": user["_id"], "actionType": "scan_uploaded", "title": "Product label uploaded", "description": f"Multi-image OCR scan created for {scan['productName']}.", "documentId": scan["id"], "metadata": {"fileName": combined.image, "category": scan["category"], "imagesProcessed": len(successful)}, "createdAt": datetime.now(timezone.utc)})
        _report_analysis_update(98, "REPORT_GENERATION", "Generating unified compliance report", processedImages=len(successful), failedImages=total - len(successful))
        _report_progress("COMPLETED", result=_public_scan(scan))
    except Exception as exc:
        logger.exception("Multi-image analysis job %s failed", job_id)
        _report_progress(_analysis_jobs[job_id].get("stage", "REPORT_GENERATION"), error=str(exc))
    finally:
        _active_progress_job.reset(token)


def _run_multi_analysis_job_in_worker(*args: Any) -> None:
    asyncio.run(_run_multi_analysis_job(*args))


def _run_analysis_job_in_worker(*args: Any) -> None:
    """Run blocking OCR/model work off FastAPI's event-loop thread."""
    asyncio.run(_run_analysis_job(*args))


@router.post("/api/analysis-jobs")
async def start_analysis_job(
    background_tasks: BackgroundTasks,
    file: list[UploadFile] = File(...),
    product_name: str = Form(""),
    category: str = Form("General"),
    manufacturer: str = Form("Not specified"),
    image_sides: str = Form("[]"),
    user: dict[str, Any] = Depends(official_user),
):
    if not file:
        raise HTTPException(status_code=400, detail="No file was uploaded")
    if len(file) > MAX_ANALYSIS_IMAGES:
        raise HTTPException(status_code=400, detail=f"Upload up to {MAX_ANALYSIS_IMAGES} images per product")
    try:
        requested_sides = __import__("json").loads(image_sides)
    except (TypeError, ValueError):
        requested_sides = []
    images = []
    seen_names = set()
    for index, upload in enumerate(file):
        filename = Path(upload.filename or "").name
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported image format for {filename}")
        if filename.casefold() in seen_names:
            continue
        contents = await upload.read()
        if not contents:
            raise HTTPException(status_code=400, detail=f"Uploaded file is empty: {filename}")
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail=f"File exceeds the 15MB limit: {filename}")
        seen_names.add(filename.casefold())
        side = requested_sides[index] if index < len(requested_sides) and requested_sides[index] in {"Front", "Back", "Left Side", "Right Side", "Top", "Bottom", "Other"} else "Other"
        images.append({"contents": contents, "filename": filename, "content_type": upload.content_type, "side": side})

    job_id = f"AN-{uuid.uuid4().hex[:12].upper()}"
    _analysis_jobs[job_id] = {"id": job_id, "userId": user["_id"], "status": "running", "stage": "INITIALIZING", "progress": 10, "label": "Initializing analysis", "imageCount": len(images)}
    background_tasks.add_task(_run_multi_analysis_job_in_worker, job_id, images, product_name, category, manufacturer, user)
    return {"id": job_id, "status": "running", "stage": "INITIALIZING", "progress": 10, "label": "Initializing analysis", "imageCount": len(images)}


@router.get("/api/analysis-jobs/{job_id}")
def get_analysis_job(job_id: str, user: dict[str, Any] = Depends(official_user)):
    job = _analysis_jobs.get(job_id)
    if job is None or job.get("userId") != user["_id"]:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return {key: value for key, value in job.items() if key != "userId"}


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


@router.delete("/api/scans/{scan_id}")
def delete_scan(scan_id: str, user: dict[str, Any] = Depends(official_user)):
    """Delete an owned scan and its stored source image."""
    database = get_database()
    scan = database.scans.find_one({"id": scan_id, "userId": user["_id"]})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.get("sourceImageId"):
        try:
            GridFS(database, collection="scan_images").delete(ObjectId(scan["sourceImageId"]))
        except NoFile:
            logger.info("Source image %s was already absent for scan %s", scan["sourceImageId"], scan_id)
    database.scans.delete_one({"_id": scan["_id"]})
    database.history.delete_many({"userId": user["_id"], "documentId": scan_id})
    return {"success": True, "id": scan_id}


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


@router.get("/api/scans/{scan_id}/images/{image_index}")
def get_scan_source_image(scan_id: str, image_index: int, user: dict[str, Any] = Depends(official_user)):
    """Stream one source image from a multi-image scan."""
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"sourceImages": 1})
    source_images = scan.get("sourceImages", []) if scan else []
    if scan is None or image_index < 0 or image_index >= len(source_images):
        raise HTTPException(status_code=404, detail="Source image not found")
    try:
        image = GridFS(get_database(), collection="scan_images").get(ObjectId(source_images[image_index]["id"]))
    except NoFile as exc:
        raise HTTPException(status_code=404, detail="Source image not found") from exc
    return StreamingResponse(image, media_type=image.content_type or "application/octet-stream")


@router.get("/api/cases")
def list_cases(user: dict[str, Any] = Depends(official_user)):
    """Return persisted cases newest first."""
    cases = get_database().cases.find({"userId": user["_id"]}, {"_id": 0}).sort("date", -1)
    return [{**case, "userId": str(case["userId"])} if case.get("userId") is not None else case for case in cases]
