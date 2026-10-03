"""
HTTP layer for OCR. This module owns request validation, temp-file handling,
and response shaping ONLY - all actual OCR inference is delegated to the
existing, already-tested PaddleOCRService. No OCR/PaddleOCR logic is
duplicated here.
"""

from __future__ import annotations

import base64
import logging
import asyncio
import os
from contextvars import ContextVar
from io import BytesIO
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from gridfs import GridFS
from gridfs.errors import NoFile
from PIL import Image
import numpy as np
from starlette.datastructures import Headers
from pydantic import BaseModel, Field

from backend.database import get_database
from backend.api.auth import official_user
from backend.services.audit import append_audit_event
from backend.services.authenticity_service import build_authenticity_evidence
from backend.services.capture_quality import evaluate_capture_bytes
from backend.services.evidence_package import assemble_evidence_package_from_scan, build_evidence_package, record_evidence_package
from backend.services.barcode_calibration import calibrate_ean13_scale, decode_ean13_barcodes
from backend.services.gs1_verification import GS1VerificationService, crop_barcode_image
from backend.services.gs1_resolver_provider import GS1ResolverCEProvider
from backend.services.image_integrity import compute_image_sha256, sniff_image_mime_type
from backend.services.legibility_validator import measure_ocr_detection
from backend.services.measurement import measure_ocr_dimensions
from backend.services.pdp_detector import PDPDetectorService
from backend.ocr.ensemble import run_ensemble
from backend.ocr.tesseract_service import TesseractOCRService
from backend.ocr.gemini_service import (
    GEMINI_API_KEY,
    GEMINI_ENABLED,
    GeminiService,
    apply_suggestions_to_ocr_result,
    find_ambiguous_fields,
)
from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
from backend.ocr.preprocessing import MAX_SIDE_DEFAULT, PreprocessOptions, preprocess_image, production_preprocess_options
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
_gs1_verification_service = GS1VerificationService(provider=GS1ResolverCEProvider.from_env())

# Same one-instance-per-process pattern as _service above: YOLOService caches
# the loaded YOLO26 model at the class level, so it is loaded once and reused
# for every request. Disabled entirely via YOLO_ENABLED=false, in which case
# OCR runs exactly as it did before YOLO26 was introduced.
_yolo_service = YOLOService() if YOLO_ENABLED else None
_pdp_detector_service = PDPDetectorService()

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
_preprocess_options = production_preprocess_options()

# Gemini is an AI/vision FALLBACK for ambiguous/low-confidence fields only -
# see backend/ocr/gemini_service.py. Disabled by default (GEMINI_ENABLED) and
# a no-op without an API key, in which case every scan behaves exactly as it
# did before Gemini was introduced. Constructing GeminiService is cheap (no
# network call happens until a request is actually made), same lazy pattern
# as _yolo_service above.
_gemini_service = GeminiService() if GEMINI_ENABLED else None

