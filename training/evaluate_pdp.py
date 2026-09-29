from __future__ import annotations

import sys
from pathlib import Path

from evaluate_yolo import main as evaluate_yolo_main

TRAINING_DIR = Path(__file__).resolve().parent
DATASET = TRAINING_DIR / "pdp" / "v1" / "dataset.yaml"
WEIGHTS = TRAINING_DIR.parent / "models" / "legal_lense_pdp_v1.pt"
REPORT = TRAINING_DIR / "pdp" / "v1" / "runs" / "validation.json"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return evaluate_yolo_main([
        "--weights", str(WEIGHTS), "--data", str(DATASET),
        "--report", str(REPORT), "--split", "test", *args,
    ])


if __name__ == "__main__":
    raise SystemExit(main())
