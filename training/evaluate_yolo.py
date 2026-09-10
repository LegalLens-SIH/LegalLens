"""
Evaluate a fine-tuned LegalLense YOLO26 checkpoint against a held-out
split, reporting PER-CLASS precision/recall (not just an overall score) -
the whole point being to tell whether the model actually learned the 8
Legal Metrology declaration classes individually, rather than doing well on
whichever class happens to be most common in your dataset while failing the
rest.

Does not modify backend/ocr/yolo_service.py or affect the running app.

Usage:

    backend\\.venv311\\Scripts\\python.exe training\\evaluate_yolo.py
    backend\\.venv311\\Scripts\\python.exe training\\evaluate_yolo.py --weights models/legal_lense_yolo26.pt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from _dataset_utils import has_real_files, resolve_split_dirs

# Same rationale as train_yolo.py: no silent auto-installs, no HUB sync.
os.environ.setdefault("YOLO_AUTOINSTALL", "False")

TRAINING_DIR = Path(__file__).resolve().parent
PORTAL_DIR = TRAINING_DIR.parent
DATASET_YAML = TRAINING_DIR / "dataset.yaml"
DEFAULT_WEIGHTS = PORTAL_DIR / "models" / "legal_lense_yolo26.pt"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report per-class precision/recall for a fine-tuned LegalLense YOLO26 checkpoint."
    )
    parser.add_argument(
        "--weights", default=str(DEFAULT_WEIGHTS),
        help=f"Path to the fine-tuned checkpoint (default: {DEFAULT_WEIGHTS})",
    )
    parser.add_argument(
        "--data", default=str(DATASET_YAML),
        help=f"Path to dataset.yaml (default: {DATASET_YAML})",
    )
    parser.add_argument(
        "--split", default="val", choices=["val", "test", "train"],
        help="Which dataset.yaml split to evaluate against (default: val - the held-out split)",
    )
    parser.add_argument(
        "--device", default="cpu",
        help="'cpu', or a CUDA device index like '0' if you have a GPU (default: cpu)",
    )
    parser.add_argument(
        "--imgsz", type=int, default=640,
        help="Evaluation image size (default: 640, should normally match training)",
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    weights_path = Path(args.weights)
    if not weights_path.exists():
        print(
            f"No checkpoint found at {weights_path}.\n"
            "Run training\\train_yolo.py first (see training/README.md).",
            file=sys.stderr,
        )
        return 1

    split_dirs = resolve_split_dirs(Path(args.data).resolve(), (args.split,))
    if not split_dirs:
        print(
            f"'{args.split}:' is not defined in {args.data} - nothing to evaluate against.",
            file=sys.stderr,
        )
        return 1
    missing = [str(d) for d in split_dirs if not has_real_files(d)]
    if missing:
        print(
            f"No labeled '{args.split}' data found yet - refusing to evaluate against an "
            "empty/placeholder split.\n\nStill missing real files in:\n  "
            + "\n  ".join(missing)
            + (
                "\n\nThe 'test' split is meant to stay empty until you're past the pilot "
                "stage (see training/DATASET_STRUCTURE.md) - use --split val until then."
                if args.split == "test"
                else "\n\nSee training/README.md for the expected annotation format."
            ),
            file=sys.stderr,
        )
        return 1

    from ultralytics import YOLO
    from ultralytics import settings as yolo_settings

    yolo_settings.update({"sync": False})

    model = YOLO(str(weights_path))
    results = model.val(
        data=args.data, split=args.split, device=args.device, imgsz=args.imgsz,
        project=str(TRAINING_DIR / "runs"), name="eval", exist_ok=True,
    )

    print("\n" + "=" * 72)
    print(f"Weights: {weights_path}")
    print(f"Split:   {args.split}  (data={args.data})")
    print("=" * 72)

    mp, mr, map50, map50_95 = results.box.mean_results()
    print(f"\nOverall (mean across classes): precision={mp:.3f}  recall={mr:.3f}  "
          f"mAP50={map50:.3f}  mAP50-95={map50_95:.3f}")

    print("\nPer-class:")
    header = f"{'class':<24}{'images':>8}{'instances':>11}{'precision':>11}{'recall':>9}{'mAP50':>9}{'mAP50-95':>10}"
    print(header)
    print("-" * len(header))

    seen_classes: set[str] = set()
    if hasattr(results, "summary"):
        # Preferred: ultralytics' own per-class summary (Ultralytics >= 8.3-ish).
        for row in results.summary():
            seen_classes.add(row["Class"])
            print(
                f"{row['Class']:<24}{row['Images']:>8}{row['Instances']:>11}"
                f"{row['Box-P']:>11.3f}{row['Box-R']:>9.3f}{row['mAP50']:>9.3f}{row['mAP50-95']:>10.3f}"
            )
    else:
        # Fallback for older/newer API shapes that don't expose summary().
        names = results.names
        for i, class_id in enumerate(results.box.ap_class_index):
            seen_classes.add(names[class_id])
            p, r, ap50, ap = results.box.class_result(i)
            print(f"{names[class_id]:<24}{'':>8}{'':>11}{p:>11.3f}{r:>9.3f}{ap50:>9.3f}{ap:>10.3f}")

    missing_classes = set(results.names.values()) - seen_classes
    if missing_classes:
        print(
            f"\nNote: no validation instances at all for: {sorted(missing_classes)}. "
            "Their precision/recall cannot be computed - add labeled val examples "
            "for these classes before trusting this model on them."
        )

    confusion_matrix = getattr(results, "confusion_matrix", None)
    if confusion_matrix is not None and getattr(confusion_matrix, "matrix", None) is not None:
        matrix = confusion_matrix.matrix
        names = results.names
        nc = len(names)
        print("\nConfusion summary (per class, this split):")
        conf_header = f"{'class':<24}{'TP':>6}{'FP':>6}{'FN':>6}"
        print(conf_header)
        print("-" * len(conf_header))
        for class_id in sorted(names):
            tp = int(matrix[class_id, class_id])
            fp = int(matrix[class_id, :].sum()) - tp  # predicted this class, ground truth was something else (incl. background)
            fn = int(matrix[:, class_id].sum()) - tp  # ground truth this class, predicted something else (incl. missed)
            print(f"{names[class_id]:<24}{tp:>6}{fp:>6}{fn:>6}")
        print(
            "\nFP = model predicted this class where the truth was something else or nothing there; "
            "FN = this class was actually present but the model missed it or called it something else. "
            "Full confusion matrix plot (all class-to-class confusions, not just per-class totals) is "
            "saved alongside this run - see the 'Results saved to' path printed above "
            "(confusion_matrix.png / confusion_matrix_normalized.png)."
        )

    print(
        "\nA class with high recall but low precision is producing false positives "
        "(boxes where the field isn't actually present); low recall means it's "
        "missing real instances. Both call for more/better-varied labeled examples "
        "of that specific class, per training/README.md."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
