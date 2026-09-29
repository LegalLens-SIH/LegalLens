from __future__ import annotations

import math
import os
import shutil
from pathlib import Path
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
        configured_binary = binary_path if binary_path is not None else configured_binary
        configured_binary = configured_binary.strip() if configured_binary else ""
        if configured_binary:
            tesseract_binary = configured_binary if Path(configured_binary).is_file() else shutil.which(configured_binary)
        else:
            tesseract_binary = shutil.which("tesseract")
            if not tesseract_binary and os.name == "nt":
                program_files = Path(os.getenv("ProgramFiles", r"C:\Program Files"))
                default_install = program_files / "Tesseract-OCR" / "tesseract.exe"
                if default_install.is_file():
                    tesseract_binary = str(default_install)
        self.invalid_binary = bool(configured_binary and not tesseract_binary)
        self.enabled = self.requested and pytesseract is not None and bool(tesseract_binary)
        self.tesseract_binary = tesseract_binary
        self.language = os.getenv("TESSERACT_LANGUAGE", "eng").strip() or "eng"
        try:
            page_segmentation_mode = int(os.getenv("TESSERACT_PSM", "6"))
        except ValueError:
            page_segmentation_mode = 6
        self.page_segmentation_mode = page_segmentation_mode if page_segmentation_mode in _ALLOWED_PSM else 6
        try:
            timeout = float(os.getenv("TESSERACT_TIMEOUT_SECONDS", "12"))
            max_pixels = int(os.getenv("TESSERACT_MAX_PIXELS", "24000000"))
        except ValueError:
            timeout, max_pixels = 12.0, 24_000_000
        self.timeout_seconds = timeout if math.isfinite(timeout) and 1 <= timeout <= 60 else 12.0
        self.max_pixels = max_pixels if 1 <= max_pixels <= 50_000_000 else 24_000_000
        try:
            self.max_detections = min(5000, max(1, int(os.getenv("TESSERACT_MAX_DETECTIONS", "2000"))))
        except ValueError:
            self.max_detections = 2000

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
        return detections[:5000]

    def run(self, image: Any | None) -> dict[str, Any]:
        if image is None:
            return {"success": False, "error_code": "missing_input", "message": "No image supplied"}

        if self.requested and self.invalid_binary and pytesseract is not None:
            return {
                "success": False,
                "error_code": "invalid_binary",
                "message": "Configured Tesseract binary path does not identify an executable file.",
                "engine": "tesseract",
                "provenance": "secondary_review_only",
            }
        if not self.enabled or pytesseract is None:
            return {
                "success": False,
                "error_code": "not_available",
                "message": "Tesseract OCR is disabled or unavailable. Enable TESSERACT_ENABLED and install the optional Python package and native binary.",
                "engine": "tesseract",
                "provenance": "secondary_review_only",
            }

        size = getattr(image, "size", None)
        shape = getattr(image, "shape", None)
        try:
            width, height = (image.size if isinstance(image.size, tuple) else (shape[1], shape[0]))
        except (TypeError, AttributeError, IndexError):
            width = height = 0
        if not width or not height or width * height > self.max_pixels:
            return {"success": False, "error_code": "image_limit_exceeded", "message": "Image dimensions exceed the configured Tesseract pixel limit.", "engine": "tesseract", "provenance": "secondary_review_only"}

        try:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_binary
            available_languages = pytesseract.get_languages(config="")
            requested_languages = [language.strip() for language in self.language.split("+") if language.strip()]
            missing_languages = [language for language in requested_languages if language not in available_languages]
            if missing_languages:
                return {
                    "success": False,
                    "error_code": "language_unavailable",
                    "message": "One or more configured Tesseract language models are unavailable.",
                    "missing_languages": missing_languages,
                    "available_languages": available_languages,
                    "engine": "tesseract",
                    "provenance": "secondary_review_only",
                }
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                config=f"--oem 3 --psm {self.page_segmentation_mode}",
                output_type=pytesseract.Output.DICT,
                timeout=self.timeout_seconds,
            )
            required_output = ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")
            if not isinstance(data, dict) or any(key not in data for key in required_output):
                return {
                    "success": False,
                    "error_code": "malformed_output",
                    "message": "Tesseract returned malformed OCR data.",
                    "engine": "tesseract",
                    "provenance": "secondary_review_only",
                }
            detections = self._line_detections(data)[:self.max_detections]
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
        except RuntimeError as exc:  # pytesseract reports its subprocess timeout as RuntimeError.
            not_found_error = getattr(pytesseract, "TesseractNotFoundError", None)
            if not_found_error is not None and isinstance(exc, not_found_error):
                return {
                    "success": False,
                    "error_code": "not_available",
                    "message": "The configured Tesseract executable or language data is unavailable.",
                    "engine": "tesseract",
                    "provenance": "secondary_review_only",
                }
            timed_out = "timeout" in str(exc).lower()
            return {
                "success": False,
                "error_code": "timeout" if timed_out else "ocr_failed",
                "message": "Tesseract OCR timed out." if timed_out else f"Tesseract OCR failed ({type(exc).__name__}).",
                "engine": "tesseract",
                "provenance": "secondary_review_only",
            }
        except Exception as exc:  # pragma: no cover - runtime/native OCR failures depend on image + binary
            return {
                "success": False,
                "error_code": "ocr_failed",
                "message": f"Tesseract OCR failed ({type(exc).__name__}).",
                "engine": "tesseract",
                "provenance": "secondary_review_only",
            }
