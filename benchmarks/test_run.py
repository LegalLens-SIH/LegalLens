from pathlib import Path

from backend.ocr.schemas import Detection, OCRResult
from benchmarks.run import run_benchmark


def test_benchmark_harness_records_field_and_evidence_metrics(tmp_path, monkeypatch):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "sample.png"
    image_path.write_bytes(b"tooling-only-placeholder")
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text(
        '{"records":[{"product_id":"P1","image_id":"sample.png","capture_conditions":["synthetic_tooling_fixture"],"expected_fields":{"net_quantity":"500 g"}}]}',
        encoding="utf-8",
    )

    def fake_run(_self, _path):
        detection = Detection(
            text="Net Quantity: 500 g",
            confidence=0.99,
            bbox=[0, 0, 100, 20],
            polygon=[[0, 0], [100, 0], [100, 20], [0, 20]],
        )
        return OCRResult(success=True, image="sample.png", full_text=detection.text, detections=[detection], detection_count=1)

    monkeypatch.setattr("benchmarks.run.PaddleOCRService.run", fake_run)
    result = run_benchmark(image_dir, ground_truth)

    assert result["metrics"]["field_matches"] == 1
    assert result["metrics"]["evidence_coverage"] == 1.0
    assert result["records"][0]["timings_ms"]["total"] >= 0
