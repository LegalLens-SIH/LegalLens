from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from backend.ocr.paddle_ocr_service import PaddleOCRService
from backend.services.compliance_engine import ComplianceEngine, normalize_ocr_result
from backend.services.structured_extraction import build_structured_extraction


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def load_ground_truth(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = raw.get("records") if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError("ground truth must be a list or an object with a records list")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each ground-truth record must be an object")
        for required in ("product_id", "image_id", "capture_conditions", "expected_fields"):
            if required not in record:
                raise ValueError(f"ground-truth record missing {required}")
        image_id = str(record["image_id"])
        if image_id in result:
            raise ValueError(f"duplicate ground-truth image_id: {image_id}")
        if not isinstance(record["capture_conditions"], list) or not isinstance(record["expected_fields"], dict):
            raise ValueError(f"invalid ground-truth shapes for {image_id}")
        result[image_id] = record
    return result


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict) and "status" in expected:
        return actual == expected["status"]
    if expected is None:
        return actual is None
    return str(expected).strip().casefold() in str(actual or "").casefold()


def run_benchmark(image_dir: Path, ground_truth_path: Path, *, output: Path | None = None, baseline: Path | None = None) -> dict[str, Any]:
    ground_truth = load_ground_truth(ground_truth_path)
    service = PaddleOCRService()
    engine = ComplianceEngine()
    records: list[dict[str, Any]] = []
    field_results: list[dict[str, Any]] = []

    for image_path in sorted(path for path in image_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS):
        truth = ground_truth.get(image_path.name)
        if truth is None:
            continue
        started = time.perf_counter()
        ocr_result = service.run(str(image_path))
        ocr_ms = (time.perf_counter() - started) * 1000
        if not ocr_result.success:
            records.append({"image_id": image_path.name, "success": False, "timings_ms": {"ocr": round(ocr_ms, 2)}})
            continue
        ocr_dict = ocr_result.model_dump()
        normalized_started = time.perf_counter()
        normalized = normalize_ocr_result(ocr_dict)
        extraction = build_structured_extraction(ocr_dict, normalized=normalized)
        normalization_ms = (time.perf_counter() - normalized_started) * 1000
        compliance_started = time.perf_counter()
        compliance = engine.evaluate(ocr_dict, truth.get("validation_profile", "e_commerce_product_listing"))
        compliance_ms = (time.perf_counter() - compliance_started) * 1000
        record = {
            "product_id": truth["product_id"],
            "image_id": image_path.name,
            "capture_conditions": truth["capture_conditions"],
            "success": True,
            "ocr": {"detection_count": ocr_result.detection_count},
            "compliance_status": compliance.overall_status,
            "timings_ms": {
                "ocr": round(ocr_ms, 2),
                "normalization_extraction": round(normalization_ms, 2),
                "compliance": round(compliance_ms, 2),
                "total": round(ocr_ms + normalization_ms + compliance_ms, 2),
            },
        }
        for field_name, expected in truth["expected_fields"].items():
            normalized_field = normalized.fields.get(field_name)
            actual = normalized_field.value if normalized_field and normalized_field.detected else None
            matched = _matches(expected, actual)
            field_results.append({"image_id": image_path.name, "field": field_name, "expected": expected, "actual": actual, "matched": matched, "evidence_regions": len(normalized_field.region_ids) if normalized_field else 0})
        records.append(record)

    matched = sum(1 for item in field_results if item["matched"])
    total = len(field_results)
    metrics = {
        "images_evaluated": len(records),
        "successful_images": sum(1 for record in records if record["success"]),
        "field_checks": total,
        "field_matches": matched,
        "field_accuracy": matched / total if total else None,
        "evidence_coverage": sum(1 for item in field_results if item["evidence_regions"] > 0) / total if total else None,
        "latency_ms": {
            "p50_total": statistics.median([r["timings_ms"]["total"] for r in records if r["success"]]) if any(r["success"] for r in records) else None,
            "p95_total": _percentile([r["timings_ms"]["total"] for r in records if r["success"]], 95),
        },
    }
    result = {"schema_version": 1, "metrics": metrics, "records": records, "field_results": field_results}
    if baseline:
        baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
        result["baseline"] = {"metrics": baseline_data.get("metrics", {})}
        result["regressions"] = _compare_metrics(metrics, baseline_data.get("metrics", {}))
    if output:
        output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1)))
    return ordered[index]


def _compare_metrics(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for key in ("field_accuracy", "evidence_coverage"):
        if isinstance(current.get(key), (int, float)) and isinstance(baseline.get(key), (int, float)):
            comparison[key] = {"current": current[key], "baseline": baseline[key], "delta": current[key] - baseline[key]}
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic LegalLense OCR/extraction/compliance benchmark.")
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.image_dir, args.ground_truth, output=args.output, baseline=args.baseline)
    print(json.dumps(result["metrics"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
