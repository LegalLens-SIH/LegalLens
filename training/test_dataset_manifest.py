from __future__ import annotations

from pathlib import Path

import yaml

from training._dataset_utils import build_dataset_manifest


def _dataset(tmp_path: Path, *, split_path: str = "images/train") -> tuple[Path, Path]:
    root = tmp_path / "data"
    image_dir = root / split_path
    image_dir.mkdir(parents=True)
    (image_dir / "a.jpg").write_bytes(b"image-a")
    (image_dir / "b.jpg").write_bytes(b"image-b")
    if "images" in Path(split_path).parts:
        labels = root / "labels" / Path(*Path(split_path).parts[1:])
        labels.mkdir(parents=True)
        (labels / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    data_yaml = tmp_path / "dataset.yaml"
    data_yaml.write_text(yaml.safe_dump({
        "path": str(root), "train": split_path, "val": "images/val", "names": {0: "PDP"},
    }), encoding="utf-8")
    return data_yaml, root


def test_manifest_is_deterministic_and_records_missing_annotations(tmp_path):
    data_yaml, _ = _dataset(tmp_path)
    first = build_dataset_manifest(data_yaml, ("train", "val", "test"))
    second = build_dataset_manifest(data_yaml, ("train", "val", "test"))

    assert first == second
    assert first["class_names"] == {"0": "PDP"}
    assert first["splits"]["train"]["images"] == 2
    assert first["splits"]["train"]["labeled_images"] == 1
    assert first["splits"]["train"]["files"][0]["image"] == "a.jpg"
    assert first["splits"]["train"]["files"][1]["label_sha256"] is None
    assert first["splits"]["val"]["status"] == "unavailable"
    assert first["splits"]["test"]["status"] == "not_configured"


def test_manifest_does_not_search_working_directory_for_labels(tmp_path, monkeypatch):
    data_yaml, _ = _dataset(tmp_path, split_path="custom/train")
    monkeypatch.chdir(tmp_path)
    Path("a.txt").write_text("not a dataset label", encoding="utf-8")

    manifest = build_dataset_manifest(data_yaml, ("train",))

    assert manifest["splits"]["train"]["labeled_images"] == 0
    assert all(item["label"] is None for item in manifest["splits"]["train"]["files"])


def test_manifest_rejects_missing_class_metadata(tmp_path):
    data_yaml = tmp_path / "bad.yaml"
    data_yaml.write_text("train: images/train\n", encoding="utf-8")

    import pytest
    with pytest.raises(ValueError, match="class names"):
        build_dataset_manifest(data_yaml, ("train",))
