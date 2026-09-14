"""
LegalLense end-to-end regression benchmark.

Diagnostic-only: reads real product photos from training/raw_images/ (and
optionally a pre-captured OCR JSON fixture) and runs them through the
EXISTING, UNMODIFIED production pipeline -
PaddleOCRService.run() -> normalize_ocr_result() -> build_structured_extraction()
-> ComplianceEngine.evaluate() - recording per-stage timings and comparing
detection outcomes against ground truth transcribed from
training/rejected_images/QC_LOG.md (the only place in this project that
already documents, per real image, which declarations are actually legible).

This script imports production code READ-ONLY (it never patches, mocks, or
alters compliance_engine.py / structured_extraction.py / paddle_ocr_service.py
behavior) and calls Gemini/YOLO NEVER - build_structured_extraction is called
with gemini_suggestions=None and PaddleOCRService.run() is called without a
yolo_service, exactly like the existing deterministic-only test suite does.
It is not part of the application and is not imported by any production
module - safe to delete without affecting the app.

Usage:
    backend\\.venv311\\Scripts\\python.exe training\\benchmark_e2e.py
    backend\\.venv311\\Scripts\\python.exe training\\benchmark_e2e.py --fixtures Handwash_back.jpg Ferrero_back.jpg
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

TRAINING_DIR = Path(__file__).resolve().parent
PORTAL_DIR = TRAINING_DIR.parent
RAW_IMAGES_DIR = TRAINING_DIR / "raw_images"
RESULTS_DIR = TRAINING_DIR / "benchmark_results"

sys.path.insert(0, str(PORTAL_DIR))  # so `backend....` imports resolve regardless of cwd

# Must run BEFORE any backend.* import below - backend/ocr/gemini_service.py
# (imported a few lines down) reads GEMINI_API_KEY/GEMINI_ENABLED/etc. via
# os.getenv(...) at MODULE level, once, at import time - the exact same
# pitfall backend/main.py's own load_dotenv() call already documents and
# fixes for the real app ("if .env is loaded after those imports run,
# those constants permanently bake in ... silently ignoring .env for the
# lifetime of the process"). This script never went through main.py, so it
# needs the identical fix here for --gemini to see a real API key at all.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PORTAL_DIR / ".env")

from backend.ocr.paddle_ocr_service import PaddleOCRService  # noqa: E402
from backend.ocr.preprocessing import PreprocessOptions, preprocess_image  # noqa: E402
# Accuracy Fix #7 (Gemini experiment): reuses the EXISTING production Gemini
# fallback path verbatim (find_ambiguous_fields -> GeminiService.suggest_fields
# -> apply_suggestions_to_ocr_result), the exact same sequence
# backend/api/ocr.py's create_scan() already uses - no new VLM abstraction,
# no duplicated logic. Only ever invoked when --gemini is explicitly passed
# (see main()) - the default `python training/benchmark_e2e.py` invocation
# never imports or calls any of this, so normal benchmark behavior is
# byte-for-byte unchanged.
from backend.ocr.gemini_service import GEMINI_API_KEY, GeminiService, apply_suggestions_to_ocr_result, find_ambiguous_fields  # noqa: E402
from backend.services.compliance_engine import ComplianceEngine, normalize_ocr_result  # noqa: E402
from backend.services.structured_extraction import build_structured_extraction  # noqa: E402

# --- fixture ground truth -----------------------------------------------
# Transcribed directly from training/rejected_images/QC_LOG.md's per-image
# notes (the "Session: 2026-09-10" table) - the only existing, already-
# human-verified record of what is actually legible on each real photo in
# this project. "legible" / "blank" reproduce that log's own wording; a
# field absent from a given image's dict means the log did not comment on
# it either way (kept as GROUND_TRUTH_UNAVAILABLE, never guessed).
#
# Field keys match compliance_engine.py's ALIASES / LMPC-R6 required_fields
# names exactly, so results can be compared without translation.
FIXTURES: list[dict[str, Any]] = [
    {
        "file": "Handwash_back.jpg",
        "scenarios": ["clear_ideal"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "maximum_retail_price_mrp": "legible", "unit_sale_price": "legible",
            "net_quantity": "legible", "manufacturer_packer_importer_details": "legible",
            "month_and_year_of_manufacture_or_packing": "legible", "consumer_care_details": "legible",
        },
        "notes": "QC_LOG: real hand-held photo; excellent coverage, all core declarations legible.",
    },
    {
        "file": "Maggi_back.jpg",
        "scenarios": ["small_text_low_resolution"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "manufacturer_packer_importer_details": "legible", "consumer_care_details": "legible",
            "net_quantity": "legible",
        },
        "notes": "QC_LOG: low resolution (374x534) but text still readable at full zoom.",
    },
    {
        "file": "Ferrero_back.jpg",
        "scenarios": ["glare_reflection", "imported_product"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "country_of_origin": "legible", "manufacturer_packer_importer_details": "legible",
            "net_quantity": "legible",
        },
        "notes": "QC_LOG: heavy glare/reflection streak obscures part of the text; country_of_origin (ITALY - imported) remains legible.",
    },
    {
        "file": "DarkFantasy_back.jpg",
        "scenarios": ["dense_declarations"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "month_and_year_of_manufacture_or_packing": "legible", "maximum_retail_price_mrp": "legible",
            "manufacturer_packer_importer_details": "legible", "consumer_care_details": "legible",
            "net_quantity": "legible",
        },
        "notes": "QC_LOG: embossed/stamped codes (batch, dates, price) moderately legible.",
    },
    {
        "file": "Pears_back.jpg",
        "scenarios": ["multiple_price_numbers"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "manufacturer_packer_importer_details": "legible", "consumer_care_details": "legible",
            "net_quantity": "legible", "maximum_retail_price_mrp": "legible", "country_of_origin": "legible",
        },
        "notes": "QC_LOG: excellent coverage, MRP includes a legible revised-MRP strikethrough (real multi-price case).",
    },
    {
        "file": "Chips_back.jpg",
        "scenarios": ["baseline"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "maximum_retail_price_mrp": "legible", "manufacturer_packer_importer_details": "legible",
            "consumer_care_details": "legible",
        },
        "notes": "QC_LOG: real hand-held photo; mrp (Rs.10.00), entity_details, consumer_care legible.",
    },
    {
        "file": "Chips_nutrition.jpg",
        "scenarios": ["multiple_quantities_candidate", "domestic_product"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {"country_of_origin": "legible"},
        "notes": "QC_LOG: close-up companion crop of Chips_back.jpg; reveals legible country_of_origin ('PRODUCT OF INDIA') not readable in the wider shot. Likely contains nutrition-table gram values near net_quantity - verified empirically below, not assumed.",
    },
    {
        "file": "Facewash_back.jpg",
        "scenarios": ["consumer_care_multiline", "elongated_aspect_ratio"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "maximum_retail_price_mrp": "legible", "unit_sale_price": "legible",
            "month_and_year_of_manufacture_or_packing": "legible", "net_quantity": "legible",
            "manufacturer_packer_importer_details": "legible", "consumer_care_details": "legible",
        },
        "notes": "QC_LOG: extremely elongated aspect ratio (flagged in the log for pipeline handling); excellent coverage otherwise.",
    },
    {
        "file": "Cookie_back.jpg",
        "scenarios": ["domestic_product"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "manufacturer_packer_importer_details": "legible", "consumer_care_details": "legible",
            "net_quantity": "legible", "country_of_origin": "legible",
        },
        "notes": "QC_LOG: entity_details, consumer_care, net_quantity, country_of_origin ('MADE IN INDIA') legible.",
    },
    {
        "file": "Salt_back.jpg",
        "scenarios": ["evidence_contamination_risk"],
        "profile": "e_commerce_product_listing",
        "ground_truth": {
            "manufacturer_packer_importer_details": "legible", "net_quantity": "legible",
            "maximum_retail_price_mrp": "legible", "consumer_care_details": "legible",
        },
        "notes": "QC_LOG: cross-promotional panel for other variants nearby - flagged in the log as a risk of being boxed as this product's own fields. Real false-evidence-attachment risk case.",
    },
]

# Categories explicitly requested by the task that this project's current
# real-fixture pool cannot (or cannot yet confidently) cover. Recorded here
# rather than fabricated fixtures/results for them.
UNCOVERED_CATEGORIES = {
    "curved_packaging": "NOT_AVAILABLE",
    "low_contrast": "INSUFFICIENT_DATA",
    "wholesale_package": "NOT_AVAILABLE",
}
# curved_packaging / low_contrast: no image in training/raw_images/ or
# training/rejected_images/ is documented (QC_LOG.md) or filenamed as
# curved-packaging or low-contrast specifically - guessing from pixels
# without a documented label would be inventing benchmark ground truth.
# wholesale_package: every raw_images photo is a single retail consumer
# package; LMPC-R24-WHOLESALE-DECLARATIONS has no real photographed
# fixture anywhere in this project (confirmed during the prior P1 task -
# its own tests all use synthetic package_type="wholesale" JSON, never a
# real photo).

# Amount-like numbers (₹/Rs currency-prefixed OR "N.NN" decimal shape) -
# used only to COUNT how many distinct price-shaped strings appear in the
# OCR text, to derive "multiple_price_numbers" empirically rather than by
# assumption.
_PRICE_LIKE_PATTERN = re.compile(r"(?:₹|Rs\.?|INR)\s*[0-9]+(?:[.,][0-9]{1,2})?|\b[0-9]{1,4}\.[0-9]{2}\b", re.I)
# Weight/volume-shaped numbers (reuses the same unit vocabulary as
# compliance_engine.py's _QUANTITY_PATTERN, duplicated here only because
# that pattern is a private module attribute not meant for external
# import - kept in sync manually, benchmark-only, never used to make a
# compliance decision).
_QUANTITY_LIKE_PATTERN = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(?:kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b",
    re.I,
)

CORE_FIELDS = [
    "maximum_retail_price_mrp", "unit_sale_price", "net_quantity",
    "manufacturer_packer_importer_details", "month_and_year_of_manufacture_or_packing",
    "country_of_origin", "consumer_care_details",
]


def _classify_field(field_name: str, ground_truth: Optional[str], detected: bool, status: str) -> str:
    """Compare ground truth (from QC_LOG.md) against the actual pipeline
    outcome for one field. Deliberately conservative: only ever asserts
    CORRECT/FALSE_* when ground truth is an explicit "legible"/"blank" note
    from the log; otherwise GROUND_TRUTH_UNAVAILABLE - never inferred from
    the pipeline's own output, which would be circular."""
    if ground_truth is None:
        return "GROUND_TRUTH_UNAVAILABLE"
    if ground_truth == "legible":
        if detected:
            return "CORRECT"
        return "FALSE_NOT_DETECTED"  # OCR/extraction missed a declaration a human confirmed is readable
    if ground_truth == "blank":
        # The log confirms THIS PHOTO shows no printed value for this field.
        # The engine cannot distinguish "genuinely blank" from "OCR missed
        # it" from a single photo either - by design (see compliance_engine.py's
        # NEEDS_MANUAL_VERIFICATION philosophy) it must never claim a
        # CONFIRMED violation from non-detection, so NEEDS_MANUAL_VERIFICATION
        # here is the intended, correct outcome, not a defect.
        if status == "NEEDS_MANUAL_VERIFICATION":
            return "LEGITIMATE_REVIEW"
        if status == "PASS":
            return "FALSE_PASS"  # detected something on a value area the log confirms is blank
        return "GROUND_TRUTH_UNAVAILABLE"
    return "GROUND_TRUTH_UNAVAILABLE"


