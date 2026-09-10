"""
Dataset integrity checker for the LegalLense YOLO26 training set.

Checks the things Ultralytics itself won't warn you about until training
silently misbehaves: malformed label lines, out-of-range class IDs, a
`classes.txt` that's drifted from `dataset.yaml`, images with no label file
(and vice versa), and - the one that matters most for a valid evaluation -
the exact same photo appearing in more than one split (train/val/test data
leakage via accidental duplicate import, not just via bad splitting logic).

Does not modify any images or labels. Safe to run at any time, as often as
you like - this is meant to be run repeatedly during labeling, not just
once at the end (see training/LABELIMG_SETUP.md section 6).

Usage:

    backend\\.venv311\\Scripts\\python.exe training\\validate_dataset.py
    backend\\.venv311\\Scripts\\python.exe training\\validate_dataset.py --data training\\dataset.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

from _dataset_utils import resolve_split_dirs

TRAINING_DIR = Path(__file__).resolve().parent
DATASET_YAML = TRAINING_DIR / "dataset.yaml"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SPLITS = ("train", "val", "test")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the LegalLense YOLO26 dataset.")
    parser.add_argument("--data", default=str(DATASET_YAML), help=f"Path to dataset.yaml (default: {DATASET_YAML})")
    return parser


def _load_class_names(data_yaml: Path) -> dict[int, str]:
    with data_yaml.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    names = config.get("names")
    if names is None:
        raise SystemExit(f"{data_yaml}: no 'names:' key found")
    if isinstance(names, dict):
        return {int(k): v for k, v in names.items()}
    return dict(enumerate(names))


def _split_image_label_dirs(data_yaml: Path, split: str) -> tuple[Path, Path] | None:
    dirs = resolve_split_dirs(data_yaml, (split,))
    if len(dirs) < 2:
        return None  # split not defined in dataset.yaml, or has no images->labels sibling
    return dirs[0], dirs[1]


def _check_classes_txt(label_dir: Path, expected_names: dict[int, str], errors: list[str], split: str) -> None:
    classes_txt = label_dir / "classes.txt"
    if not classes_txt.exists():
        return  # not an error - LabelImg creates it on first save in that folder
    lines = [line.strip() for line in classes_txt.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected_list = [expected_names[i] for i in sorted(expected_names)]
    if lines != expected_list:
        errors.append(
            f"[{split}] {classes_txt} does not match dataset.yaml's names list.\n"
            f"    classes.txt: {lines}\n"
            f"    dataset.yaml: {expected_list}\n"
            f"    This is serious: LabelImg/Ultralytics identify classes by INDEX, not name - "
            f"a mismatch here means every saved label may already point at the wrong class."
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    data_yaml = Path(args.data).resolve()

    if not data_yaml.exists():
        print(f"Dataset config not found: {data_yaml}", file=sys.stderr)
        return 1

    class_names = _load_class_names(data_yaml)
    num_classes = len(class_names)

    errors: list[str] = []
    warnings: list[str] = []
    instance_counts: dict[str, dict[int, int]] = {}
    split_image_totals: dict[str, int] = {}
    image_hashes: dict[str, list[tuple[str, str]]] = {}  # sha256 -> [(split, filename), ...]

    any_split_found = False
    for split in SPLITS:
        pair = _split_image_label_dirs(data_yaml, split)
        if pair is None:
            print(f"[{split}] not defined in dataset.yaml - skipped")
            continue
        image_dir, label_dir = pair
        if not image_dir.is_dir():
            print(f"[{split}] {image_dir} does not exist yet - skipped")
            continue
        any_split_found = True

        images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
        split_image_totals[split] = len(images)
        labels = sorted(p for p in (label_dir.glob("*.txt") if label_dir.is_dir() else []) if p.name != "classes.txt")
        image_stems = {p.stem for p in images}
        label_stems = {p.stem for p in labels}

        unlabeled = sorted(image_stems - label_stems)
        orphans = sorted(label_stems - image_stems)
        if unlabeled:
            warnings.append(
                f"[{split}] {len(unlabeled)} image(s) with no label file (Ultralytics will skip them "
                f"entirely - fine if intentional, a gap if not): {unlabeled[:10]}"
                + (" ..." if len(unlabeled) > 10 else "")
            )
        if orphans:
            warnings.append(
                f"[{split}] {len(orphans)} orphan label file(s) with no matching image "
                f"(likely a rename/delete that missed the label): {orphans[:10]}"
                + (" ..." if len(orphans) > 10 else "")
            )

        _check_classes_txt(label_dir, class_names, errors, split)

        counts: dict[int, int] = {i: 0 for i in class_names}
        for label_path in labels:
            for line_no, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                tokens = line.split()
                if len(tokens) != 5:
                    errors.append(f"[{split}] {label_path}:{line_no}: expected 5 values, got {len(tokens)}: {line!r}")
                    continue
                class_token, *coord_tokens = tokens
                try:
                    class_id = int(class_token)
                except ValueError:
                    errors.append(f"[{split}] {label_path}:{line_no}: class id {class_token!r} is not an integer")
                    continue
                if class_id not in class_names:
                    errors.append(
                        f"[{split}] {label_path}:{line_no}: class id {class_id} out of range "
                        f"(valid: 0-{num_classes - 1})"
                    )
                    continue
                bad_coords = []
                for token in coord_tokens:
                    try:
                        value = float(token)
                    except ValueError:
                        bad_coords.append(token)
                        continue
                    if not (0.0 <= value <= 1.0):
                        bad_coords.append(token)
                if bad_coords:
                    errors.append(
                        f"[{split}] {label_path}:{line_no}: coordinate(s) not in [0,1]: {bad_coords}"
                    )
                    continue
                counts[class_id] += 1
        instance_counts[split] = counts

        for image_path in images:
            digest = _sha256(image_path)
            image_hashes.setdefault(digest, []).append((split, image_path.name))

    if not any_split_found:
        print(
            "\nNo populated splits found at all - nothing to validate yet. "
            "See training/README.md / training/LABELING_GUIDE.md to get started."
        )
        return 0

    # "train/val/test classes are represented" - only meaningful for splits
    # that actually have images yet; an all-placeholder split reporting
    # every class as zero isn't a finding, it's the starting state.
    for split, counts in instance_counts.items():
        if split_image_totals.get(split, 0) == 0:
            continue
        zero_classes = [class_names[cid] for cid, count in counts.items() if count == 0]
        if zero_classes:
            warnings.append(
                f"[{split}] has images but zero instances of: {zero_classes}. "
                f"If this split is meant to be representative, these classes are "
                f"missing coverage - see training/COLLECTION_PLAN.md."
            )

    # Cross-split duplicate detection - the one check that directly protects
    # against train/val/test leakage from an accidentally duplicated photo,
    # not just from bad splitting logic.
    for digest, occurrences in image_hashes.items():
        splits_involved = {split for split, _ in occurrences}
        if len(splits_involved) > 1:
            errors.append(
                f"DATA LEAKAGE: identical image content appears in multiple splits: "
                f"{occurrences} (sha256 {digest[:12]}...). Remove all but one copy, or "
                f"if these are meant to be different photos of the same product, confirm "
                f"they are not byte-for-byte identical files."
            )
        elif len(occurrences) > 1:
            warnings.append(
                f"Duplicate image content within the same split ({occurrences[0][0]}): "
                f"{[name for _, name in occurrences]} - wastes labeling effort on a repeat, "
                f"consider removing per training/LABELING_GUIDE.md section G.4."
            )

    print("\n" + "=" * 72)
    print("Per-class instance counts")
    print("=" * 72)
    header = f"{'class':<24}" + "".join(f"{split:>10}" for split in instance_counts)
    print(header)
    for class_id in sorted(class_names):
        row = f"{class_names[class_id]:<24}"
        for split in instance_counts:
            row += f"{instance_counts[split][class_id]:>10}"
        print(row)

    if warnings:
        print("\n" + "=" * 72)
        print(f"Warnings ({len(warnings)})")
        print("=" * 72)
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("\n" + "=" * 72)
        print(f"ERRORS ({len(errors)}) - fix these before training")
        print("=" * 72)
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nNo errors found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
