from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from backend.services.pdp_detector import PDPDetectorService, _load_class_names


def test_detector_is_disabled_by_default_without_loading_any_model():
    result = PDPDetectorService(enabled=False).run(np.zeros((100, 100, 3), dtype=np.uint8), source_image_sha256="a" * 64)
    assert result["status"] == "disabled"
    assert result["production_authority"] is False
    assert result["used_for_compliance"] is False
    assert result["detections"] == []


def test_missing_checkpoint_and_validation_report_degrade_cleanly(tmp_path):
    absent = PDPDetectorService(enabled=True, model_path=tmp_path / "missing.pt", validation_path=tmp_path / "missing.json")
    assert absent.run(np.zeros((100, 100, 3), dtype=np.uint8), source_image_sha256="a" * 64)["status"] == "checkpoint_unavailable"

    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test-only checkpoint fixture")
    no_report = PDPDetectorService(enabled=True, model_path=checkpoint, validation_path=tmp_path / "missing.json")
    assert no_report.run(np.zeros((100, 100, 3), dtype=np.uint8), source_image_sha256="a" * 64)["status"] == "validation_unavailable"


def test_evaluation_report_does_not_auto_promote_checkpoint(tmp_path):
    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test-only checkpoint fixture")
    report = tmp_path / "evaluation.json"
    report.write_text(json.dumps({"status": "evaluated", "model_hash": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}), encoding="utf-8")
    service = PDPDetectorService(enabled=True, model_path=checkpoint, validation_path=report)
    result = service.run(np.zeros((100, 100, 3), dtype=np.uint8), source_image_sha256="a" * 64)
    assert result["status"] == "validation_required"
    assert result["available"] is False


def test_validated_candidate_maps_regions_in_deterministic_order_without_authority(tmp_path, monkeypatch):
    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test-only checkpoint fixture")
    class_names = [name for _, name in sorted(_load_class_names().items())]
    dataset_manifest = {
        "schema_version": 1,
        "dataset_id": "legal-lense-pdp",
        "dataset_version": "1.0.0",
        "class_names": {str(index): name for index, name in enumerate(class_names)},
        "splits": {"val": {"status": "present", "images": 1, "labeled_images": 1, "files": []}},
        "provenance": {"complete": True, "product_split_leakage": False},
    }
    dataset_manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = tmp_path / "validation.json"
    report.write_text(json.dumps({
        "status": "evaluated",
        "model_hash": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "class_names": class_names,
        "dataset_hash": "test-dataset-hash",
        "dataset_manifest_hash": dataset_manifest["manifest_sha256"],
        "dataset_manifest": dataset_manifest,
        "split": "val",
        "promotion_status": "not_promoted",
        "metrics": {"evaluation_fixture": "schema-only test"},
    }), encoding="utf-8")
    predictor = lambda _image, **_kwargs: [
        {"class_id": 1, "confidence": 0.90, "bbox": [5, 5, 95, 95]},
        {"class_id": 0, "confidence": 0.80, "bbox": [10, 20, 90, 80]},
        {"class_id": 0, "confidence": 0.91, "bbox": [10, 10, 90, 70]},
    ]
    monkeypatch.delenv("LEGAL_LENSE_PDP_PROMOTION_SHA256", raising=False)
    service = PDPDetectorService(
        enabled=True,
        model_path=checkpoint,
        validation_path=report,
        validated_model_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        confidence=0.5,
        predictor=predictor,
        max_regions=2,
    )

    result = service.run(np.zeros((100, 100, 3), dtype=np.uint8), source_image_sha256="b" * 64)

    assert result["status"] == "experimental_candidate"
    assert result["available"] is True
    assert result["production_authority"] is False
    assert result["used_for_compliance"] is False
    assert len(result["detections"]) == 2
    assert [item["bbox"][1] for item in result["detections"]] == [10, 20]
    assert result["promotion_status"] == "PROMOTION_BLOCKED"
    assert result["dataset_id"] == "legal-lense-pdp"
    assert result["dataset_version"] == "1.0.0"
    region = result["detections"][0]
    assert region["class_name"] == class_names[0]
    assert region["confidence"] == 0.91
    assert region["source_image_sha256"] == "b" * 64
    assert region["evidence_region_ids"] == [region["region_id"]]
    assert result["geometry"]["evidence_regions"][0]["region_id"] == region["region_id"]
    assert result["geometry"]["status"] == "eligible"


def test_malformed_validation_report_and_checkpoint_hash_mismatch_fail_closed(tmp_path):
    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test-only checkpoint fixture")
    report = tmp_path / "validation.json"
    image = np.zeros((10, 10, 3), dtype=np.uint8)

    report.write_text("{malformed", encoding="utf-8")
    malformed = PDPDetectorService(enabled=True, model_path=checkpoint, validation_path=report)
    assert malformed.run(image, source_image_sha256="a" * 64)["status"] == "validation_invalid"

    report.write_text(json.dumps({"status": "evaluated", "model_hash": "0" * 64}), encoding="utf-8")
    mismatched = PDPDetectorService(enabled=True, model_path=checkpoint, validation_path=report)
    assert mismatched.run(image, source_image_sha256="a" * 64)["status"] == "checkpoint_hash_mismatch"


def test_dataset_manifest_tampering_and_class_schema_mismatch_are_rejected(tmp_path):
    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test-only checkpoint fixture")
    class_names = [name for _, name in sorted(_load_class_names().items())]
    dataset_manifest = {
        "schema_version": 1,
        "dataset_id": "legal-lense-pdp",
        "dataset_version": "1.0.0",
        "class_names": {str(index): name for index, name in enumerate(class_names)},
        "splits": {"val": {"status": "present", "images": 1, "labeled_images": 1, "files": []}},
        "provenance": {"complete": True, "product_split_leakage": False},
    }
    dataset_manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = tmp_path / "validation.json"
    model_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    base = {
        "status": "evaluated", "model_hash": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "class_names": class_names, "dataset_hash": "test-dataset-hash", "dataset_manifest": dataset_manifest,
        "dataset_manifest_hash": dataset_manifest["manifest_sha256"], "split": "val",
        "promotion_status": "not_promoted", "metrics": {"fixture": "schema-only"},
    }

    tampered = {**base, "dataset_manifest": {**dataset_manifest, "splits": {"val": {"images": 2, "labeled_images": 1}}}}
    report.write_text(json.dumps(tampered), encoding="utf-8")
    service = PDPDetectorService(enabled=True, model_path=checkpoint, validation_path=report, validated_model_sha256=model_hash)
    assert service.run(np.zeros((10, 10, 3), dtype=np.uint8), source_image_sha256="a" * 64)["status"] == "validation_invalid"

    wrong_classes = {**base, "dataset_manifest": {**dataset_manifest, "class_names": {"0": "wrong"}}}
    wrong_classes["dataset_manifest"]["manifest_sha256"] = hashlib.sha256(
        json.dumps({key: value for key, value in wrong_classes["dataset_manifest"].items() if key != "manifest_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    wrong_classes["dataset_manifest_hash"] = wrong_classes["dataset_manifest"]["manifest_sha256"]
    report.write_text(json.dumps(wrong_classes), encoding="utf-8")
    service = PDPDetectorService(enabled=True, model_path=checkpoint, validation_path=report, validated_model_sha256=model_hash)
    assert service.run(np.zeros((10, 10, 3), dtype=np.uint8), source_image_sha256="a" * 64)["status"] == "class_schema_mismatch"