def _failure_source(field_name: str, ocr_full_text: str, normalized_field, extraction_detected: bool, extraction_confidence: float) -> list[str]:
    """Best-effort localization for a FALSE_NOT_DETECTED field: did OCR ever
    produce text plausibly related to this field at all? If not, the miss
    starts at OCR. If OCR text exists but normalize_ocr_result's ALIASES
    match still didn't fire (or fired with no usable value), the miss is in
    extraction/normalization, not OCR itself. Deliberately does not blame
    the compliance rule engine, which never sees raw text - only whatever
    normalize_ocr_result already resolved."""
    keyword_hints = {
        "maximum_retail_price_mrp": ("mrp", "maximum retail price", "₹", "rs."),
        "unit_sale_price": ("per kg", "per g", "/kg", "/g", "/litre", "/l", "unit sale price"),
        "net_quantity": ("net", "wt", "weight", "qty", "quantity"),
        "manufacturer_packer_importer_details": ("manufactur", "packer", "packed by", "marketed"),
        "month_and_year_of_manufacture_or_packing": ("mfg", "manufactur", "packed on", "packing date"),
        "country_of_origin": ("origin", "made in", "product of"),
        "consumer_care_details": ("consumer", "customer care", "helpline", "toll free"),
    }
    lowered = ocr_full_text.lower()
    hints = keyword_hints.get(field_name, ())
    ocr_has_keyword = any(hint in lowered for hint in hints)
    sources: list[str] = []
    if not ocr_has_keyword:
        sources.append("OCR")  # no plausible trigger text anywhere in the recognized text at all
    elif not extraction_detected:
        sources.append("Extraction")  # OCR read something plausible, but normalize/extract still didn't resolve a value
    else:
        sources.append("Confidence")  # resolved, but apparently still didn't satisfy ground truth - flag for manual review, not auto-blamed further
    if extraction_detected and not (normalized_field.region_ids or normalized_field.regions):
        sources.append("Evidence")  # detected a value but produced no supporting region - a separate, independently-visible gap
    return sources


