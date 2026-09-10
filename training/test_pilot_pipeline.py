"""
Pilot integration diagnostic: for EACH region a custom YOLO26 checkpoint
detects in a real photo, crop it and run the EXISTING, unmodified
PaddleOCRService on that crop, printing what was read per class.

Why this exists instead of just pointing the live app at the pilot model:
`backend/ocr/yolo_service.py`'s `best_crop_region()` picks ONE region (the
single largest by area) and crops the WHOLE image down to it before OCR
ever runs - correct for the current pretrained model's job (find the one
product/label region, discard background) but actively harmful for an
8-class declaration detector: picking "the single largest of 8 detected
declaration boxes" would crop away every declaration except whichever one
happens to be biggest, discarding the rest before OCR ever sees them. See
`training/CLASS_TAXONOMY.md`'s "Known integration gap" for the real
production integration this still needs (per-class multi-region OCR feeding
a rules engine that consumes per-class-tagged text - not built yet, out of
scope until a validated custom model exists).

This script exists so you can visually validate per-class detection + crop
+ OCR quality NOW, during the pilot, without that integration work and
without touching `backend/ocr/yolo_service.py` or any production code at
all - it only reads `PaddleOCRService`, never modifies it.

**Do not set YOLO_MODEL_PATH to a pilot/multi-class checkpoint in
Portal/.env** until the per-class integration above is actually built -
doing so today would make the live app's OCR quality worse, not better,
for the reason above.

Usage:

    backend\\.venv311\\Scripts\\python.exe training\\test_pilot_pipeline.py path\\to\\photo.jpg
    backend\\.venv311\\Scripts\\python.exe training\\test_pilot_pipeline.py path\\to\\photo.jpg --model ..\\models\\legal_lense_yolo26.pt
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("YOLO_AUTOINSTALL", "False")

TRAINING_DIR = Path(__file__).resolve().parent
PORTAL_DIR = TRAINING_DIR.parent
DEFAULT_MODEL = PORTAL_DIR / "models" / "legal_lense_yolo26.pt"

sys.path.insert(0, str(PORTAL_DIR))  # so `backend.ocr...` imports resolve regardless of cwd

from backend.ocr.paddle_ocr_service import PaddleOCRService  # noqa: E402
from backend.ocr.yolo_service import YOLOService  # noqa: E402


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Per-class crop+OCR diagnostic for a pilot/custom YOLO26 checkpoint."
    )
    parser.add_argument("image", help="Path to a real product photo")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Custom checkpoint (default: {DEFAULT_MODEL})")
    parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence threshold (default: 0.25)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--padding", type=float, default=0.05,
        help="Crop padding ratio around each detected box before OCR (default: 0.05)",
    )
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not Path(args.model).exists():
        print(f"No checkpoint found at {args.model}. Run training\\train_yolo.py first.", file=sys.stderr)
        return 1

    yolo = YOLOService(model_path=args.model, confidence=args.conf, device=args.device)
    ocr_service = PaddleOCRService()

    image = PaddleOCRService.load_image(PaddleOCRService.validate_image_path(args.image))
    detections = yolo.detect(image)

    print(f"\nImage: {args.image}")
    print(f"Model: {args.model}")
    print(f"Detections: {len(detections)}\n")

    if not detections:
        print(
            "No regions detected - nothing to OCR. This is itself useful pilot signal: "
            "either the model hasn't learned this image's declarations yet, or none are "
            "visible in this photo. Not necessarily a bug on its own."
        )
        return 0

    import cv2

    h, w = image.shape[:2]
    for i, det in enumerate(detections, start=1):
        box = det.bbox
        pad_x = int((box.x2 - box.x1) * args.padding)
        pad_y = int((box.y2 - box.y1) * args.padding)
        x1 = max(0, box.x1 - pad_x)
        y1 = max(0, box.y1 - pad_y)
        x2 = min(w, box.x2 + pad_x)
        y2 = min(h, box.y2 + pad_y)
        crop = image[y1:y2, x1:x2]

        print(f"[{i}] class={det.class_name!r} conf={det.confidence:.3f} bbox=({box.x1},{box.y1},{box.x2},{box.y2})")

        if crop.size == 0:
            print("    (empty crop - degenerate box coordinates, skipping OCR)\n")
            continue

        tmp_path = Path(tempfile.mktemp(suffix=".png"))
        try:
            cv2.imwrite(str(tmp_path), crop)
            result = ocr_service.run(str(tmp_path))
            if result.success:
                text = result.full_text.replace("\n", " / ")
                print(f"    OCR text: {text!r}")
            else:
                print(f"    OCR failed: [{result.error.code}] {result.error.message}")
        finally:
            tmp_path.unlink(missing_ok=True)
        print()

    print(
        "Review each class's OCR text above against training/CLASS_TAXONOMY.md's "
        "definition for that class - this is exactly the 'bad crops' / 'OCR failures "
        "caused by boxes' check the pilot phase is for. Wrong-looking or truncated text "
        "usually means the box is too tight/loose, or the model hasn't learned that class "
        "well yet - see training/COLLECTION_PLAN.md for what to add."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
