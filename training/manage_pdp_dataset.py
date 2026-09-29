from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

try:
    from ._dataset_utils import build_dataset_manifest
except ImportError:
    from _dataset_utils import build_dataset_manifest

TRAINING_DIR = Path(__file__).resolve().parent
DATASET_DIR = TRAINING_DIR / "pdp" / "v1"
DATASET_YAML = DATASET_DIR / "dataset.yaml"
METADATA_PATH = DATASET_DIR / "metadata.jsonl"
SPLITS = ("train", "val", "test")
MAX_IMAGE_BYTES = 100 * 1024 * 1024
ANNOTATION_VERSION = "pdp-bbox-yolo-v1"
CONDITIONS = {
    "front_facing", "perspective", "rotated", "glossy", "glare", "low_light",
    "curved_cylindrical", "partial_occlusion", "multiple_packages", "clutter",
    "hard_negative_nutrition_panel", "hard_negative_barcode", "hard_negative_side_panel",
    "hard_negative_secondary_packaging", "hard_negative_background", "hard_negative_decorative_region",
}


class PDPDatasetError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_metadata() -> list[dict]:
    if not METADATA_PATH.is_file():
        return []
    records = []
    for line_number, line in enumerate(METADATA_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PDPDatasetError(f"Invalid metadata JSON on line {line_number}") from exc
        if not isinstance(record, dict):
            raise PDPDatasetError(f"Metadata line {line_number} must be an object")
        records.append(record)
    return records


def _validated_label_text(label_path: Path) -> tuple[str, int]:
    try:
        text = label_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PDPDatasetError("Annotation must be a readable UTF-8 YOLO label file") from exc
    boxes = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise PDPDatasetError(f"Annotation line {line_number} must contain class and four box values")
        try:
            class_id = int(fields[0])
            center_x, center_y, width, height = (float(value) for value in fields[1:])
        except (ValueError, OverflowError) as exc:
            raise PDPDatasetError(f"Annotation line {line_number} contains invalid numeric values") from exc
        if class_id != 0:
            raise PDPDatasetError("PDP v1 accepts only class 0: principal_display_panel")
        values = (center_x, center_y, width, height)
        if not all(math.isfinite(value) for value in values) or width <= 0 or height <= 0:
            raise PDPDatasetError(f"Annotation line {line_number} has invalid geometry")
        if any(value < 0 or value > 1 for value in values):
            raise PDPDatasetError(f"Annotation line {line_number} coordinates must be normalized to [0, 1]")
        if center_x - width / 2 < 0 or center_x + width / 2 > 1 or center_y - height / 2 < 0 or center_y + height / 2 > 1:
            raise PDPDatasetError(f"Annotation line {line_number} extends outside the image")
        boxes += 1
    return text, boxes


def import_example(
    *,
    image_path: Path,
    label_path: Path,
    split: str,
    product_identity: str,
    source: str,
    license_name: str,
    category: str,
    conditions: list[str],
    operator: str,
    capture_date: str | None,
    rights_confirmed: bool,
    negative: bool,
) -> dict:
    if split not in SPLITS:
        raise PDPDatasetError("split must be train, val, or test")
    if not rights_confirmed:
        raise PDPDatasetError("Confirm image ownership or license permission before importing")
    for name, value in (("product identity", product_identity), ("source", source), ("license", license_name), ("category", category), ("operator", operator)):
        if not value.strip():
            raise PDPDatasetError(f"{name} is required")
    if not conditions or any(condition not in CONDITIONS for condition in conditions):
        raise PDPDatasetError(f"conditions must be selected from: {', '.join(sorted(CONDITIONS))}")
    if capture_date:
        try:
            date.fromisoformat(capture_date)
        except ValueError as exc:
            raise PDPDatasetError("capture_date must use YYYY-MM-DD") from exc

    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        raise PDPDatasetError("Source image could not be read") from exc
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise PDPDatasetError("Image must be between 1 byte and 100 MB")
    try:
        with Image.open(image_path) as image:
            image_format = image.format
            dimensions = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise PDPDatasetError("Source file is not a valid image") from exc
    suffix = image_path.suffix.lower()
    if image_format not in {"JPEG", "PNG", "WEBP", "BMP", "TIFF"} or suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        raise PDPDatasetError("Unsupported image format")

    label_text, object_count = _validated_label_text(label_path)
    if negative and object_count:
        raise PDPDatasetError("A negative example must have an empty annotation file")
    if not negative and not object_count:
        raise PDPDatasetError("Empty annotations require explicit --negative confirmation")

    image_digest = _sha256(image_bytes)
    label_bytes = label_text.encode("utf-8")
    label_digest = _sha256(label_bytes)
    product_digest = hashlib.sha256(product_identity.strip().casefold().encode("utf-8")).hexdigest()
    for existing in _read_metadata():
        if existing.get("product_identity_sha256") == product_digest and existing.get("split") != split:
            raise PDPDatasetError("All images of one product identity must remain in the same split")
        if existing.get("image_sha256") == image_digest:
            raise PDPDatasetError("This exact image is already present in the dataset")

    image_name = f"{image_digest}{suffix}"
    image_target = DATASET_DIR / "images" / split / image_name
    label_target = DATASET_DIR / "labels" / split / f"{image_digest}.txt"
    image_target.parent.mkdir(parents=True, exist_ok=True)
    label_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(image_path, image_target)
    label_target.write_bytes(label_bytes)
    record = {
        "split": split,
        "image": image_name,
        "image_sha256": image_digest,
        "label_sha256": label_digest,
        "image_width": int(dimensions[0]),
        "image_height": int(dimensions[1]),
        "product_identity_sha256": product_digest,
        "source": source.strip(),
        "license": license_name.strip(),
        "category": category.strip(),
        "conditions": sorted(set(conditions)),
        "capture_date": capture_date or None,
        "operator": operator.strip(),
        "rights_confirmation": "operator_confirmed",
        "annotation_version": ANNOTATION_VERSION,
        "annotation_status": "positive" if object_count else "negative",
        "object_count": object_count,
        "annotation_format": "yolo_axis_aligned_bbox",
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    with METADATA_PATH.open("a", encoding="utf-8", newline="\n") as metadata_file:
        metadata_file.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return record


def dataset_report() -> dict:
    manifest = build_dataset_manifest(DATASET_YAML, SPLITS)
    provenance = manifest.get("provenance") or {}
    records = provenance.get("records", [])
    categories: dict[str, int] = {}
    sources: dict[str, int] = {}
    dimensions: dict[str, int] = {}
    conditions: dict[str, int] = {}
    for record in records:
        categories[record["category"]] = categories.get(record["category"], 0) + 1
        sources[record["source"]] = sources.get(record["source"], 0) + 1
        size = f"{record.get('image_width', 0)}x{record.get('image_height', 0)}"
        dimensions[size] = dimensions.get(size, 0) + 1
        for condition in record.get("conditions", []):
            conditions[condition] = conditions.get(condition, 0) + 1
    return {
        "dataset_id": manifest.get("dataset_id"),
        "dataset_version": manifest.get("dataset_version"),
        "manifest_sha256": manifest["manifest_sha256"],
        "class_names": manifest["class_names"],
        "splits": {
            name: {
                "images": split["images"],
                "labeled_images": split["labeled_images"],
            }
            for name, split in manifest["splits"].items()
        },
        "positive_images": provenance.get("positive_images", 0),
        "negative_images": provenance.get("negative_images", 0),
        "objects": provenance.get("objects", 0),
        "condition_counts": provenance.get("condition_counts", {}),
        "category_distribution": dict(sorted(categories.items())),
        "source_distribution": dict(sorted(sources.items())),
        "image_dimensions": dict(sorted(dimensions.items())),
        "condition_distribution": dict(sorted(conditions.items())),
        "provenance_complete": provenance.get("complete", False),
        "product_split_leakage": provenance.get("product_split_leakage", True),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import and validate locally licensed PDP detector examples.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser("import", help="Import one locally held and rights-cleared image/annotation pair")
    importer.add_argument("--image", type=Path, required=True)
    importer.add_argument("--label", type=Path, required=True, help="YOLO class-0 bbox file; use an empty file only with --negative")
    importer.add_argument("--split", choices=SPLITS, required=True)
    importer.add_argument("--product-identity", required=True, help="Used only to derive a split-leakage hash; raw identity is not stored")
    importer.add_argument("--source", required=True, help="Acquisition/source provenance; no scraping is performed")
    importer.add_argument("--license", dest="license_name", required=True, help="License/ownership basis supplied by the importer")
    importer.add_argument("--category", required=True)
    importer.add_argument("--condition", action="append", choices=sorted(CONDITIONS), required=True, help="Capture condition tag; repeat for multiple conditions")
    importer.add_argument("--operator", required=True)
    importer.add_argument("--capture-date")
    importer.add_argument("--rights-confirmed", action="store_true", help="Confirm the operator has rights to use the image for model training")
    importer.add_argument("--negative", action="store_true", help="Explicitly confirm no PDP region is present")
    subparsers.add_parser("validate", help="Validate provenance, labels, hashes, and product-independent splits")
    subparsers.add_parser("stats", help="Print deterministic split and positive/negative statistics")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            record = import_example(
                image_path=args.image, label_path=args.label, split=args.split,
                product_identity=args.product_identity, source=args.source,
                license_name=args.license_name, category=args.category, conditions=args.condition,
                operator=args.operator, capture_date=args.capture_date,
                rights_confirmed=args.rights_confirmed, negative=args.negative,
            )
            print(json.dumps({"imported": True, "image": record["image"], "split": record["split"], "annotation_status": record["annotation_status"]}, indent=2))
        else:
            print(json.dumps(dataset_report(), indent=2, sort_keys=True))
    except (PDPDatasetError, OSError, ValueError) as exc:
        print(f"PDP dataset error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