async def _run_gemini_augmentation(
    image_path: Path, ocr_dict: dict, normalized, engine: ComplianceEngine, validation_profile: str, ground_truth: dict,
) -> dict:
    """Run the EXISTING production Gemini fallback (unchanged, imported
    verbatim) against an already-computed OCR result, and re-evaluate
    compliance on the augmented result - mirrors backend/api/ocr.py's
    create_scan() sequence exactly: find_ambiguous_fields ->
    GeminiService.suggest_fields -> apply_suggestions_to_ocr_result ->
    normalize_ocr_result -> ComplianceEngine.evaluate(). Never re-runs OCR -
    reuses the SAME ocr_dict/normalized this fixture's deterministic pass
    already produced. Returns a dict with its own results/classification/
    timings, entirely separate from the deterministic record, so both can
    be compared field-by-field without either one overwriting the other."""
    result: dict[str, Any] = {
        "ambiguous_fields": [], "gemini_fields_used": [], "suggestions": [],
        "extraction": {}, "compliance": {"results": {}, "classification": {}}, "timings_ms": {},
    }
    t0 = time.perf_counter()
    ambiguous_fields = find_ambiguous_fields(ocr_dict, normalized=normalized)
    result["ambiguous_fields"] = ambiguous_fields
    if not ambiguous_fields:
        result["timings_ms"]["gemini_call"] = 0.0
        return result

    image_bytes = image_path.read_bytes()
    service = GeminiService()
    t1 = time.perf_counter()
    suggestions = await service.suggest_fields(image_bytes, ambiguous_fields, ocr_dict.get("regions"))
    t2 = time.perf_counter()
    result["timings_ms"]["gemini_call"] = round((t2 - t1) * 1000, 2)
    result["suggestions"] = [s.model_dump() for s in suggestions]

    if not suggestions:
        return result

    gemini_ocr_dict = apply_suggestions_to_ocr_result(ocr_dict, suggestions)
    gemini_normalized = normalize_ocr_result(gemini_ocr_dict)
    result["gemini_fields_used"] = [s.field for s in suggestions]

    gemini_compliance = engine.evaluate(gemini_ocr_dict, validation_profile)
    gemini_rule_fields = gemini_compliance.rule_results[0].required_fields if gemini_compliance.rule_results else {}
    for field_name in CORE_FIELDS:
        norm_field = gemini_normalized.fields.get(field_name)
        field_result = gemini_rule_fields.get(field_name)
        detected = bool(norm_field.detected) if norm_field else False
        status = field_result.status if field_result else "NOT_EVALUATED"
        result["extraction"][field_name] = {
            "detected": detected,
            "value": norm_field.value if norm_field else None,
            "confidence": round(norm_field.confidence, 4) if norm_field else 0.0,
        }
        result["compliance"]["results"][field_name] = status
        result["compliance"]["classification"][field_name] = _classify_field(field_name, ground_truth.get(field_name), detected, status)
    result["compliance"]["overall_status"] = gemini_compliance.overall_status
    result["compliance"]["compliance_score"] = gemini_compliance.compliance_score
    total_t = time.perf_counter()
    result["timings_ms"]["gemini_total"] = round((total_t - t0) * 1000, 2)
    return result


