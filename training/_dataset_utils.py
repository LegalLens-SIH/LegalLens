"""
Shared dataset-path helpers for train_yolo.py and evaluate_yolo.py.

Kept in one place because Ultralytics' own path-resolution behavior (see
dataset.yaml's `path:` comment) is subtle enough that getting the
images<->labels substitution wrong once already caused a real bug during
this project's own testing - not worth risking two divergent copies that
could silently drift apart.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import yaml
from PIL import Image

# Files that exist in an "empty" images/labels directory on purpose and must
# never count as real dataset content: .gitkeep keeps the folder tracked by
# git, classes.txt is LabelImg's class-index file (see training/LABELIMG_SETUP.md)
# and is present from day one, before any image has ever been labeled.
_PLACEHOLDER_NAMES = {".gitkeep", "classes.txt"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset_manifest(data_yaml: Path, splits: tuple[str, ...]) -> dict:
    """Build a deterministic split manifest with hashes for images and labels."""
    config = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Dataset schema must be a YAML object")
    split_manifest = {}
    for split in splits:
        split_value = config.get(split)
        if not split_value:
            split_manifest[split] = {"status": "not_configured", "images": 0, "labels": 0, "files": []}
            continue
        dirs = resolve_split_dirs(data_yaml.resolve(), (split,))
        image_dir = dirs[0] if dirs else Path()
        label_dir = dirs[1] if len(dirs) > 1 else None
        image_files = sorted(path for path in image_dir.rglob("*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES and path.name not in _PLACEHOLDER_NAMES)
        if len(image_files) > 100_000:
            raise ValueError(f"Dataset split {split} contains more than 100000 images")
        files = []
        for image in image_files:
            rel = image.relative_to(image_dir)
            label = (label_dir / rel).with_suffix(".txt") if label_dir is not None else None
            files.append({
                "split": split,
                "image": rel.as_posix(),
                "image_sha256": _sha256_file(image),
                "label": label.relative_to(label_dir).as_posix() if label is not None and label.is_file() else None,
                "label_sha256": _sha256_file(label) if label is not None and label.is_file() else None,
            })
        split_manifest[split] = {
            "status": "present" if image_files else "unavailable",
            "images": len(image_files),
            "labeled_images": sum(1 for item in files if item["label_sha256"]),
            "files": files,
        }
    names = config.get("names")
    if not isinstance(names, dict) or not names:
        raise ValueError("Dataset schema must declare class names")
    normalized_names = {str(key): str(names[key]) for key in sorted(names, key=lambda value: int(value))}
    payload = {"schema_version": 1, "dataset_yaml_sha256": hashlib.sha256(data_yaml.read_bytes()).hexdigest(), "class_names": normalized_names, "splits": split_manifest}
    if normalized_names == {"0": "principal_display_panel"}:
        metadata_path = data_yaml.parent / "metadata.jsonl"
        if not metadata_path.is_file():
            raise ValueError("PDP dataset requires metadata.jsonl provenance records")
        all_metadata_records = []
        by_image: dict[tuple[str, str], dict] = {}
        product_splits: dict[str, str] = {}
        required = ("split", "image", "image_sha256", "label_sha256", "product_identity_sha256", "source", "license", "category", "operator", "rights_confirmation", "annotation_version", "annotation_status", "object_count")
        for line_number, line in enumerate(metadata_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid PDP metadata JSON on line {line_number}") from exc
            if not isinstance(record, dict) or any(key not in record for key in required):
                raise ValueError(f"PDP metadata line {line_number} is missing required provenance")
            if any(not isinstance(record[key], str) or not record[key].strip() for key in required if key != "object_count"):
                raise ValueError(f"PDP metadata line {line_number} has empty or invalid provenance fields")
            if isinstance(record["object_count"], bool) or not isinstance(record["object_count"], int) or record["object_count"] < 0:
                raise ValueError(f"PDP metadata line {line_number} has an invalid object_count")
            if ((record["annotation_status"] == "positive" and record["object_count"] == 0)
                    or (record["annotation_status"] == "negative" and record["object_count"] != 0)):
                raise ValueError(f"PDP metadata line {line_number} annotation status disagrees with its object count")
            if record["split"] not in {"train", "val", "test"}:
                raise ValueError(f"PDP metadata line {line_number} has an invalid split")
            if record["rights_confirmation"] != "operator_confirmed" or record["annotation_status"] not in {"positive", "negative"}:
                raise ValueError(f"PDP metadata line {line_number} has an invalid rights or annotation state")
            for digest_key in ("image_sha256", "label_sha256", "product_identity_sha256"):
                digest = record[digest_key]
                if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    raise ValueError(f"PDP metadata line {line_number} has an invalid {digest_key}")
            key = (record["split"], str(record["image"]))
            if key in by_image:
                raise ValueError(f"Duplicate PDP metadata entry for {record['split']}/{record['image']}")
            prior_split = product_splits.setdefault(record["product_identity_sha256"], record["split"])
            if prior_split != record["split"]:
                raise ValueError("PDP product identity appears in more than one data split")
            by_image[key] = record
            all_metadata_records.append(record)

        seen_images: set[tuple[str, str]] = set()
        metadata_records = [record for record in all_metadata_records if record["split"] in split_manifest]
        positive_images = 0
        negative_images = 0
        object_count = 0
        condition_counts: dict[str, int] = {}
        for split, summary in split_manifest.items():
            split_dirs = resolve_split_dirs(data_yaml.resolve(), (split,))
            if len(split_dirs) != 2:
                raise ValueError(f"PDP split {split} has no corresponding images/labels directories")
            image_dir, label_dir = split_dirs
            for file_record in summary["files"]:
                key = (split, file_record["image"])
                metadata = by_image.get(key)
                if metadata is None:
                    raise ValueError(f"PDP image {split}/{file_record['image']} has no provenance record")
                if metadata["image_sha256"] != file_record["image_sha256"] or metadata["label_sha256"] != file_record["label_sha256"]:
                    raise ValueError(f"PDP provenance digest mismatch for {split}/{file_record['image']}")
                if file_record["label_sha256"] is None:
                    raise ValueError(f"PDP image {split}/{file_record['image']} has no annotation file")
                image_path = image_dir / file_record["image"]
                try:
                    with Image.open(image_path) as image:
                        actual_dimensions = image.size
                        image.verify()
                except Exception as exc:
                    raise ValueError(f"PDP image {split}/{file_record['image']} is unreadable") from exc
                if (metadata.get("image_width"), metadata.get("image_height")) != actual_dimensions:
                    raise ValueError(f"PDP image dimensions do not match provenance for {split}/{file_record['image']}")
                label_path = label_dir / file_record["image"]
                label_path = label_path.with_suffix(".txt")
                try:
                    label_lines = [line for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                except (OSError, UnicodeDecodeError) as exc:
                    raise ValueError(f"PDP annotation for {split}/{file_record['image']} is unreadable") from exc
                if len(label_lines) != metadata["object_count"]:
                    raise ValueError(f"PDP object count does not match annotation for {split}/{file_record['image']}")
                for line in label_lines:
                    values = line.split()
                    if len(values) != 5 or values[0] != "0":
                        raise ValueError(f"PDP annotation for {split}/{file_record['image']} must contain class 0 and four box coordinates")
                    try:
                        center_x, center_y, box_width, box_height = (float(value) for value in values[1:])
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"PDP annotation for {split}/{file_record['image']} contains invalid coordinates") from exc
                    if (not all(math.isfinite(value) for value in (center_x, center_y, box_width, box_height))
                            or box_width <= 0 or box_height <= 0
                            or any(value < 0 or value > 1 for value in (center_x, center_y, box_width, box_height))
                            or center_x - box_width / 2 < 0 or center_x + box_width / 2 > 1
                            or center_y - box_height / 2 < 0 or center_y + box_height / 2 > 1):
                        raise ValueError(f"PDP annotation for {split}/{file_record['image']} has out-of-bounds geometry")
                seen_images.add(key)
                positive_images += metadata["annotation_status"] == "positive"
                negative_images += metadata["annotation_status"] == "negative"
                object_count += int(metadata["object_count"])
                conditions = metadata.get("conditions")
                if not isinstance(conditions, list) or not conditions or any(not isinstance(item, str) or not item for item in conditions):
                    raise ValueError(f"PDP provenance for {split}/{file_record['image']} has no valid capture conditions")
                for condition in set(conditions):
                    condition_counts[condition] = condition_counts.get(condition, 0) + 1
                file_record["provenance"] = {key: metadata[key] for key in required if key not in {"image_sha256", "label_sha256"}}
        selected_image_keys = {key for key in by_image if key[0] in split_manifest}
        if seen_images != selected_image_keys:
            raise ValueError("PDP provenance metadata references missing or unconfigured images")
        payload.update({
            "dataset_id": config.get("dataset_id"),
            "dataset_version": config.get("dataset_version"),
            "metadata_sha256": hashlib.sha256(json.dumps(metadata_records, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "provenance": {
                "complete": bool(metadata_records),
                "product_split_leakage": False,
                "images": len(metadata_records),
                "positive_images": positive_images,
                "negative_images": negative_images,
                "objects": object_count,
                "condition_counts": dict(sorted(condition_counts.items())),
                "records": metadata_records,
            },
        })
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload


def has_real_files(directory: Path) -> bool:
    """True if `directory` has anything besides known placeholders."""
    if not directory.is_dir():
        return False
    return any(p.name not in _PLACEHOLDER_NAMES for p in directory.iterdir())


def resolve_split_dirs(data_yaml: Path, splits: tuple[str, ...]) -> list[Path]:
    """Derive the image AND label directories a dataset.yaml actually points
    to for the given split key(s), the same way Ultralytics itself does:
    `path:` (if present) + each split's path for images, and an
    images->labels substitution for labels. A split key absent from the
    yaml (e.g. `test` before it's populated) is silently skipped rather than
    treated as an error - callers decide whether that's acceptable for their
    use case.
    """
    with data_yaml.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    root = data_yaml.parent
    if config.get("path"):
        root = (data_yaml.parent / config["path"]).resolve()

    dirs: list[Path] = []
    for split_key in splits:
        split_value = config.get(split_key)
        if not split_value:
            continue
        image_dir = (root / split_value).resolve()
        dirs.append(image_dir)
        # Ultralytics locates labels by replacing the LAST "images" path
        # segment with "labels" - mirrored here rather than assuming a
        # fixed images/<split> <-> labels/<split> layout.
        parts = list(image_dir.parts)
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "images":
                parts[i] = "labels"
                dirs.append(Path(*parts))
                break
    return dirs
