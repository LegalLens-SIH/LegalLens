from __future__ import annotations

import math
import os
import shutil
from collections import OrderedDict
from typing import Any

try:
    import pytesseract
except Exception:  # pragma: no cover - exercised only when dependency is absent
    pytesseract = None

_ALLOWED_PSM = {3, 4, 6, 11, 12}


class TesseractOCRService:
    def __init__(self, *, enabled: bool | None = None, binary_path: str | None = None) -> None:
        requested = os.getenv("TESSERACT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.requested = requested if enabled is None else enabled
        configured_binary = os.getenv("TESSERACT_BINARY_PATH", "").strip()
        tesseract_binary = binary_path or configured_binary or shutil.which("tesseract")
        self.enabled = self.requested and pytesseract is not None and bool(tesseract_binary)
        self.tesseract_binary = tesseract_binary
        self.language = os.getenv("TESSERACT_LANGUAGE", "eng").strip() or "eng"
        try:
            page_segmentation_mode = int(os.getenv("TESSERACT_PSM", "6"))
        except ValueError:
            page_segmentation_mode = 6
        self.page_segmentation_mode = page_segmentation_mode if page_segmentation_mode in _ALLOWED_PSM else 6

    @staticmethod
    def _line_detections(data: dict[str, Any]) -> list[dict[str, Any]]:
        required = ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")
        if not isinstance(data, dict) or any(key not in data for key in required):
            return []
        line_map: OrderedDict[tuple[int, int, int], dict[str, Any]] = OrderedDict()
        word_count = min(len(data[key]) for key in required)
        for index in range(word_count):
            text = str(data["text"][index] or "").strip()
            try:
                raw_confidence = float(data["conf"][index])
                left = int(data["left"][index])
                top = int(data["top"][index])
                width = int(data["width"][index])
                height = int(data["height"][index])
                line_key = (int(data["block_num"][index]), int(data["par_num"][index]), int(data["line_num"][index]))
            except (TypeError, ValueError, OverflowError):
                continue
            if not text or not math.isfinite(raw_confidence) or raw_confidence < 0 or width <= 0 or height <= 0:
                continue
            line = line_map.setdefault(line_key, {
                "words": [], "confidences": [], "x1": left, "y1": top,
                "x2": left + width, "y2": top + height,
            })
            line["words"].append(text)
            line["confidences"].append(min(raw_confidence, 100.0) / 100.0)
            line["x1"] = min(line["x1"], left)
            line["y1"] = min(line["y1"], top)
            line["x2"] = max(line["x2"], left + width)
            line["y2"] = max(line["y2"], top + height)

        detections = []
        for line in line_map.values():
            x1, y1, x2, y2 = line["x1"], line["y1"], line["x2"], line["y2"]
            detections.append({
                "text": " ".join(line["words"]),
                "confidence": sum(line["confidences"]) / len(line["confidences"]),
                "bbox": [x1, y1, x2, y2],
                "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "source": "tesseract",
                "provenance": ["tesseract"],
            })
        return detections

    def run(self, image: Any | None) -> dict[str, Any]:
        if image is None:
            return {"success": False, "error_code": "missing_input", "message": "No image supplied"}

        if not self.enabled or pytesseract is None:
            return {
                "success": False,
                "error_code": "not_available",
                "message": "Tesseract OCR is disabled or unavailable. Enable TESSERACT_ENABLED and install the optional Python package and native binary.",
                "engine": "tesseract",
                "provenance": "secondary_review_only",
            }

        try:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_binary
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                config=f"--oem 3 --psm {self.page_segmentation_mode}",
                output_type=pytesseract.Output.DICT,
            )
            detections = self._line_detections(data)
            text = "\n".join(detection["text"] for detection in detections)
            return {
                "success": True,
                "text": text.strip(),
                "full_text": text.strip(),
                "detections": detections,
                "detection_count": len(detections),
                "engine": "tesseract",
                "provenance": "secondary_review_only",
                "error_code": None,
                "message": "OCR completed successfully",
            }
        except Exception as exc:  # pragma: no cover - runtime/native OCR failures depend on image + binary
            return {
                "success": False,
                "error_code": "ocr_failed",
                "message": f"Tesseract OCR failed: {exc}",
                "engine": "tesseract",
                "provenance": "secondary_review_only",
            }