def run_fixture(fixture: dict, ocr_service: PaddleOCRService, engine: ComplianceEngine, preprocess_options: Optional[PreprocessOptions] = None, use_gemini: bool = False) -> dict:
    image_path = RAW_IMAGES_DIR / fixture["file"]
    record: dict[str, Any] = {
        "fixture": fixture["file"],
        "scenarios": fixture["scenarios"],
        "notes": fixture["notes"],
        "ocr": {}, "extraction": {}, "evidence": {"issues": []},
        "compliance": {"results": {}, "classification": {}},
        "failure_source": {},
        "timings_ms": {},
    }
    if not image_path.exists():
        record["error"] = f"fixture image not found: {image_path}"
        return record

    # Isolated preprocessing-only timing (Accuracy Fix #2's A/B test):
    # measured on a SEPARATE, freshly-loaded copy of the image purely for
    # this number - preprocess_image() also runs a second time inside
    # ocr_service.run() below (that's the one whose OUTPUT actually reaches
    # PaddleOCR), so this never changes what the benchmark measures for
    # accuracy, only adds one extra, isolated timing data point without
    # touching paddle_ocr_service.py to expose an internal timing split.
    if preprocess_options is not None:
        timing_image = PaddleOCRService.load_image(PaddleOCRService.validate_image_path(str(image_path)))
        t_pre0 = time.perf_counter()
        preprocess_image(timing_image, preprocess_options)
        t_pre1 = time.perf_counter()
        record["timings_ms"]["preprocessing_only"] = round((t_pre1 - t_pre0) * 1000, 2)

    t0 = time.perf_counter()
    ocr_result = ocr_service.run(str(image_path), preprocess_options=preprocess_options)
    t1 = time.perf_counter()
    record["timings_ms"]["ocr_inference"] = round((t1 - t0) * 1000, 2)

    if not ocr_result.success:
        record["ocr"] = {"success": False, "error": ocr_result.error.model_dump() if ocr_result.error else None}
        return record

    ocr_dict = ocr_result.model_dump()
    t2 = time.perf_counter()
    record["timings_ms"]["ocr_serialization"] = round((t2 - t1) * 1000, 2)

    full_text = ocr_dict.get("full_text", "")
    price_like_matches = _PRICE_LIKE_PATTERN.findall(full_text)
    quantity_like_matches = _QUANTITY_LIKE_PATTERN.findall(full_text)
    record["ocr"] = {
        "success": True,
        "detection_count": ocr_dict.get("detection_count", 0),
        "mean_confidence": round(sum(d["confidence"] for d in ocr_dict.get("detections") or []) / len(ocr_dict.get("detections") or [1]), 4) if ocr_dict.get("detections") else 0.0,
        "min_confidence": round(min((d["confidence"] for d in ocr_dict.get("detections") or [1.0]), default=0.0), 4),
        "distinct_price_like_numbers": len(set(price_like_matches)),
        "distinct_quantity_like_numbers": len(set(quantity_like_matches)),
    }
    if len(set(price_like_matches)) >= 2 and "multiple_price_numbers" not in record["scenarios"]:
        record["scenarios"].append("multiple_price_numbers_observed")
    if len(set(quantity_like_matches)) >= 2 and "multiple_quantities_observed" not in record["scenarios"]:
        record["scenarios"].append("multiple_quantities_observed")

    t3 = time.perf_counter()
    normalized = normalize_ocr_result(ocr_dict)
    t4 = time.perf_counter()
    record["timings_ms"]["normalization"] = round((t4 - t3) * 1000, 2)

    # Accuracy Fix #7 (Gemini experiment) - ONLY runs when explicitly
    # requested (--gemini). Operates on a SEPARATE copy of the pipeline
    # state (ocr_dict/normalized below are NEVER reassigned by this block),
    # reusing the exact same ocr_dict/normalized already computed above -
    # no second OCR call, no mutation of the deterministic path this
    # record's `extraction`/`compliance` sections describe. Result stored
    # under record["gemini"] for direct field-by-field comparison.
    if use_gemini:
        record["gemini"] = asyncio.run(
            _run_gemini_augmentation(image_path, ocr_dict, normalized, engine, fixture["profile"], fixture["ground_truth"])
        )

    t5 = time.perf_counter()
    extraction = build_structured_extraction(ocr_dict, normalized=normalized, gemini_suggestions=None)
    t6 = time.perf_counter()
    record["timings_ms"]["structured_extraction"] = round((t6 - t5) * 1000, 2)

    t7 = time.perf_counter()
    compliance = engine.evaluate(ocr_dict, fixture["profile"])
    t8 = time.perf_counter()
    record["timings_ms"]["compliance_evaluation"] = round((t8 - t7) * 1000, 2)
    record["timings_ms"]["downstream_total"] = round((t8 - t3) * 1000, 2)
    record["timings_ms"]["end_to_end_total"] = round((t8 - t0) * 1000, 2)

    ground_truth = fixture["ground_truth"]
    rule_fields = compliance.rule_results[0].required_fields if compliance.rule_results else {}

    for field_name in CORE_FIELDS:
        norm_field = normalized.fields.get(field_name)
        field_result = rule_fields.get(field_name)
        detected = bool(norm_field.detected) if norm_field else False
        status = field_result.status if field_result else "NOT_EVALUATED"
        gt = ground_truth.get(field_name)

        record["extraction"][field_name] = {
            "detected": detected,
            "value": norm_field.value if norm_field else None,
            "confidence": round(norm_field.confidence, 4) if norm_field else 0.0,
            "region_ids": norm_field.region_ids if norm_field else [],
        }
        record["compliance"]["results"][field_name] = status

        classification = _classify_field(field_name, gt, detected, status)
        record["compliance"]["classification"][field_name] = classification

        if detected and norm_field and not (norm_field.region_ids or norm_field.regions):
            record["evidence"]["issues"].append(f"{field_name}: detected but no supporting evidence region")

        if classification == "FALSE_NOT_DETECTED":
            record["failure_source"][field_name] = _failure_source(field_name, full_text, norm_field, detected, norm_field.confidence if norm_field else 0.0)
        elif classification == "FALSE_PASS":
            record["failure_source"][field_name] = ["Compliance"]  # confirmed-blank area still yielded PASS - needs direct review, not attributable to OCR/extraction alone

    record["compliance"]["overall_status"] = compliance.overall_status
    record["compliance"]["compliance_score"] = compliance.compliance_score
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", nargs="*", default=None, help="Subset of filenames to run (default: all defined fixtures)")
    parser.add_argument(
        "--preprocess", action="store_true",
        help="Accuracy Fix #2 A/B test: run with denoise+contrast-enhancement preprocessing applied "
             "(the same PreprocessOptions bundle backend/api/ocr.py's OCR_PREPROCESSING_ENABLED flag uses - "
             "resize deliberately excluded, see the accompanying inspection report). Default: off, i.e. today's baseline.",
    )
    parser.add_argument("--out-prefix", default="e2e_benchmark", help="Basename for output files (default: e2e_benchmark)")
    parser.add_argument(
        "--gemini", action="store_true",
        help="Accuracy Fix #7 experiment: ADDITIONALLY run the existing production Gemini fallback "
             "(find_ambiguous_fields -> GeminiService.suggest_fields -> apply_suggestions_to_ocr_result, "
             "unchanged, imported from backend/ocr/gemini_service.py) alongside the normal deterministic-only "
             "pass, storing the result separately under each record's \"gemini\" key for comparison. Makes real "
             "Gemini API calls (needs GEMINI_API_KEY set). Default: off - normal `python training/benchmark_e2e.py` "
             "remains deterministic-only and never imports/calls Gemini, exactly as before this flag existed.",
    )
    args = parser.parse_args(argv)

    fixtures = FIXTURES
    if args.fixtures:
        wanted = set(args.fixtures)
        fixtures = [f for f in FIXTURES if f["file"] in wanted]

    preprocess_options = PreprocessOptions(denoise=True, enhance_contrast=True) if args.preprocess else None

    if args.gemini and not GEMINI_API_KEY:
        print("ERROR: --gemini requires GEMINI_API_KEY to be set (see Portal/.env).", file=sys.stderr)
        return 1

    RESULTS_DIR.mkdir(exist_ok=True)
    jsonl_path = RESULTS_DIR / f"{args.out_prefix}.jsonl"
    summary_path = RESULTS_DIR / f"{args.out_prefix}_summary.json"

    ocr_service = PaddleOCRService()
    engine = ComplianceEngine()

    results = []
    with jsonl_path.open("w", encoding="utf-8") as jsonl_file:
        for fixture in fixtures:
            print(f"Running {fixture['file']} ...", file=sys.stderr, flush=True)
            record = run_fixture(fixture, ocr_service, engine, preprocess_options=preprocess_options, use_gemini=args.gemini)
            results.append(record)
            jsonl_file.write(json.dumps(record) + "\n")
            jsonl_file.flush()  # checkpoint after every fixture - a long OCR run can be interrupted without losing prior results
            print(f"  done: {record.get('timings_ms', {}).get('end_to_end_total', 'n/a')} ms end-to-end", file=sys.stderr, flush=True)

    successful = [r for r in results if r.get("ocr", {}).get("success")]

    def _avg(key: str) -> Optional[float]:
        values = [r["timings_ms"][key] for r in successful if key in r.get("timings_ms", {})]
        return round(sum(values) / len(values), 2) if values else None

    classification_counts: dict[str, int] = {}
    failure_source_counts: dict[str, int] = {}
    for r in successful:
        for classification in r.get("compliance", {}).get("classification", {}).values():
            classification_counts[classification] = classification_counts.get(classification, 0) + 1
        for sources in r.get("failure_source", {}).values():
            for source in sources:
                failure_source_counts[source] = failure_source_counts.get(source, 0) + 1

    summary = {
        "fixtures_run": len(results),
        "fixtures_ocr_succeeded": len(successful),
        "uncovered_categories": UNCOVERED_CATEGORIES,
        "average_latency_ms": {
            "preprocessing_only": _avg("preprocessing_only"),
            "ocr_inference": _avg("ocr_inference"),
            "ocr_serialization": _avg("ocr_serialization"),
            "normalization": _avg("normalization"),
            "structured_extraction": _avg("structured_extraction"),
            "compliance_evaluation": _avg("compliance_evaluation"),
            "downstream_total": _avg("downstream_total"),
            "end_to_end_total": _avg("end_to_end_total"),
        },
        "classification_counts": classification_counts,
        "failure_source_counts": failure_source_counts,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nWrote {len(results)} fixture record(s) to {jsonl_path}")
    print(f"Wrote summary to {summary_path}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