TESSERACT_ENABLED = os.getenv("TESSERACT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
_raw_tesseract_mode = os.getenv("TESSERACT_MODE", "secondary").strip().lower()
TESSERACT_MODE = _raw_tesseract_mode if _raw_tesseract_mode in {"secondary", "fallback"} else "secondary"
_tesseract_service = TesseractOCRService()

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB safety cap for local dev


async def read_upload_limited(file: UploadFile) -> bytes:
    return await file.read(MAX_UPLOAD_BYTES + 1)

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
PDP_CROP_OCR_ENABLED = os.getenv("LEGAL_LENSE_PDP_CROP_OCR_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


class ScanCalibrationRequest(BaseModel):
    code: str = Field(..., min_length=13, max_length=13)
    polygon: list[list[float]]
    barcode_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reference_width: float = Field(..., gt=0.0)
    reference_unit: str = "mm"
    reference_source: str = Field(..., min_length=1, max_length=500)
    reference_id: str = Field(..., min_length=1, max_length=200)
    reference_sha256: str = Field(..., min_length=64, max_length=64)
    reference_verification_status: str = "unverified"
    reference_file_name: str | None = Field(default=None, min_length=1, max_length=200)
    reference_file_mime_type: str | None = Field(default=None, min_length=1, max_length=100)
    reference_file_base64: str | None = Field(default=None, max_length=7_000_000)


def _experimental_pdp_crop_ocr(image: Any, pdp_result: dict[str, Any]) -> dict[str, Any]:
    """Optionally OCR validated PDP crops as supplementary, region-local evidence."""
    if not PDP_CROP_OCR_ENABLED:
        return {"status": "disabled", "enabled": False, "regions": [], "authoritative": False, "used_for_compliance": False}
    if not isinstance(pdp_result, dict) or pdp_result.get("status") != "experimental_candidate" or pdp_result.get("available") is not True:
        return {"status": "review_required", "enabled": True, "regions": [], "reason": "Validated PDP candidates are unavailable.", "authoritative": False, "used_for_compliance": False}
    if image is None or getattr(image, "size", 0) == 0:
        return {"status": "review_required", "enabled": True, "regions": [], "reason": "Source image is unavailable.", "authoritative": False, "used_for_compliance": False}
    height, width = image.shape[:2]
    output = []
    for detection in pdp_result.get("detections", [])[:8]:
        try:
            x1, y1, x2, y2 = (int(round(float(value))) for value in detection["bbox"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        x1, x2 = max(0, x1), min(width, x2)
        y1, y2 = max(0, y1), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        file_path = _UPLOAD_TMP_DIR / f"pdp-crop-{uuid.uuid4().hex}.png"
        try:
            crop = Image.fromarray(image[y1:y2, x1:x2, ::-1].copy())
            crop.save(file_path, format="PNG")
            result = _service.run(str(file_path), preprocess_options=PreprocessOptions(resize=True, max_side=MAX_SIDE_DEFAULT), yolo_service=None)
            output.append({
                "region_id": detection.get("region_id"), "class_name": detection.get("class_name"),
                "bbox": [x1, y1, x2, y2], "detection_confidence": detection.get("confidence"),
                "ocr_status": "experimental_candidate" if result.success else "review_required",
                "full_text": result.full_text if result.success else "",
                "detections": [item.model_dump() for item in result.detections] if result.success else [],
                "detection_coordinates": "region_local_pixels", "source": "paddleocr_on_experimental_pdp_crop",
                "authoritative": False, "used_for_compliance": False,
            })
        except Exception as exc:
            output.append({"region_id": detection.get("region_id"), "bbox": [x1, y1, x2, y2], "ocr_status": "review_required", "reason": type(exc).__name__, "authoritative": False, "used_for_compliance": False})
        finally:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary PDP crop file")
    return {"status": "experimental_candidate" if output else "review_required", "enabled": True, "regions": output, "authoritative": False, "used_for_compliance": False}


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


def _error_response(status_code: int, image: str, code: str, message: str, secondary_ocr: dict[str, Any] | None = None) -> JSONResponse:
    result = OCRResult(success=False, image=image, error=OCRError(code=code, message=message))
    content = result.model_dump()
    if secondary_ocr is not None:
        content["secondary_ocr"] = secondary_ocr
    return JSONResponse(status_code=status_code, content=content)


def _secondary_ocr_evidence(contents: bytes, paddle_result: OCRResult | None, preprocess_options: PreprocessOptions | None = None) -> dict[str, Any] | None:
    if not TESSERACT_ENABLED:
        return None
    paddle_has_text = paddle_result is not None and paddle_result.success and paddle_result.detection_count > 0
    if TESSERACT_MODE == "fallback" and paddle_has_text:
        return None
    if not _tesseract_service.enabled:
        return {
            "status": "unavailable",
            "mode": TESSERACT_MODE,
            "engine": "tesseract",
            "provenance": "secondary_review_only",
            "used_for_compliance": False,
            "message": "Tesseract was requested but its optional Python package or native binary is unavailable.",
        }
    try:
        with Image.open(BytesIO(contents)) as source_image:
            image = source_image.convert("RGB")
    except Exception as exc:
        return {
            "status": "unavailable",
            "mode": TESSERACT_MODE,
            "engine": "tesseract",
            "provenance": "secondary_review_only",
            "used_for_compliance": False,
            "message": f"Could not decode the source image for secondary OCR: {type(exc).__name__}",
        }

    source_width, source_height = image.size
    ocr_image = np.asarray(image)[:, :, ::-1].copy()
    if preprocess_options is not None:
        try:
            ocr_image = preprocess_image(ocr_image, preprocess_options)
        except Exception as exc:
            return {
                "status": "unavailable", "mode": TESSERACT_MODE, "engine": "tesseract",
                "provenance": "secondary_review_only", "used_for_compliance": False,
                "message": f"Could not align secondary OCR preprocessing ({type(exc).__name__}).",
            }
    candidate = _tesseract_service.run(ocr_image)
    if not candidate.get("success"):
        return {
            "status": "unavailable",
            "mode": TESSERACT_MODE,
            "engine": "tesseract",
            "provenance": "secondary_review_only",
            "used_for_compliance": False,
            "result": candidate,
        }

    paddle_succeeded = paddle_has_text
    evidence: dict[str, Any] = {
        "status": "secondary_completed" if paddle_succeeded else "fallback_review_required",
        "mode": TESSERACT_MODE,
        "engine": "tesseract",
        "provenance": "secondary_review_only",
        "authoritative_engine": "paddleocr" if paddle_succeeded else None,
        "used_for_compliance": False,
        "requires_manual_review": not paddle_succeeded,
        "result": candidate,
        "coordinate_transform": {
            "coordinate_space": "paddleocr_input_pixels",
            "source_image_size": {"width": source_width, "height": source_height},
            "ocr_input_size": {"width": int(ocr_image.shape[1]), "height": int(ocr_image.shape[0])},
            "source_to_ocr_scale": {"x": round(source_width / max(1, ocr_image.shape[1]), 8), "y": round(source_height / max(1, ocr_image.shape[0]), 8)},
        },
    }
    if paddle_succeeded:
        evidence["comparison"] = run_ensemble(paddle_result.model_dump(), candidate)
        evidence["comparison"]["coordinate_space"] = "paddleocr_input_pixels"
        evidence["comparison"]["source_to_ocr_scale"] = {
            "x": round(source_width / max(1, ocr_image.shape[1]), 8),
            "y": round(source_height / max(1, ocr_image.shape[0]), 8),
        }
        evidence["comparison"]["source_image_size"] = {"width": source_width, "height": source_height}
        evidence["comparison"]["ocr_input_size"] = {"width": int(ocr_image.shape[1]), "height": int(ocr_image.shape[0])}
    return evidence


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
        "reportId": f"RPT-{uuid.uuid4().hex[:12].upper()}",
        "findings": [],
        "ocr": result.model_dump(),
    }


def _image_report(
    *,
    scan_id: str,
    report_id: str,
    image_id: str,
    image: dict[str, Any],
    result: OCRResult,
    structured_extraction: Any,
    compliance: Any,
    source_index: int,
) -> dict[str, Any]:
    """Build an independently addressable, image-scoped official report."""
    return {
        "reportId": report_id,
        "scanId": scan_id,
        "imageId": image_id,
        "sourceIndex": source_index,
        "fileName": image["filename"],
        "side": image["side"],
        "date": datetime.now(timezone.utc).isoformat(),
        "status": _status_for_compliance(compliance.overall_status),
        "score": compliance.compliance_score,
        "ocr": result.model_dump(),
        "structuredExtraction": structured_extraction.model_dump(),
        "compliance": compliance.model_dump(),
        "sourceImageSha256": image.get("sha256"),
        "evidenceScope": "image_only",
    }


def _public_scan(scan: dict[str, Any]) -> dict[str, Any]:
    """Remove Mongo internals and make ownership IDs JSON serializable."""
    public = {key: value for key, value in scan.items() if key != "_id"}
    if public.get("userId") is not None:
        public["userId"] = str(public["userId"])
    return public


def _attach_gs1_verification(
    *,
    database: Any,
    scan_id: str,
    image_id: str,
    report_id: str,
    user_id: Any,
    image_bytes: bytes,
    ocr_result: OCRResult,
    structured_extraction: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Create local GS1 evidence for exactly one image/report; never affects compliance."""
    decoding = ocr_result.barcode_decoding or {"status": "no_barcode_detected", "detections": [], "authoritative": False}
    extraction = (
        structured_extraction.model_dump(mode="json")
        if hasattr(structured_extraction, "model_dump")
        else structured_extraction if isinstance(structured_extraction, dict) else None
    )
    result = _gs1_verification_service.verify(
        decoding,
        structured_extraction=extraction,
        scan_id=scan_id,
        image_id=image_id,
        report_id=report_id,
    )
    crops: list[dict[str, Any]] = []
    image_store = GridFS(database, collection="scan_images")
    for barcode in result["barcodes"]:
        bbox = barcode.get("bbox")
        crop_bytes = crop_barcode_image(image_bytes, bbox)
        if not crop_bytes:
            continue
        crop_digest = compute_image_sha256(crop_bytes)
        evidence_id = barcode["evidence"]["evidence_id"]
        try:
            crop_file_id = image_store.put(
                crop_bytes,
                filename=f"{report_id}-{barcode['barcode_id']}.png",
                content_type="image/png",
                metadata={
                    "scan_id": scan_id,
                    "user_id": str(user_id),
                    "image_id": image_id,
                    "report_id": report_id,
                    "barcode_id": barcode["barcode_id"],
                    "evidence_id": evidence_id,
                    "artifact_type": "gs1_barcode_crop",
                    "sha256": crop_digest,
                },
            )
        except Exception:
            logger.exception("Could not persist GS1 barcode crop for report %s", report_id)
            continue
        crop_url = f"/api/scans/{scan_id}/gs1-evidence/{crop_file_id}"
        barcode["evidence"].update({"crop_file_id": str(crop_file_id), "crop_url": crop_url, "crop_sha256": crop_digest})
        crops.append({
            "contents": crop_bytes,
            "file_name": f"{report_id}-{barcode['barcode_id']}.png",
            "sha256": crop_digest,
            "image_id": image_id,
            "report_id": report_id,
            "barcode_id": barcode["barcode_id"],
            "evidence_id": evidence_id,
        })
    try:
        append_audit_event(
            database,
            event_type="gs1_verification",
            entity_type="scan_report",
            entity_id=report_id,
            user_id=user_id,
            actor_role="system",
            action=result["status"],
            file_name=ocr_result.image,
            source_image_sha256=decoding.get("source_image_sha256"),
            details={
                "scan_id": scan_id,
                "image_id": image_id,
                "report_id": report_id,
                "barcode_ids": [barcode["barcode_id"] for barcode in result["barcodes"]],
                "gtins": [barcode["gtin"] for barcode in result["barcodes"] if barcode["gtin"]],
                "validation_states": [barcode["validation_state"] for barcode in result["barcodes"]],
                "provider_state": result["provider_state"]["status"],
                "resolver_states": [
                    (barcode.get("external_verification") or {}).get("status", "NOT_CONFIGURED")
                    for barcode in result["barcodes"]
                ],
                "resolver_sources": sorted({
                    (barcode.get("external_verification") or {}).get("source")
                    for barcode in result["barcodes"]
                    if (barcode.get("external_verification") or {}).get("source")
                }),
                "evidence_ids": [barcode["evidence"]["evidence_id"] for barcode in result["barcodes"]],
                "authoritative": False,
                "used_for_compliance": False,
            },
        )
    except Exception:
        logger.exception("Could not record GS1 evidence audit for report %s", report_id)
    return result, crops


def _unavailable_font_measurements(detections: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain OCR geometry while refusing physical measurements without scale."""
    items = []
    for index, detection in enumerate(detections):
        region_id = f"region_{index}"
        measurement = measure_ocr_detection(
            detection=detection,
            calibration=None,
            evidence_region_ids=[region_id],
        )
        items.append({"detection_id": region_id, **measurement})
    return {
        "status": "review_required",
        "available": False,
        "review_reason": "No validated barcode-symbol geometry and verified physical reference dimension are available for this scan.",
        "items": items,
    }


def _experimental_measurement_evidence(detections: list[dict[str, Any]]) -> dict[str, Any]:
    """Build pixel and font geometry evidence; physical scale stays unavailable."""
    dimensions = []
    for index, detection in enumerate(detections[:10000]):
        region_id = f"region_{index}"
        dimensions.append({"measurement_id": f"measurement_{index}", **measure_ocr_dimensions(
            detection, calibration=None, evidence_region_ids=[region_id],
        )})
    return {
        "status": "review_required", "available": False,
        "reason": "No validated physical calibration was supplied; only pixel geometry is recorded.",
        "items": dimensions, "authoritative": False, "used_for_compliance": False,
    }


def _capture_assessment(contents: bytes, *, filename: str, side: str) -> dict[str, Any]:
    try:
        quality = evaluate_capture_bytes(contents)
        return {
            "status": "assessed",
            "filename": filename,
            "side": side,
            "quality": quality,
        }
    except ValueError as exc:
        return {
            "status": "unavailable",
            "filename": filename,
            "side": side,
            "quality": {
                "acceptable": False,
                "reasons": [f"Capture quality could not be assessed: {exc}"],
                "source_image_sha256": compute_image_sha256(contents),
                "framing_status": "manual_visual_check_required",
                "physical_calibration": {"status": "unavailable", "review_required": True},
            },
        }


def _capture_text_visibility(result: OCRResult) -> dict[str, Any]:
    detections = result.detections[:200] if result.success else []
    if not result.success:
        status = "unavailable"
        guidance = "Text visibility could not be assessed; inspect the package image manually."
    elif detections:
        status = "regions_detected"
        guidance = "Review the detected text regions against the original package image."
    else:
        status = "not_detected_review_required"
        guidance = "No text regions were detected; this does not establish that declarations are absent. Inspect the image manually."
    return {
        "status": status,
        "detected_region_count": len(detections),
        "evidence_regions": [detection.model_dump() for detection in detections],
        "coordinate_space": "paddleocr_input_pixels",
        "ocr_image_dimensions": result.image_dimensions,
        "guidance": guidance,
        "authoritative": False,
        "used_for_compliance": False,
    }


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

    contents = await read_upload_limited(file)
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
        result = await asyncio.to_thread(_service.run, str(tmp_path), **run_options)
        source_image_sha256 = compute_image_sha256(contents)
        try:
            source_image = PaddleOCRService.load_image(str(tmp_path))
        except Exception:
            source_image = None
        result.barcode_decoding = decode_ean13_barcodes(
            source_image, source_image_sha256=source_image_sha256,
        )
        result.pdp_detection = _pdp_detector_service.run(source_image, source_image_sha256=source_image_sha256)
        result.pdp_detection["ocr_crop_evidence"] = _experimental_pdp_crop_ocr(source_image, result.pdp_detection)
        # Swap the temp filename back out for the original, client-facing name,
        # and scrub the server-side temp path out of any error message so no
        # internal filesystem detail reaches the browser.
        result.image = original_name
        result.image_path = None
        if result.error is not None:
            result.error.message = result.error.message.replace(str(tmp_path), original_name)
        result.secondary_ocr = _secondary_ocr_evidence(contents, result, preprocess_options)

        status_code = 200 if result.success else 422
        logger.info(
            "OCR request for '%s' complete: success=%s detections=%d",
            original_name, result.success, result.detection_count,
        )
        return JSONResponse(status_code=status_code, content=result.model_dump())

    except OCRInitializationError as exc:
        logger.exception("PaddleOCR engine failed to initialize")
        secondary_ocr = _secondary_ocr_evidence(contents, None, preprocess_options)
        return _error_response(503, original_name, "engine_init_failed", "OCR engine failed to initialize", secondary_ocr)

    except Exception:
        logger.exception("Unexpected error while processing OCR request for %s", original_name)
        secondary_ocr = _secondary_ocr_evidence(contents, None, preprocess_options)
        return _error_response(500, original_name, "internal_error", "Unexpected server error during OCR processing", secondary_ocr)

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


@router.post("/api/captures/quality")
async def assess_capture_quality(
    file: Optional[UploadFile] = File(None),
    user: dict[str, Any] = Depends(official_user),
):
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No image was uploaded")
    filename = Path(file.filename).name
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported image type")
    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 15MB upload limit")
    try:
        quality = evaluate_capture_bytes(contents)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    database = get_database()
    source_hash = quality["source_image_sha256"]
    event = append_audit_event(
        database,
        event_type="capture_evaluation",
        entity_type="capture",
        entity_id=source_hash,
        user_id=user["_id"],
        actor_role=user.get("role"),
        action="capture_quality_accepted" if quality["acceptable"] else "capture_quality_rejected",
        file_name=filename,
        source_image_sha256=source_hash,
        details={
            "acceptable": quality["acceptable"],
            "reasons": quality["reasons"],
            "dimensions": [quality["image_width"], quality["image_height"]],
            "framing_status": quality["framing_status"],
            "physical_calibration": quality["physical_calibration"],
        },
    )
    return {
        "success": True,
        "filename": filename,
        "quality": quality,
        "audit_event_hash": event["eventHash"],
    }


@router.post("/api/scans")
async def create_scan(
    file: Optional[UploadFile] = File(None),
    product_name: str = Form(""),
    category: str = Form("General"),
    manufacturer: str = Form("Not specified"),
    ocr_mode: str = Form(DEFAULT_OCR_MODE),
    image_side: str = Form("Other"),
    capture_quality_requested: bool = Form(False),
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

    original_contents = await read_upload_limited(file) if file is not None else b""
    if len(original_contents) > MAX_UPLOAD_BYTES:
        return _error_response(
            400,
            Path(file.filename).name if file is not None and file.filename else "",
            "file_too_large",
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )
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
    source_mime_type = sniff_image_mime_type(original_contents, file.content_type if file is not None else None)
    capture_assessment = (
        _capture_assessment(original_contents, filename=Path(file.filename).name, side=image_side)
        if capture_quality_requested and original_contents and file is not None
        else None
    )
    response = await _run_ocr_core(file, effective_preprocess_options)
    if response.status_code != 200:
        return response

    result = OCRResult.model_validate(response.body and __import__("json").loads(response.body))
    if not result.success:
        return response
    if capture_assessment is not None:
        capture_assessment["quality"]["text_visibility"] = _capture_text_visibility(result)

    _report_progress("OCR_PROCESSING")

    secondary_ocr = result.secondary_ocr
    pdp_detection = result.pdp_detection
    barcode_decoding = result.barcode_decoding
    ocr_dict = result.model_dump(exclude={"secondary_ocr", "pdp_detection", "barcode_decoding"})
    structured_ocr_dict = ocr_dict
    structured_normalized = None
    # Computed once and reused for both the Gemini ambiguous-field check and
    # structured extraction below (see find_ambiguous_fields' and
    # build_structured_extraction's `normalized` parameter) - a cheap, pure,
    # CPU-only function (no OCR/model re-run), but sharing it avoids even
    # that redundant recomputation in the common case where Gemini doesn't
    # end up changing anything.
    normalized = normalize_ocr_result(ocr_dict)
    structured_normalized = normalized
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
                    structured_ocr_dict = apply_suggestions_to_ocr_result(ocr_dict, suggestions)
                    structured_normalized = normalize_ocr_result(structured_ocr_dict)
                    gemini_fields_used = [s.field for s in suggestions]
                    logger.info("Gemini fallback supplied %d field(s) for %s: %s", len(suggestions), result.image, gemini_fields_used)
                except Exception:
                    logger.exception("Failed to merge Gemini suggestions for %s; continuing with existing OCR result", result.image)

    try:
        structured_extraction = build_structured_extraction(
            structured_ocr_dict,
            normalized=structured_normalized,
            gemini_suggestions=suggestions or None,
        )
    except Exception:
        logger.exception("Structured extraction failed for %s; scan continues without it", result.image)
        structured_extraction = None

    _report_progress("DECLARATION_IDENTIFICATION")

    validation_profile, is_wholesale = _select_validation_profile(category)
    if is_wholesale:
        ocr_dict["package_type"] = "wholesale"
    compliance = _compliance_engine.evaluate(ocr_dict, validation_profile, structured_extraction=structured_extraction)
    _report_progress("RULE_VALIDATION")
    scan = _scan_document(result, product_name.strip(), category.strip(), manufacturer.strip(), result.image)
    font_measurements = _unavailable_font_measurements([d.model_dump() for d in result.detections])
    physical_measurements = _experimental_measurement_evidence([d.model_dump() for d in result.detections])
    scan["fontHeightMeasurements"] = font_measurements["items"]
    scan["measurementReview"] = {key: value for key, value in font_measurements.items() if key != "items"}
    scan["physicalMeasurements"] = physical_measurements
    scan["barcodeDecoding"] = barcode_decoding or {"status": "unavailable", "available": False, "detections": [], "authoritative": False, "used_for_compliance": False}
    scan["barcodeCalibration"] = {"status": "unavailable", "available": False, "scale_px_per_mm": None, "authoritative": False, "used_for_compliance": False, "reason": "No verified physical reference dimension was supplied.", "evidence_region": None}
    if capture_assessment is not None:
        scan["captureAssessments"] = [capture_assessment]
    if secondary_ocr is not None:
        scan["secondaryOcr"] = secondary_ocr
    if pdp_detection is not None:
        scan["pdpDetection"] = pdp_detection
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
    if source_mime_type:
        scan["sourceMimeType"] = source_mime_type
    # Phase 2B: evidence/traceability only - which OCR mode actually
    # produced this scan's OCR result, never consulted by ComplianceEngine.
    scan["ocrMode"] = resolved_ocr_mode
    status_map = {
        "PASS": "compliant",
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
        "ocrImageDimensions": result.image_dimensions,
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
    upload_audit = append_audit_event(
        database,
        event_type="upload",
        entity_type="scan",
        entity_id=scan["id"],
        user_id=user["_id"],
        actor_role=user.get("role"),
        action="scan_uploaded",
        file_name=result.image,
        source_image_sha256=source_image_sha256,
        details={
            "fileHash": source_image_sha256,
            "mimeType": source_mime_type,
            "productName": product_name.strip(),
            "category": category.strip(),
            "manufacturer": manufacturer.strip(),
        },
    )
    ocr_audit = append_audit_event(
        database,
        event_type="ocr_run",
        entity_type="scan",
        entity_id=scan["id"],
        user_id=user["_id"],
        actor_role=user.get("role"),
        action="ocr_completed",
        file_name=result.image,
        source_image_sha256=source_image_sha256,
        details={
            "engine": "paddleocr",
            "detection_count": result.detection_count,
            "confidence": max((d.confidence for d in result.detections), default=0.0),
            "ocrMode": resolved_ocr_mode,
            "preprocessingApplied": result.preprocessing_applied,
        },
    )
    authenticity_audit = None
    if authenticity is not None:
        authenticity_audit = append_audit_event(
            database,
            event_type="image_authenticity_analysis",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="integrity_evidence_collected",
            file_name=result.image,
            source_image_sha256=source_image_sha256,
            details={
                "exif_available": authenticity.exif.available,
                "ela_available": authenticity.ela.available,
                "ela_method": authenticity.ela.method,
                "ela_difference_sha256": authenticity.ela.difference_sha256,
                "is_compliance_verdict": False,
            },
        )
    capture_audit = None
    if capture_assessment is not None:
        capture_quality = capture_assessment["quality"]
        capture_audit = append_audit_event(
            database,
            event_type="capture_evaluation",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="camera_capture_assessed" if capture_assessment["status"] == "assessed" else "camera_capture_quality_unavailable",
            file_name=result.image,
            source_image_sha256=source_image_sha256,
            details={
                "side": capture_assessment["side"],
                "quality_status": capture_assessment["status"],
                "acceptable": capture_quality.get("acceptable", False),
                "reasons": capture_quality.get("reasons", []),
                "physical_calibration": capture_quality.get("physical_calibration"),
            },
        )
    measurement_audit = append_audit_event(
        database, event_type="experimental_measurement", entity_type="scan", entity_id=scan["id"],
        user_id=user["_id"], actor_role=user.get("role"), action="physical_measurement_review_required",
        file_name=result.image, source_image_sha256=source_image_sha256,
        details={"status": physical_measurements["status"], "measurement_count": len(physical_measurements["items"]), "calibration_status": scan["barcodeCalibration"]["status"], "authoritative": False, "used_for_compliance": False},
    )
    barcode_audit = append_audit_event(
        database,
        event_type="barcode_decoding",
        entity_type="scan",
        entity_id=scan["id"],
        user_id=user["_id"],
        actor_role=user.get("role"),
        action=str((barcode_decoding or {}).get("status", "unavailable")),
        file_name=result.image,
        source_image_sha256=source_image_sha256,
        details={
            "decoder": (barcode_decoding or {}).get("decoder"),
            "detections": len((barcode_decoding or {}).get("detections", [])),
            "valid_ean13": sum(1 for item in (barcode_decoding or {}).get("detections", []) if item.get("status") == "decoded"),
            "authoritative": False,
            "used_for_compliance": False,
        },
    )
    pdp_audit = None
    if pdp_detection is not None:
        pdp_audit = append_audit_event(
            database,
            event_type="experimental_pdp_detection",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action=str(pdp_detection.get("status", "pdp_unavailable")),
            file_name=result.image,
            source_image_sha256=source_image_sha256,
            details={
                "model_hash": pdp_detection.get("model_hash"),
                "model_version": pdp_detection.get("model_version"),
                "dataset_hash": pdp_detection.get("dataset_hash"),
                "capability_state": pdp_detection.get("capability_state"),
                "promotion_status": pdp_detection.get("promotion_status"),
                "status": pdp_detection.get("status"),
                "detection_count": len(pdp_detection.get("detections", [])),
                "production_authority": False,
                "used_for_compliance": False,
            },
        )
    secondary_ocr_audit = None
    if secondary_ocr is not None:
        secondary_result = secondary_ocr.get("result") if isinstance(secondary_ocr.get("result"), dict) else {}
        secondary_ocr_audit = append_audit_event(
            database,
            event_type="secondary_ocr_run",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action=str(secondary_ocr.get("status", "secondary_ocr_unavailable")),
            file_name=result.image,
            source_image_sha256=source_image_sha256,
            details={
                "mode": secondary_ocr.get("mode"),
                "engine": "tesseract",
                "status": secondary_ocr.get("status"),
                "detection_count": secondary_result.get("detection_count", 0),
                "disagreement_count": len((secondary_ocr.get("comparison") or {}).get("disagreements", [])) if isinstance(secondary_ocr.get("comparison"), dict) else 0,
                "used_for_compliance": False,
                "provenance": secondary_ocr.get("provenance"),
            },
        )
    scan["sourceImageId"] = str(image_id)
    scan["sourceImageUrl"] = f"/api/scans/{scan['id']}/image"
    single_image = {
        "filename": result.image,
        "side": image_side,
        "sha256": source_image_sha256,
    }
    scan["reports"] = [_image_report(
        scan_id=scan["id"], report_id=scan["reportId"], image_id=str(image_id),
        image=single_image, result=result, structured_extraction=structured_extraction,
        compliance=compliance, source_index=0,
    )]
    gs1_verification, gs1_crops = _attach_gs1_verification(
        database=database,
        scan_id=scan["id"],
        image_id=str(image_id),
        report_id=scan["reportId"],
        user_id=user["_id"],
        image_bytes=original_contents,
        ocr_result=result,
        structured_extraction=structured_extraction,
    )
    scan["gs1Verification"] = gs1_verification
    scan["reports"][0]["gs1Verification"] = gs1_verification
    gs1_artifacts: dict[str, Any] = {
        f"gs1_verification_{scan['reportId']}": {
            "artifact_type": "gs1_verification_result",
            "value": gs1_verification,
            "source": "local_gs1_verification",
            "derived_from": ["source_image_0"],
            "metadata": {"scan_id": scan["id"], "image_id": str(image_id), "report_id": scan["reportId"]},
        },
    }
    for crop in gs1_crops:
        gs1_artifacts[f"gs1_barcode_crop_{crop['barcode_id']}"] = {
            "artifact_type": "gs1_barcode_crop",
            "value": crop["contents"],
            "source": "barcode_region_crop",
            "derived_from": ["source_image_0"],
            "metadata": {key: crop[key] for key in ("image_id", "report_id", "barcode_id", "evidence_id", "sha256")},
        }
    package = assemble_evidence_package_from_scan(
        evidence_id=scan["id"],
        ocr_result=ocr_dict,
        compliance_result=compliance.model_dump(),
        audit_refs=[
            event["eventHash"]
            for event in (upload_audit, ocr_audit, authenticity_audit, secondary_ocr_audit, capture_audit, measurement_audit, barcode_audit, pdp_audit)
            if event is not None and event.get("eventHash")
        ],
        scan_evidence=scan,
        source_images=[{
            "contents": original_contents,
            "file_name": result.image,
            "mime_type": source_mime_type,
            "sha256": source_image_sha256,
        }],
        supporting_artifacts=gs1_artifacts,
    )
    package_audit = record_evidence_package(database, package, entity_id=scan["id"], entity_type="scan")
    scan["evidencePackageId"] = package.package_id
    scan["evidenceMerkleRoot"] = package.merkle_root
    scan["evidencePackageAuditHash"] = package_audit["eventHash"]
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
    ocr_mode: str = DEFAULT_OCR_MODE,
    image_side: str = "Other",
    capture_quality_requested: bool = False,
) -> None:
    token = _active_progress_job.set(job_id)
    try:
        headers = Headers({"content-type": content_type}) if content_type else None
        upload = UploadFile(file=BytesIO(contents), filename=filename, headers=headers)
        result = await create_scan(
            upload,
            product_name=product_name,
            category=category,
            manufacturer=manufacturer,
            ocr_mode=ocr_mode,
            image_side=image_side,
            capture_quality_requested=capture_quality_requested,
            user=user,
        )
        if isinstance(result, JSONResponse):
            body = result.body.decode("utf-8") if result.body else "{}"
            payload = __import__("json").loads(body)
            message = payload.get("error", {}).get("message", "Analysis failed")
            if payload.get("secondary_ocr") is not None:
                _analysis_jobs[job_id]["secondaryOcr"] = payload["secondary_ocr"]
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
        "PASS": "compliant",
        "COMPLIANT": "compliant",
        "NON_COMPLIANT": "flagged",
        "PARTIALLY_COMPLIANT": "partial",
        "NEEDS_MANUAL_VERIFICATION": "manual_review",
        "REVIEW_REQUIRED": "manual_review",
        "NOT_APPLICABLE": "not_applicable",
    }.get(status, "manual_review")


def _run_image_ocr(
    contents: bytes,
    filename: str,
    progress_callback: Any,
    *,
    preprocess_options: PreprocessOptions | None = None,
) -> OCRResult:
    if preprocess_options is None:
        preprocess_options = _preprocess_options
    suffix = Path(filename).suffix.lower()
    tmp_path = _UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        tmp_path.write_bytes(contents)
        result = _service.run(
            str(tmp_path),
            yolo_service=_yolo_service,
            preprocess_options=preprocess_options,
            progress_callback=progress_callback,
        )
        try:
            source_image = PaddleOCRService.load_image(str(tmp_path))
        except Exception:
            source_image = None
        source_image_sha256 = compute_image_sha256(contents)
        result.barcode_decoding = decode_ean13_barcodes(
            source_image, source_image_sha256=source_image_sha256,
        )
        result.pdp_detection = _pdp_detector_service.run(source_image, source_image_sha256=source_image_sha256)
        result.pdp_detection["ocr_crop_evidence"] = _experimental_pdp_crop_ocr(source_image, result.pdp_detection)
        result.image = filename
        result.image_path = None
        if result.error is not None:
            result.error.message = result.error.message.replace(str(tmp_path), filename)
        result.secondary_ocr = _secondary_ocr_evidence(contents, result, preprocess_options)
        return result
    except Exception as exc:
        logger.exception("OCR failed for multi-image upload %s", filename)
        failed = OCRResult(success=False, image=filename, error=OCRError(code="internal_error", message=str(exc)))
        failed.secondary_ocr = _secondary_ocr_evidence(contents, failed, preprocess_options)
        return failed
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
    ocr_mode: str = DEFAULT_OCR_MODE,
) -> None:
    token = _active_progress_job.set(job_id)
    image_results: list[dict[str, Any]] = []
    try:
        total = len(images)
        preprocess_options = (
            _preprocess_options
            if ocr_mode == DEFAULT_OCR_MODE
            else forced_preprocess_options_for_mode(parse_ocr_mode(ocr_mode))
        )
        _report_analysis_update(12, "IMAGE_VALIDATION", f"Validating {total} uploaded images", imageCount=total)
        panel_hashes: list[dict[str, Any]] = []
        panel_authenticity: list[dict[str, Any]] = []
        for index, image in enumerate(images):
            image_number = index + 1
            start = 18 + round(index / total * 57)
            _report_analysis_update(start, "IMAGE_PREPROCESSING", f"Preparing image {image_number} of {total}", currentImage=image_number, currentSide=image["side"])
            source_sha256 = compute_image_sha256(image["contents"])
            authenticity = build_authenticity_evidence(image["contents"]).model_dump()
            image["sha256"] = source_sha256
            image["authenticity"] = authenticity
            panel_hashes.append({
                "side": image["side"],
                "fileName": image["filename"],
                "sha256": source_sha256,
            })
            panel_authenticity.append({
                "side": image["side"],
                "fileName": image["filename"],
                "evidence": authenticity,
            })
            if image.get("capture_quality_requested"):
                image["capture_assessment"] = _capture_assessment(
                    image["contents"], filename=image["filename"], side=image["side"],
                )
            else:
                image["capture_assessment"] = {
                    "status": "not_assessed",
                    "filename": image["filename"],
                    "side": image["side"],
                    "quality": {"acceptable": None, "reasons": ["No live-camera quality preflight was supplied for this image."]},
                }

            def image_progress(stage: str, image_number: int = image_number, side: str = image["side"]) -> None:
                offsets = {"IMAGE_PREPROCESSING": 0, "OBJECT_DETECTION": 10, "OCR_PROCESSING": 28}
                progress = 18 + round(index / total * 57) + offsets.get(stage, 0)
                _report_analysis_update(progress, stage, f"{ANALYSIS_STAGES[stage][1]}: image {image_number} of {total}", currentImage=image_number, currentSide=side)

            if ocr_mode == DEFAULT_OCR_MODE:
                result = _run_image_ocr(image["contents"], image["filename"], image_progress)
            else:
                result = _run_image_ocr(
                    image["contents"], image["filename"], image_progress,
                    preprocess_options=preprocess_options,
                )
            image["capture_assessment"]["quality"]["text_visibility"] = _capture_text_visibility(result)
            image_results.append({"image": image, "result": result})
            if not result.success:
                logger.warning("Image %d/%d failed: %s", image_number, total, result.error.message if result.error else "unknown error")

        successful = [item for item in image_results if item["result"].success]
        if not successful:
            secondary_candidates = [
                {"side": item["image"]["side"], "fileName": item["image"]["filename"], "result": item["result"].secondary_ocr}
                for item in image_results if item["result"].secondary_ocr is not None
            ]
            if secondary_candidates and job_id in _analysis_jobs:
                _analysis_jobs[job_id]["secondaryOcr"] = secondary_candidates
            _report_progress("OCR_PROCESSING", error="No uploaded image could be processed")
            return

        validation_profile, is_wholesale = _select_validation_profile(category)
        per_image_payloads: list[dict[str, Any]] = []
        for source_index, item in enumerate(image_results):
            image = item["image"]
            result = item["result"]
            report_id = f"RPT-{uuid.uuid4().hex[:12].upper()}"
            image_id = f"IMG-{uuid.uuid4().hex[:12].upper()}"
            if not result.success:
                per_image_payloads.append({
                    "reportId": report_id, "imageId": image_id, "sourceIndex": source_index,
                    "fileName": image["filename"], "side": image["side"],
                    "date": datetime.now(timezone.utc).isoformat(), "status": "REVIEW_REQUIRED",
                    "score": None, "ocr": result.model_dump(), "structuredExtraction": None,
                    "compliance": None, "sourceImageSha256": image.get("sha256"),
                    "error": result.error.model_dump() if result.error else {"code": "ocr_failed"},
                    "evidenceScope": "image_only",
                })
                continue
            image_ocr_dict = result.model_dump()
            image_normalized = normalize_ocr_result(image_ocr_dict)
            image_extraction = build_structured_extraction(image_ocr_dict, normalized=image_normalized)
            if is_wholesale:
                image_ocr_dict["package_type"] = "wholesale"
            image_compliance = _compliance_engine.evaluate(image_ocr_dict, validation_profile, structured_extraction=image_extraction)
            per_image_payloads.append(_image_report(
                scan_id="pending", report_id=report_id, image_id=image_id,
                image=image, result=result, structured_extraction=image_extraction,
                compliance=image_compliance, source_index=source_index,
            ))

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
        if is_wholesale:
            ocr_dict["package_type"] = "wholesale"
        compliance = _compliance_engine.evaluate(ocr_dict, validation_profile, structured_extraction=structured_extraction)
        _report_analysis_update(95, "RULE_VALIDATION", "Checking Legal Metrology rules", processedImages=len(successful), failedImages=total - len(successful))

        scan = _scan_document(combined, product_name.strip(), category.strip(), manufacturer.strip(), combined.image)
        font_measurements = _unavailable_font_measurements([d.model_dump() for d in combined.detections])
        physical_measurements = _experimental_measurement_evidence([d.model_dump() for d in combined.detections])
        secondary_candidates = [
            {"side": item["image"]["side"], "fileName": item["image"]["filename"], "result": item["result"].secondary_ocr}
            for item in image_results if item["result"].secondary_ocr is not None
        ]
        barcode_candidates = [
            {"side": item["image"]["side"], "fileName": item["image"]["filename"], "result": item["result"].barcode_decoding}
            for item in image_results
        ]
        scan.update({
            "userId": user["_id"], "success": True, "image": combined.image,
            "full_text": combined.full_text, "detections": [d.model_dump() for d in combined.detections],
            "detection_count": combined.detection_count, "preprocessing_applied": combined.preprocessing_applied,
            "error": None, "compliance": compliance.model_dump(), "status": _status_for_compliance(compliance.overall_status),
            "score": compliance.compliance_score, "structuredExtraction": structured_extraction.model_dump(),
            "imagesProcessed": len(successful), "imagesRequested": total,
            "panelHashes": panel_hashes,
            "ocrImageDimensions": [
                {"side": item["image"]["side"], "fileName": item["image"]["filename"], "dimensions": item["result"].image_dimensions}
                for item in image_results
            ],
            "panelAuthenticity": panel_authenticity,
            "captureAssessments": [image["capture_assessment"] for image in images],
            "pdpDetections": [
                {"side": item["image"]["side"], "fileName": item["image"]["filename"], "result": item["result"].pdp_detection}
                for item in image_results
                if item["result"].pdp_detection is not None
            ],
            "secondaryOcr": secondary_candidates,
            "barcodeDecoding": barcode_candidates,
            "fontHeightMeasurements": font_measurements["items"],
            "measurementReview": {key: value for key, value in font_measurements.items() if key != "items"},
            "physicalMeasurements": physical_measurements,
            "barcodeCalibration": {"status": "unavailable", "available": False, "scale_px_per_mm": None, "authoritative": False, "used_for_compliance": False, "reason": "No verified EAN-13 geometry and explicit physical reference dimension were supplied.", "evidence_region": None},
            "sourceMimeType": sniff_image_mime_type(successful[0]["image"]["contents"], successful[0]["image"]["content_type"]),
            "imageResults": [{"fileName": item["image"]["filename"], "side": item["image"]["side"], "success": item["result"].success, "detectionCount": item["result"].detection_count, "error": item["result"].error.model_dump() if item["result"].error else None} for item in image_results],
        })
        database = get_database()
        source_images = []
        for source_index, item in enumerate(image_results):
            image = item["image"]
            image_id = GridFS(database, collection="scan_images").put(image["contents"], filename=image["filename"], content_type=image["content_type"], scan_id=scan["id"], user_id=user["_id"])
            report = per_image_payloads[source_index]
            source_images.append({
                "id": str(image_id),
                "imageId": report["imageId"],
                "reportId": report["reportId"],
                "fileName": image["filename"],
                "side": image["side"],
                "detectionCount": item["result"].detection_count,
                "success": item["result"].success,
                "sha256": image["sha256"],
            })
        scan["sourceImages"] = source_images
        for source_index, report in enumerate(per_image_payloads):
            source_image = source_images[source_index]
            report["scanId"] = scan["id"]
            report["sourceImageId"] = source_image["imageId"]
            report["sourceImageUrl"] = f"/api/scans/{scan['id']}/images/{source_index}"
        scan["reports"] = per_image_payloads
        scan["reportIds"] = [report["reportId"] for report in per_image_payloads]
        if source_images:
            scan["sourceImageId"] = source_images[0]["id"]
            scan["sourceImageUrl"] = f"/api/scans/{scan['id']}/image"
            for source_index, source_image in enumerate(source_images):
                source_image["url"] = f"/api/scans/{scan['id']}/images/{source_index}"

            gs1_reports: list[dict[str, Any]] = []
            gs1_artifacts: dict[str, Any] = {}
            for source_index, item in enumerate(image_results):
                report = per_image_payloads[source_index]
                image = item["image"]
                result = item["result"]
                gs1_verification, gs1_crops = _attach_gs1_verification(
                    database=database,
                    scan_id=scan["id"],
                    image_id=report["imageId"],
                    report_id=report["reportId"],
                    user_id=user["_id"],
                    image_bytes=image["contents"],
                    ocr_result=result,
                    structured_extraction=report.get("structuredExtraction"),
                )
                report["gs1Verification"] = gs1_verification
                gs1_reports.append(gs1_verification)
                gs1_artifacts[f"gs1_verification_{report['reportId']}"] = {
                    "artifact_type": "gs1_verification_result",
                    "value": gs1_verification,
                    "source": "local_gs1_verification",
                    "derived_from": [f"source_image_{source_index}"],
                    "metadata": {"scan_id": scan["id"], "image_id": report["imageId"], "report_id": report["reportId"]},
                }
                for crop in gs1_crops:
                    gs1_artifacts[f"gs1_barcode_crop_{report['reportId']}_{crop['barcode_id']}"] = {
                        "artifact_type": "gs1_barcode_crop",
                        "value": crop["contents"],
                        "source": "barcode_region_crop",
                        "derived_from": [f"source_image_{source_index}"],
                        "metadata": {key: crop[key] for key in ("image_id", "report_id", "barcode_id", "evidence_id", "sha256")},
                    }
            scan["gs1Verifications"] = gs1_reports

        upload_audit = append_audit_event(
            database,
            event_type="upload",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_scan_uploaded",
            file_name=combined.image,
            details={"panelHashes": panel_hashes, "imageCount": total},
        )
        authenticity_audit = append_audit_event(
            database,
            event_type="image_authenticity_analysis",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_integrity_evidence_collected",
            file_name=combined.image,
            details={"panelAuthenticity": panel_authenticity, "is_compliance_verdict": False},
        )
        capture_audit = append_audit_event(
            database,
            event_type="capture_evaluation",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_capture_assessments_recorded",
            file_name=combined.image,
            details={"capture_assessments": scan["captureAssessments"], "compliance_affected": False},
        )
        measurement_audit = append_audit_event(
            database, event_type="experimental_measurement", entity_type="scan", entity_id=scan["id"],
            user_id=user["_id"], actor_role=user.get("role"), action="physical_measurement_review_required",
            file_name=combined.image,
            details={"status": physical_measurements["status"], "measurement_count": len(physical_measurements["items"]), "calibration_status": scan["barcodeCalibration"]["status"], "authoritative": False, "used_for_compliance": False},
        )
        barcode_audit = append_audit_event(
            database,
            event_type="barcode_decoding",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_barcode_decoding_recorded",
            file_name=combined.image,
            details={
                "panels": [{"side": item.get("side"), "source_image_sha256": (item.get("result") or {}).get("source_image_sha256"), "status": (item.get("result") or {}).get("status"), "detections": len((item.get("result") or {}).get("detections", []))} for item in barcode_candidates],
                "authoritative": False,
                "used_for_compliance": False,
            },
        )
        pdp_audit = append_audit_event(
            database,
            event_type="experimental_pdp_detection",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_candidates_recorded",
            file_name=combined.image,
            details={"panel_detections": scan["pdpDetections"], "model_hashes": sorted({item.get("result", {}).get("model_hash") for item in scan["pdpDetections"] if isinstance(item.get("result"), dict) and item.get("result", {}).get("model_hash")}), "capability_states": sorted({item.get("result", {}).get("capability_state") for item in scan["pdpDetections"] if isinstance(item.get("result"), dict)}), "production_authority": False, "used_for_compliance": False},
        ) if scan["pdpDetections"] else None
        secondary_ocr_audit = append_audit_event(
            database,
            event_type="secondary_ocr_run",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_secondary_ocr_recorded",
            file_name=combined.image,
            details={"panels": secondary_candidates, "used_for_compliance": False, "engine": "tesseract", "disagreement_count": sum(len(((panel.get("result") or {}).get("comparison") or {}).get("disagreements", [])) for panel in secondary_candidates if isinstance(panel.get("result"), dict))},
        ) if secondary_candidates else None
        ocr_audit = append_audit_event(
            database,
            event_type="ocr_run",
            entity_type="scan",
            entity_id=scan["id"],
            user_id=user["_id"],
            actor_role=user.get("role"),
            action="multi_image_ocr_completed",
            file_name=combined.image,
            details={"engine": "paddleocr", "detection_count": combined.detection_count, "images_processed": len(successful), "images_requested": total},
        )
        package = assemble_evidence_package_from_scan(
            evidence_id=scan["id"],
            ocr_result=ocr_dict,
            compliance_result=compliance.model_dump(),
            audit_refs=[event["eventHash"] for event in (upload_audit, authenticity_audit, ocr_audit, capture_audit, measurement_audit, barcode_audit, pdp_audit, secondary_ocr_audit) if event is not None and event.get("eventHash")],
            scan_evidence=scan,
            source_images=[{
                "contents": image["contents"],
                "file_name": image["filename"],
                "mime_type": sniff_image_mime_type(image["contents"], image["content_type"]),
                "sha256": image["sha256"],
                "side": image["side"],
                "authenticity": image["authenticity"],
                "capture_assessment": image["capture_assessment"],
            } for image in images],
            supporting_artifacts=gs1_artifacts,
        )
        package_audit = record_evidence_package(database, package, entity_id=scan["id"], entity_type="scan")
        scan["evidencePackageId"] = package.package_id
        scan["evidenceMerkleRoot"] = package.merkle_root
        scan["evidencePackageAuditHash"] = package_audit["eventHash"]
        database.scans.insert_one(scan)
        database.history.insert_one({"userId": user["_id"], "actionType": "scan_uploaded", "title": "Product label uploaded", "description": f"Multi-image OCR scan created for {scan['productName']}.", "documentId": scan["id"], "metadata": {"fileName": combined.image, "category": scan["category"], "imagesProcessed": len(successful)}, "createdAt": datetime.now(timezone.utc)})
        _report_analysis_update(98, "REPORT_GENERATION", "Generating unified compliance report", processedImages=len(successful), failedImages=total - len(successful))
        _report_progress("COMPLETED", result=_public_scan(scan))
    except Exception as exc:
        logger.exception("Multi-image analysis job %s failed", job_id)
        job = _analysis_jobs.get(job_id)
        if job is not None:
            _report_progress(job.get("stage", "REPORT_GENERATION"), error=str(exc))
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
    capture_quality_requested: str = Form("[]"),
    ocr_mode: str = Form(DEFAULT_OCR_MODE),
    user: dict[str, Any] = Depends(official_user),
):
    if not file:
        raise HTTPException(status_code=400, detail="No file was uploaded")
    if len(file) > MAX_ANALYSIS_IMAGES:
        raise HTTPException(status_code=400, detail=f"Upload up to {MAX_ANALYSIS_IMAGES} images per product")
    try:
        resolved_ocr_mode = parse_ocr_mode(ocr_mode)
    except InvalidOCRModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        requested_sides = __import__("json").loads(image_sides)
    except (TypeError, ValueError):
        requested_sides = []
    try:
        capture_quality_requests = __import__("json").loads(capture_quality_requested)
    except (TypeError, ValueError):
        capture_quality_requests = []
    images = []
    seen_names = set()
    for index, upload in enumerate(file):
        filename = Path(upload.filename or "").name
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported image format for {filename}")
        if filename.casefold() in seen_names:
            continue
        contents = await read_upload_limited(upload)
        if not contents:
            raise HTTPException(status_code=400, detail=f"Uploaded file is empty: {filename}")
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail=f"File exceeds the 15MB limit: {filename}")
        seen_names.add(filename.casefold())
        side = requested_sides[index] if index < len(requested_sides) and requested_sides[index] in {"Front", "Back", "Left Side", "Right Side", "Top", "Bottom", "Other"} else "Other"
        images.append({
            "contents": contents,
            "filename": filename,
            "content_type": upload.content_type,
            "side": side,
            "capture_quality_requested": bool(capture_quality_requests[index]) if index < len(capture_quality_requests) else False,
        })
    if not images:
        raise HTTPException(status_code=400, detail="No supported image was uploaded")

    job_id = f"AN-{uuid.uuid4().hex[:12].upper()}"
    _analysis_jobs[job_id] = {"id": job_id, "userId": user["_id"], "status": "running", "stage": "INITIALIZING", "progress": 10, "label": "Initializing analysis", "imageCount": len(images)}
    if len(images) == 1:
        image = images[0]
        background_tasks.add_task(
            _run_analysis_job_in_worker,
            job_id,
            image["contents"],
            image["filename"],
            image["content_type"],
            product_name,
            category,
            manufacturer,
            user,
            resolved_ocr_mode,
            image["side"],
            image["capture_quality_requested"],
        )
    else:
        background_tasks.add_task(
            _run_multi_analysis_job_in_worker,
            job_id,
            images,
            product_name,
            category,
            manufacturer,
            user,
            resolved_ocr_mode,
        )
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
        content=jsonable_encoder([_public_scan(scan) for scan in scans]),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/api/scans/{scan_id}")
def get_scan(scan_id: str, user: dict[str, Any] = Depends(official_user)):
    """Return one persisted scan by its public ID."""
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"_id": 0})
    if scan is None:
        return JSONResponse(status_code=404, content={"detail": "Scan not found"})
    return JSONResponse(
        content=jsonable_encoder(_public_scan(scan)),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/api/scans/{scan_id}/reports/{report_id}")
def get_scan_report(scan_id: str, report_id: str, user: dict[str, Any] = Depends(official_user)):
    """Return exactly one image-scoped report; never fall back to scan summary."""
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"reports": 1})
    report = next((item for item in (scan or {}).get("reports", []) if item.get("reportId") == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return JSONResponse(content=jsonable_encoder(report), headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@router.post("/api/scans/{scan_id}/calibration")
def submit_scan_calibration(
    scan_id: str,
    request: ScanCalibrationRequest = Body(...),
    user: dict[str, Any] = Depends(official_user),
):
    """Attach operator-verified EAN-13/reference calibration as supplemental evidence.

    Multi-image scans are refused because their persisted combined OCR lines do
    not identify which source image each line came from; applying one image's
    scale to another image would be an unauditable geometry mismatch.
    """
    database = get_database()
    scan = database.scans.find_one({"id": scan_id, "userId": user["_id"]})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.get("sourceImages"):
        raise HTTPException(status_code=409, detail="Calibration currently requires a single-source-image scan with source-linked OCR geometry")
    if not scan.get("sourceImageId"):
        raise HTTPException(status_code=409, detail="Calibration source image is unavailable")
    try:
        source_file = GridFS(database, collection="scan_images").get(ObjectId(scan["sourceImageId"]))
        source_bytes = source_file.read(MAX_UPLOAD_BYTES + 1)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Calibration source image is unavailable") from exc
    if not source_bytes or len(source_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Calibration source image exceeds the supported size")
    source_image_sha256 = compute_image_sha256(source_bytes)
    recorded_source_sha256 = scan.get("sourceImageSha256")
    if recorded_source_sha256 and recorded_source_sha256.lower() != source_image_sha256:
        raise HTTPException(status_code=409, detail="Calibration source image does not match the scan's recorded digest")
    try:
        with Image.open(BytesIO(source_bytes)) as source_image:
            source_image.verify()
        with Image.open(BytesIO(source_bytes)) as source_image:
            image_shape = (source_image.height, source_image.width)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Calibration source image is invalid") from exc
    ocr_dimensions = scan.get("ocrImageDimensions")
    if not isinstance(ocr_dimensions, dict):
        raise HTTPException(status_code=409, detail="OCR detection coordinate dimensions are unavailable for this scan")
    try:
        ocr_width = int(ocr_dimensions["width"])
        ocr_height = int(ocr_dimensions["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="OCR detection coordinate dimensions are invalid") from exc
    if ocr_width <= 0 or ocr_height <= 0:
        raise HTTPException(status_code=409, detail="OCR detection coordinate dimensions are invalid")
    source_height, source_width = image_shape
    scale_x, scale_y = ocr_width / source_width, ocr_height / source_height

    def source_to_ocr_polygon(points: list[list[float]]) -> list[list[float]]:
        return [[float(point[0]) * scale_x, float(point[1]) * scale_y] for point in points]

    reference_hash = request.reference_sha256.lower()
    if any(character not in "0123456789abcdef" for character in reference_hash):
        raise HTTPException(status_code=422, detail="reference_sha256 must be a hexadecimal SHA-256 digest")
    verification_status = request.reference_verification_status.strip().lower()
    if verification_status not in {"verified", "unverified"}:
        raise HTTPException(status_code=422, detail="reference_verification_status must be verified or unverified")
    reference_bytes = None
    if request.reference_file_base64 is not None:
        if not request.reference_file_name or not request.reference_file_mime_type:
            raise HTTPException(status_code=422, detail="Reference document name and MIME type are required")
        if request.reference_file_mime_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp", "text/plain"}:
            raise HTTPException(status_code=415, detail="Unsupported physical-reference document type")
        try:
            reference_bytes = base64.b64decode(request.reference_file_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="Reference document encoding is invalid") from exc
        if not reference_bytes or len(reference_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Reference document must be between 1 byte and 5 MB")
        if request.reference_file_mime_type == "application/pdf" and not reference_bytes.startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail="Reference document bytes are not a PDF")
        if request.reference_file_mime_type == "text/plain":
            try:
                reference_bytes.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=415, detail="Text reference document is not valid UTF-8") from exc
        if request.reference_file_mime_type.startswith("image/"):
            sniffed_reference_type = sniff_image_mime_type(reference_bytes)
            if sniffed_reference_type != request.reference_file_mime_type:
                raise HTTPException(status_code=415, detail="Reference document MIME type does not match its image bytes")
        if compute_image_sha256(reference_bytes) != reference_hash:
            raise HTTPException(status_code=409, detail="Reference document SHA-256 does not match its uploaded bytes")
    if verification_status == "verified" and reference_bytes is None:
        raise HTTPException(status_code=422, detail="A verified physical reference requires its source document")
    unit_factors = {"mm": 1.0, "cm": 10.0, "in": 25.4}
    unit = request.reference_unit.strip().lower()
    if unit not in unit_factors:
        raise HTTPException(status_code=422, detail="reference_unit must be mm, cm, or in")
    calibration_polygon = source_to_ocr_polygon(request.polygon)
    if request.barcode_confidence is None:
        decode_record = scan.get("barcodeDecoding") or {}
        detections = decode_record.get("detections", []) if isinstance(decode_record, dict) else []
        decoded = next((item for item in detections if item.get("code") == request.code.strip()
                        and item.get("symbology") == "EAN_13" and item.get("checksum_valid") is True), None)
        if decoded is None or decoded.get("source_image_sha256") != source_image_sha256:
            raise HTTPException(status_code=409, detail="A matching checksum-validated EAN-13 decode is not stored for this source image")
        if decode_record.get("source_image_dimensions") != {"width": source_width, "height": source_height}:
            raise HTTPException(status_code=409, detail="Stored EAN-13 image dimensions do not match the original scan")
        decoded_polygon = decoded.get("polygon")
        if not decoded_polygon:
            raise HTTPException(status_code=409, detail="Stored EAN-13 decode has no source-image geometry")
        calibration_polygon = source_to_ocr_polygon(decoded_polygon)
        reference_evidence = {
            **decoded, "value": request.code.strip(), "confidence": None,
            "region_id": decoded.get("detection_id"), "source_image_sha256": source_image_sha256,
        }
    else:
        reference_evidence = {
            "value": request.code.strip(), "confidence": request.barcode_confidence,
            "region_id": "barcode_reference_region", "source": "official_review_submission",
            "source_image_sha256": source_image_sha256,
        }
    calibration = calibrate_ean13_scale(
        code=request.code.strip(), polygon=calibration_polygon, image_shape=image_shape,
        reference_width_mm=None,
        reference_source=None,
        evidence=reference_evidence,
    )
    calibration.update({
        "status": "review_required",
        "acceptable": False,
        "scale_px_per_mm": None,
        "reasons": ["A decoded barcode's dimensions do not establish physical scale. Use a registered physical reference with source-linked geometry and uncertainty."],
    })
    calibration.setdefault("reference", {}).update({
        "reference_id": request.reference_id, "reference_sha256": reference_hash,
        "input_value": request.reference_width, "input_unit": unit,
        "verification_status": verification_status,
        "verification_method": "authenticated_official_submission",
    })
    calibration["coordinate_transform"] = {
        "source_image_dimensions": {"width": source_width, "height": source_height},
        "ocr_detection_dimensions": {"width": ocr_width, "height": ocr_height},
        "source_to_ocr_scale": {"x": scale_x, "y": scale_y},
        "method": "independent_axis_scale_to_ocr_pixel_frame",
    }
    reference_file_id = None
    if reference_bytes is not None:
        reference_file_id = GridFS(database, collection="calibration_reference_files").put(
            reference_bytes,
            filename=request.reference_file_name,
            content_type=request.reference_file_mime_type,
            scan_id=scan_id,
            reference_id=request.reference_id,
            sha256=reference_hash,
        )
        calibration["reference"]["file_id"] = str(reference_file_id)
    calibration.update({"authoritative": False, "used_for_compliance": False,
                        "source_image_sha256": reference_evidence["source_image_sha256"],
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "recorded_by": str(user["_id"]), "status_classification": "EXPERIMENTAL" if calibration.get("status") == "calibrated" else "REVIEW_REQUIRED"})

    font_measurements = []
    physical_measurements = []
    for index, detection in enumerate(scan.get("detections", [])[:10000]):
        region_id = f"region_{index}"
        font_measurements.append({"detection_id": region_id, **measure_ocr_detection(
            detection=detection, calibration=calibration, geometry=None, minimum_font_height_mm=None,
            evidence_region_ids=[region_id], image_shape=image_shape,
        )})
        physical_measurements.append({"measurement_id": f"measurement_{index}", **measure_ocr_dimensions(
            detection, calibration=calibration, evidence_region_ids=[region_id],
        )})
    calibration_id = uuid.uuid4().hex
    event = append_audit_event(database, event_type="barcode_calibration", entity_type="scan", entity_id=scan_id,
        user_id=user["_id"], actor_role=user.get("role"), action=calibration["status_classification"],
        source_image_sha256=reference_evidence["source_image_sha256"], details={
            "calibration": calibration, "font_measurement_count": len(font_measurements),
            "physical_measurement_count": len(physical_measurements), "authoritative": False, "used_for_compliance": False,
        })
    calibration_artifacts = {
        "source_image": {"artifact_type": "source_image", "value": source_bytes, "source": "original_upload", "metadata": {"sha256": reference_evidence["source_image_sha256"]}},
        "barcode_calibration": {"artifact_type": "barcode_calibration_evidence", "value": calibration, "source": "official_review_submission", "derived_from": ["source_image"]},
        "physical_measurements": {"artifact_type": "physical_measurement_evidence", "value": physical_measurements, "source": "calibration", "derived_from": ["source_image", "barcode_calibration"]},
        "font_height_measurements": {"artifact_type": "font_height_measurement_evidence", "value": font_measurements, "source": "paddleocr_geometry", "derived_from": ["source_image", "barcode_calibration"]},
        "reference_identity": {"artifact_type": "physical_reference_identity", "value": {"reference_id": request.reference_id, "sha256": reference_hash, "source": request.reference_source, "status": verification_status}, "source": "official_review_submission"},
    }
    if reference_bytes is not None:
        calibration_artifacts["physical_reference_document"] = {
            "artifact_type": "physical_reference_document",
            "value": reference_bytes,
            "source": "authenticated_official_upload",
            "metadata": {"file_name": request.reference_file_name, "mime_type": request.reference_file_mime_type, "sha256": reference_hash, "file_id": str(reference_file_id)},
        }
    package = build_evidence_package(
        evidence_id=f"{scan_id}:calibration:{calibration_id}",
        raw_artifacts=calibration_artifacts,
        audit_refs=[event["eventHash"]],
    )
    package_audit = record_evidence_package(database, package, entity_id=scan_id, entity_type="scan")
    database.scans.update_one({"_id": scan["_id"]}, {"$set": {
        "barcodeCalibration": calibration,
        "fontHeightMeasurements": font_measurements,
        "measurementReview": {"status": "review_required", "available": calibration.get("status") == "calibrated", "threshold_status": "unavailable", "reason": "No authoritative legal font-height threshold is configured.", "used_for_compliance": False},
        "physicalMeasurements": {"status": "experimental_measurement" if calibration.get("status") == "calibrated" else "review_required", "available": calibration.get("status") == "calibrated", "items": physical_measurements, "used_for_compliance": False},
        "calibrationReferenceFileId": str(reference_file_id) if reference_file_id else None,
        "supplementalEvidencePackageIds": list(set(scan.get("supplementalEvidencePackageIds", []) + [package.package_id])),
        "updatedAt": datetime.now(timezone.utc),
    }})
    package_audit["package_id"] = package.package_id
    return {"success": True, "status": calibration["status_classification"], "calibration": calibration,
            "fontHeightMeasurements": font_measurements, "physicalMeasurements": physical_measurements,
            "evidencePackageId": package.package_id, "auditEventHash": event["eventHash"]}


@router.get("/api/scans/{scan_id}/calibration/reference")
def get_scan_calibration_reference(scan_id: str, user: dict[str, Any] = Depends(official_user)):
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"calibrationReferenceFileId": 1, "barcodeCalibration": 1})
    if scan is None or not scan.get("calibrationReferenceFileId"):
        raise HTTPException(status_code=404, detail="Physical reference document not found")
    try:
        reference = GridFS(get_database(), collection="calibration_reference_files").get(ObjectId(scan["calibrationReferenceFileId"]))
        contents = reference.read(5 * 1024 * 1024 + 1)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Physical reference document not found") from exc
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Stored physical reference exceeds the supported size")
    expected_hash = ((scan.get("barcodeCalibration") or {}).get("reference") or {}).get("reference_sha256")
    if not expected_hash or compute_image_sha256(contents) != expected_hash:
        raise HTTPException(status_code=409, detail="Stored physical reference digest does not match calibration evidence")
    file_metadata = getattr(reference, "_file", {}) or {}
    media_type = file_metadata.get("contentType") or file_metadata.get("content_type") or "application/octet-stream"
    return StreamingResponse(
        BytesIO(contents),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{Path(reference.filename or "reference").name}"'},
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
    gs1_crop_files = database["scan_images.files"].find({
        "metadata.scan_id": scan_id,
        "metadata.user_id": str(user["_id"]),
        "metadata.artifact_type": "gs1_barcode_crop",
    }, {"_id": 1})
    crop_store = GridFS(database, collection="scan_images")
    for crop_file in gs1_crop_files:
        try:
            crop_store.delete(crop_file["_id"])
        except NoFile:
            pass
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


@router.get("/api/scans/{scan_id}/gs1-evidence/{file_id}")
def get_gs1_barcode_evidence(
    scan_id: str,
    file_id: str,
    report_id: str,
    user: dict[str, Any] = Depends(official_user),
):
    """Stream a barcode crop only to the owner of its matching image report."""
    database = get_database()
    scan = database.scans.find_one({"id": scan_id, "userId": user["_id"]}, {"reports": 1})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    report = next((item for item in scan.get("reports", []) if item.get("reportId") == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        crop = GridFS(database, collection="scan_images").get(ObjectId(file_id))
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Barcode evidence not found") from exc
    metadata = (getattr(crop, "_file", {}) or {}).get("metadata") or {}
    if (
        metadata.get("artifact_type") != "gs1_barcode_crop"
        or metadata.get("scan_id") != scan_id
        or metadata.get("user_id") != str(user["_id"])
        or metadata.get("report_id") != report_id
        or metadata.get("image_id") != report.get("imageId")
    ):
        raise HTTPException(status_code=404, detail="Barcode evidence not found")
    return StreamingResponse(crop, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/cases")
def list_cases(user: dict[str, Any] = Depends(official_user)):
    """Return persisted cases newest first."""
    cases = get_database().cases.find({"userId": user["_id"]}, {"_id": 0}).sort("date", -1)
    return [{**case, "userId": str(case["userId"])} if case.get("userId") is not None else case for case in cases]
