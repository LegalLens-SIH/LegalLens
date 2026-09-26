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


def test_validated_candidate_maps_regions_into_pdp_geometry_without_authority(tmp_path):
    checkpoint = tmp_path / "candidate.pt"
    checkpoint.write_bytes(b"test-only checkpoint fixture")
    class_names = [name for _, name in sorted(_load_class_names().items())]
    report = tmp_path / "validation.json"
    report.write_text(json.dumps({
        "status": "evaluated",
        "model_hash": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "class_names": class_names,
        "dataset_hash": "test-dataset-hash",
        "metrics": {"evaluation_fixture": "schema-only test"},
    }), encoding="utf-8")
    predictor = lambda _image, **_kwargs: [
        {"class_id": 1, "confidence": 0.91, "bbox": [10, 10, 90, 80]},
        {"class_id": 3, "confidence": 0.20, "bbox": [20, 20, 40, 40]},
    ]
    service = PDPDetectorService(
        enabled=True,
        model_path=checkpoint,
        validation_path=report,
        validated_model_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        confidence=0.5,
        predictor=predictor,
    )

    result = service.run(np.zeros((100, 100, 3), dtype=np.uint8), source_image_sha256="b" * 64)

    assert result["status"] == "experimental_candidate"
    assert result["available"] is True
    assert result["production_authority"] is False
    assert result["used_for_compliance"] is False
    assert len(result["detections"]) == 1
    region = result["detections"][0]
    assert region["class_name"] == class_names[1]
    assert region["confidence"] == 0.91
    assert region["source_image_sha256"] == "b" * 64
    assert region["evidence_region_ids"] == [region["region_id"]]
    assert result["geometry"]["evidence_regions"][0]["region_id"] == region["region_id"]
    assert result["geometry"]["status"] == "eligible"