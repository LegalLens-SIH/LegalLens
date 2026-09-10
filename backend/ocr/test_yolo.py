"""
CLI test/validation script for the YOLO26 localization service.

Run a real image through real Ultralytics YOLO26, print what was detected,
and save the structured JSON result. This is a smoke test / manual
validation tool, not a compliance checker - it only proves the YOLO26
localization step loads and runs end to end. Mirrors ocr/test_ocr.py.

Usage (from the `Portal` directory, with the backend virtualenv active):

    python -m backend.ocr.test_yolo path/to/product.jpg
    python -m backend.ocr.test_yolo path/to/product.jpg --model yolo26n.pt --conf 0.25
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.ocr.paddle_ocr_service import PaddleOCRService
    from backend.ocr.yolo_service import YOLOService
else:
    from .paddle_ocr_service import PaddleOCRService
    from .yolo_service import YOLOService

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "ocr_output"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run YOLO26 localization on a single product image.")
    parser.add_argument("image", help="Path to a JPG/JPEG/PNG/WEBP product image")
    parser.add_argument("--model", default=None, help="Model path/name (default: $YOLO_MODEL_PATH or yolo26n.pt)")
    parser.add_argument("--conf", type=float, default=None, help="Confidence threshold (default: $YOLO_CONFIDENCE_THRESHOLD or 0.25)")
    parser.add_argument("--device", default=None, help="cpu/cuda:0 (default: auto-detect)")
    parser.add_argument("--out", default=None, help="Path to write the JSON result")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("legallense.ocr.test_yolo")

    kwargs = {}
    if args.model:
        kwargs["model_path"] = args.model
    if args.conf is not None:
        kwargs["confidence"] = args.conf
    if args.device:
        kwargs["device"] = args.device

    service = YOLOService(**kwargs)

    image = PaddleOCRService.load_image(PaddleOCRService.validate_image_path(args.image))

    print("\n" + "=" * 60)
    print(f"Image:   {args.image}")
    print(f"Model:   {service.model_path}")
    print(f"Device:  {service.device}")

    try:
        detections = service.detect(image)
    except Exception as exc:
        logger.error("YOLO detection failed: %s", exc)
        print(f"FAILED: {exc}")
        return 1

    print(f"Detections: {len(detections)}")
    print("=" * 60)

    for i, det in enumerate(detections, start=1):
        box = det.bbox
        print(f"[{i:02d}] class={det.class_name!r} (id={det.class_id}) conf={det.confidence:.4f} "
              f"bbox=({box.x1},{box.y1},{box.x2},{box.y2})")

    region = YOLOService.best_crop_region(image, detections)
    print(f"\nBest crop region: {region}")

    out_path = Path(args.out) if args.out else DEFAULT_OUTPUT_DIR / f"{Path(args.image).stem}_yolo.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "image": args.image,
        "model": service.model_path,
        "device": service.device,
        "detections": [d.model_dump() for d in detections],
        "best_crop_region": region,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON result written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
