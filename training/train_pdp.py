from __future__ import annotations

import sys
from pathlib import Path

from train_yolo import main as train_yolo_main

TRAINING_DIR = Path(__file__).resolve().parent
DATASET = TRAINING_DIR / "pdp" / "v1" / "dataset.yaml"
OUTPUT = TRAINING_DIR.parent / "models" / "legal_lense_pdp_v1.pt"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return train_yolo_main(["--data", str(DATASET), "--output", str(OUTPUT), *args])


if __name__ == "__main__":
    raise SystemExit(main())
