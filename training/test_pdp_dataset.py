from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from training import manage_pdp_dataset as pdp_dataset
from training._dataset_utils import build_dataset_manifest


def _configure_dataset(tmp_path, monkeypatch):
    root = tmp_path / "pdp-v1"
    root.mkdir()
    for split in ("train", "val", "test"):
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
    schema = root / "dataset.yaml"
    schema.write_text(
        "dataset_id: legal-lense-pdp\ndataset_version: 1.0.0\n"
        "train: images/train\nval: images/val\ntest: images/test\n"
        "names:\n  0: principal_display_panel\n",
        encoding="utf-8",
    )
    metadata = root / "metadata.jsonl"
    metadata.write_text("", encoding="utf-8")
    monkeypatch.setattr(pdp_dataset, "DATASET_DIR", root)
    monkeypatch.setattr(pdp_dataset, "DATASET_YAML", schema)
    monkeypatch.setattr(pdp_dataset, "METADATA_PATH", metadata)
    return root


def _image(path: Path, color: str) -> Path:
    Image.new("RGB", (80, 60), color=color).save(path, format="PNG")
    return path


def test_import_requires_rights_confirmation_and_explicit_negative_label(tmp_path, monkeypatch):
    _configure_dataset(tmp_path, monkeypatch)
    image = _image(tmp_path / "candidate.png", "white")
    labels = tmp_path / "candidate.txt"
    labels.write_text("", encoding="utf-8")

    with pytest.raises(pdp_dataset.PDPDatasetError, match="license permission"):
        pdp_dataset.import_example(
            image_path=image, label_path=labels, split="train", product_identity="sku-1",
            source="operator capture", license_name="owned", category="food", conditions=["front_facing"], operator="reviewer",
            capture_date=None, rights_confirmed=False, negative=True,
        )
    with pytest.raises(pdp_dataset.PDPDatasetError, match="--negative"):
        pdp_dataset.import_example(
            image_path=image, label_path=labels, split="train", product_identity="sku-1",
            source="operator capture", license_name="owned", category="food", conditions=["front_facing"], operator="reviewer",
            capture_date=None, rights_confirmed=True, negative=False,
        )


def test_import_records_provenance_and_counts_explicit_negative_examples(tmp_path, monkeypatch):
    root = _configure_dataset(tmp_path, monkeypatch)
    positive_image = _image(tmp_path / "positive.png", "red")
    positive_label = tmp_path / "positive.txt"
    positive_label.write_text("0 0.5 0.5 0.7 0.6\n", encoding="utf-8")
    negative_image = _image(tmp_path / "negative.png", "blue")
    negative_label = tmp_path / "negative.txt"
    negative_label.write_text("", encoding="utf-8")

    pdp_dataset.import_example(
        image_path=positive_image, label_path=positive_label, split="train", product_identity="sku-positive",
        source="owned capture batch 1", license_name="LegalLense-owned", category="food", conditions=["front_facing", "glossy"], operator="reviewer-a",
        capture_date="2026-09-01", rights_confirmed=True, negative=False,
    )
    pdp_dataset.import_example(
        image_path=negative_image, label_path=negative_label, split="test", product_identity="sku-negative",
        source="owned capture batch 2", license_name="LegalLense-owned", category="cosmetics", conditions=["hard_negative_barcode"], operator="reviewer-b",
        capture_date=None, rights_confirmed=True, negative=True,
    )

    report = pdp_dataset.dataset_report()

    assert report["dataset_id"] == "legal-lense-pdp"
    assert report["dataset_version"] == "1.0.0"
    assert report["splits"]["train"]["images"] == 1
    assert report["splits"]["test"]["images"] == 1
    assert report["positive_images"] == 1
    assert report["negative_images"] == 1
    assert report["objects"] == 1
    assert report["category_distribution"] == {"cosmetics": 1, "food": 1}
    assert report["condition_distribution"] == {"front_facing": 1, "glossy": 1, "hard_negative_barcode": 1}
    assert report["condition_counts"] == report["condition_distribution"]
    assert report["provenance_complete"] is True
    assert report["product_split_leakage"] is False
    assert len(report["manifest_sha256"]) == 64


def test_same_product_identity_cannot_be_imported_into_another_split(tmp_path, monkeypatch):
    _configure_dataset(tmp_path, monkeypatch)
    first = _image(tmp_path / "front.png", "green")
    first_label = tmp_path / "front.txt"
    first_label.write_text("0 0.5 0.5 0.6 0.5\n", encoding="utf-8")
    second = _image(tmp_path / "back.png", "yellow")
    second_label = tmp_path / "back.txt"
    second_label.write_text("0 0.5 0.5 0.5 0.4\n", encoding="utf-8")

    for image, label, split in ((first, first_label, "train"), (second, second_label, "val")):
        if split == "val":
            with pytest.raises(pdp_dataset.PDPDatasetError, match="same split"):
                pdp_dataset.import_example(
                    image_path=image, label_path=label, split=split, product_identity="same-physical-product",
                    source="owned capture", license_name="owned", category="food", conditions=["perspective"], operator="reviewer",
                    capture_date=None, rights_confirmed=True, negative=False,
                )
        else:
            pdp_dataset.import_example(
                image_path=image, label_path=label, split=split, product_identity="same-physical-product",
                source="owned capture", license_name="owned", category="food", conditions=["perspective"], operator="reviewer",
                capture_date=None, rights_confirmed=True, negative=False,
            )


def test_training_manifest_does_not_read_held_out_test_images(tmp_path, monkeypatch):
    root = _configure_dataset(tmp_path, monkeypatch)
    train_image = _image(tmp_path / "train.png", "red")
    train_label = tmp_path / "train.txt"
    train_label.write_text("0 0.5 0.5 0.6 0.5\n", encoding="utf-8")
    test_image = _image(tmp_path / "test.png", "blue")
    test_label = tmp_path / "test.txt"
    test_label.write_text("0 0.5 0.5 0.6 0.5\n", encoding="utf-8")
    for image, label, split, product in (
        (train_image, train_label, "train", "train-product"),
        (test_image, test_label, "test", "test-product"),
    ):
        pdp_dataset.import_example(
            image_path=image, label_path=label, split=split, product_identity=product,
            source="owned capture", license_name="owned", category="food", conditions=["front_facing"], operator="reviewer",
            capture_date=None, rights_confirmed=True, negative=False,
        )
    test_file = next((root / "images" / "test").glob("*.png"))
    test_file.write_bytes(b"held-out bytes must not be read during training-manifest generation")

    manifest = build_dataset_manifest(root / "dataset.yaml", ("train", "val"))

    assert manifest["splits"]["train"]["images"] == 1
    assert "test" not in manifest["splits"]
    assert [record["split"] for record in manifest["provenance"]["records"]] == ["train"]
