"""
Fine-tune yolo26n.pt on the LegalLense Legal Metrology label-field dataset.

Scaffolding tool - this trains whatever is actually present under
training/images/{train,val} and training/labels/{train,val}. No images or
labels are bundled with this script (see training/README.md); running it
against the placeholder .gitkeep-only folders fails fast with a clear
message rather than training on nothing.

Does NOT modify backend/ocr/yolo_service.py or affect the running app in any
way - it only writes a new checkpoint to models/legal_lense_yolo26.pt.
Point YOLO_MODEL_PATH at that file (in Portal/.env) once you've validated it
with evaluate_yolo.py.

Usage:

    backend\\.venv311\\Scripts\\python.exe training\\train_yolo.py
    backend\\.venv311\\Scripts\\python.exe training\\train_yolo.py --epochs 100 --batch 4
    backend\\.venv311\\Scripts\\python.exe training\\train_yolo.py --base yolo26n.pt --device cpu
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from _dataset_utils import has_real_files, resolve_split_dirs

# Disable ultralytics' global auto-install-missing-packages behavior and its
# Ultralytics HUB sync BEFORE importing ultralytics - this project's
# dependency set is deliberately pinned (see backend/requirements.txt and
# backend/constraints.txt around opencv-python vs opencv-python-headless),
# and training images may be proprietary/sensitive, so neither silent
# package installs nor telemetry/upload behavior are acceptable here.
os.environ.setdefault("YOLO_AUTOINSTALL", "False")

TRAINING_DIR = Path(__file__).resolve().parent
PORTAL_DIR = TRAINING_DIR.parent
DATASET_YAML = TRAINING_DIR / "dataset.yaml"
DEFAULT_BASE_MODEL = os.getenv("YOLO_BASE_MODEL", "yolo26n.pt")
DEFAULT_OUTPUT_MODEL = PORTAL_DIR / "models" / "legal_lense_yolo26.pt"
RUNS_DIR = TRAINING_DIR / "runs"
RUN_NAME = "legal_lense_yolo26"


def _check_dataset_ready(data_yaml: Path) -> None:
    # Only train/val are required to actually train - test/ is intentionally
    # never touched here (see dataset.yaml's `test:` comment): train_yolo.py
    # must never be able to see test data, by construction, not just by
    # convention.
    missing = [
        str(d) for d in resolve_split_dirs(data_yaml, ("train", "val")) if not has_real_files(d)
    ]
    if missing:
        print(
            "No labeled dataset found yet - refusing to train on an empty/placeholder "
            "dataset.\n\nStill missing real files in:\n  "
            + "\n  ".join(missing)
            + "\n\nSee training/README.md for the expected annotation format, "
              "recommended tools (LabelImg/Roboflow), and how many labeled "
              "images to start with.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fine-tune yolo26n.pt on the LegalLense label-field dataset."
    )
    parser.add_argument(
        "--base", default=DEFAULT_BASE_MODEL,
        help=f"Base checkpoint to fine-tune from (default: {DEFAULT_BASE_MODEL})",
    )
    parser.add_argument(
        "--data", default=str(DATASET_YAML),
        help=f"Path to dataset.yaml (default: {DATASET_YAML})",
    )
    parser.add_argument(
        "--epochs", type=int, default=60,
        help="Training epochs (default: 60 - a small dataset benefits from more "
             "epochs than a large one; --patience below stops early if it plateaus)",
    )
    parser.add_argument(
        "--patience", type=int, default=15,
        help="Early-stopping patience in epochs with no val improvement (default: 15)",
    )
    parser.add_argument(
        "--batch", type=int, default=4,
        help="Batch size (default: 4 - conservative for CPU training and small datasets)",
    )
    parser.add_argument(
        "--imgsz", type=int, default=640,
        help="Training image size (default: 640, YOLO's standard default)",
    )
    parser.add_argument(
        "--device", default="cpu",
        help="'cpu', or a CUDA device index like '0' if you have a GPU (default: cpu)",
    )
    parser.add_argument(
        "--workers", type=int, default=0,
        help="Dataloader worker processes (default: 0 - avoids multiprocessing "
             "overhead/issues on Windows for a small dataset)",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT_MODEL),
        help=f"Where to copy the best checkpoint (default: {DEFAULT_OUTPUT_MODEL})",
    )
    parser.add_argument(
        "--skip-dataset-check", action="store_true",
        help="Skip the placeholder-dataset guard (advanced/debugging only)",
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.skip_dataset_check:
        _check_dataset_ready(Path(args.data).resolve())

    # Imported after YOLO_AUTOINSTALL is set, and after the dataset check, so
    # a missing-dataset error surfaces before paying for ultralytics' import cost.
    from ultralytics import YOLO
    from ultralytics import settings as yolo_settings

    # Keep training fully local: do not sync run metadata/images to Ultralytics HUB.
    yolo_settings.update({"sync": False})

    print(f"Base model:  {args.base}")
    print(f"Dataset:     {args.data}")
    print(f"Epochs:      {args.epochs} (patience={args.patience})")
    print(f"Batch size:  {args.batch}")
    print(f"Image size:  {args.imgsz}")
    print(f"Device:      {args.device}")
    print(f"Runs dir:    {RUNS_DIR}")

    model = YOLO(args.base)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        patience=args.patience,
        batch=args.batch,
        imgsz=args.imgsz,
        device=args.device,
        workers=args.workers,
        project=str(RUNS_DIR),
        name=RUN_NAME,
        exist_ok=True,
        verbose=True,
    )

    best_weights = Path(results.save_dir) / "weights" / "best.pt"
    if not best_weights.exists():
        print(f"Training finished but no best.pt was found at {best_weights}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_weights, output_path)

    print(f"\nBest checkpoint copied to: {output_path}")
    print(f"Full training run (logs, curves, all epoch weights): {results.save_dir}")
    print(
        "\nNext step: backend\\.venv311\\Scripts\\python.exe training\\evaluate_yolo.py "
        "-- validate per-class precision/recall before switching YOLO_MODEL_PATH over."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
