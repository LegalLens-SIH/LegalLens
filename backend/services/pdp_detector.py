from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Callable

import yaml

from backend.services.pdp_geometry import evaluate_pdp_geometry

logger = logging.getLogger("legallense.services.pdp_detector")

PORTAL_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_PATH = PORTAL_DIR / "models" / "legal_lense_yolo26.pt"
DEFAULT_VALIDATION_PATH = PORTAL_DIR / "training" / "runs" / "legal_lense_pdp_validation.json"
DATASET_SCHEMA_PATH = PORTAL_DIR / "training" / "dataset.yaml"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_class_names(path: Path = DATASET_SCHEMA_PATH) -> dict[int, str]:
    with path.open(encoding="utf-8") as source:
        config = yaml.safe_load(source)
    names = config.get("names") if isinstance(config, dict) else None
    if not isinstance(names, dict) or not names:
        raise ValueError("PDP dataset schema has no class-name mapping")
    normalized: dict[int, str] = {}
    for class_id, name in names.items():
        normalized[int(class_id)] = str(name)
    return normalized


class PDPDetectorService:
    """Experimental PDP detector adapter; never an OCR or compliance authority.

    Loading requires an explicit enable flag, an existing checkpoint, an
    evaluator-generated report with a matching checkpoint SHA-256, the
    current dataset class schema, and an explicit operator-approved
    checkpoint SHA-256. Evaluation scripts themselves never promote a model.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        model_path: str | Path | None = None,
        validation_path: str | Path | None = None,
        confidence: float | None = None,
        validated_model_sha256: str | None = None,
        device: str | None = None,
        predictor: Callable[..., Any] | None = None,
    ) -> None:
        self.enabled = (
            os.getenv("LEGAL_LENSE_PDP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
            if enabled is None else enabled
        )
        self.model_path = Path(model_path or os.getenv("LEGAL_LENSE_PDP_MODEL_PATH", str(DEFAULT_MODEL_PATH))).resolve()
        self.validation_path = Path(validation_path or os.getenv("LEGAL_LENSE_PDP_VALIDATION_REPORT", str(DEFAULT_VALIDATION_PATH))).resolve()
        if confidence is None:
            try:
                raw_confidence = float(os.getenv("LEGAL_LENSE_PDP_CONFIDENCE", "0.25"))
            except ValueError:
                raw_confidence = 0.25
        else:
            raw_confidence = confidence
        self.confidence = raw_confidence if math.isfinite(raw_confidence) and 0 <= raw_confidence <= 1 else 0.25
        self.validated_model_sha256 = validated_model_sha256 or os.getenv("LEGAL_LENSE_PDP_VALIDATED_MODEL_SHA256", "").strip().lower()
        self.device = device or os.getenv("LEGAL_LENSE_PDP_DEVICE", "cpu").strip() or "cpu"
        self.predictor = predictor
        self._model: Any | None = None
        self._class_names: dict[int, str] | None = None
        self._validation: dict[str, Any] | None = None

    def _availability(self) -> tuple[str, str | None]:
        if not self.enabled:
            return "disabled", "Custom PDP inference is disabled by configuration."
        if self.model_path.suffix.lower() not in {".pt", ".onnx"}:
            return "checkpoint_invalid", "Custom PDP checkpoint must be a .pt or .onnx model."
        if not self.model_path.is_file():
            return "checkpoint_unavailable", "No custom LegalLense PDP checkpoint is installed."
        try:
            model_hash = _sha256_file(self.model_path)
        except OSError:
            return "checkpoint_unavailable", "Custom PDP checkpoint could not be read."
        if not self.validation_path.is_file():
            return "validation_unavailable", "A checkpoint-matched validation report is required."
        try:
            validation = json.loads(self.validation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "validation_invalid", "Custom PDP validation report is unreadable."
        if validation.get("status") != "evaluated":
            return "validation_invalid", "Validation report was not produced by the LegalLense evaluator."
        report_hash = validation.get("model_hash") or validation.get("checkpoint_sha256")
        if report_hash != model_hash:
            return "checkpoint_hash_mismatch", "Validation report does not match this checkpoint."
        if self.validated_model_sha256 != model_hash:
            return "validation_required", "Set LEGAL_LENSE_PDP_VALIDATED_MODEL_SHA256 only after reviewing this checkpoint's evaluation report."
        try:
            class_names = _load_class_names()
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            return "class_schema_unavailable", "The LegalLense PDP class schema is unavailable or invalid."
        expected_names = [class_names[class_id] for class_id in sorted(class_names)]
        if validation.get("class_names") != expected_names:
            return "class_schema_mismatch", "Validation report class names do not match the training dataset schema."
        if not validation.get("dataset_hash") or not validation.get("metrics"):
            return "validation_invalid", "Validation report is missing its evaluated dataset or metrics."
        self._class_names = class_names
        self._validation = validation
        return "ready", None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self.predictor is not None:
            self._model = self.predictor
            return self._model
        from ultralytics import YOLO

        self._model = YOLO(str(self.model_path))
        return self._model

    @staticmethod
    def _unavailable(status: str, reason: str) -> dict[str, Any]:
        return {
            "status": status,
            "available": False,
            "production_authority": False,
            "used_for_compliance": False,
            "detections": [],
            "geometry": {
                "panel_coverage_ratio": None,
                "status": "review_required",
                "reasons": [reason],
                "evidence_regions": [],
            },
            "reasons": [reason],
        }

    def run(self, image: Any, *, source_image_sha256: str) -> dict[str, Any]:
        status, reason = self._availability()
        if status != "ready":
            return self._unavailable(status, reason or "Detector is unavailable")
        if image is None or getattr(image, "size", 0) == 0:
            return self._unavailable("invalid_image", "PDP detector received an empty image")
        if not isinstance(source_image_sha256, str) or len(source_image_sha256) != 64:
            return self._unavailable("source_evidence_missing", "A SHA-256 source-image reference is required")

        try:
            model = self._load_model()
            if callable(model) and self.predictor is model:
                predictions = model(image, confidence=self.confidence, device=self.device)
            else:
                model_names = getattr(model, "names", None)
                if isinstance(model_names, dict):
                    normalized_names = [str(model_names[key]) for key in sorted(model_names)]
                elif isinstance(model_names, (list, tuple)):
                    normalized_names = [str(name) for name in model_names]
                else:
                    normalized_names = None
                expected_names = [name for _, name in sorted((self._class_names or {}).items())]
                if normalized_names is not None and normalized_names != expected_names:
                    return self._unavailable("class_schema_mismatch", "Loaded checkpoint class names do not match the PDP dataset schema.")
                predictions = model.predict(source=image, device=self.device, conf=self.confidence, verbose=False)
            candidates = self._parse_predictions(predictions)
        except Exception as exc:
            logger.warning("Experimental PDP detection unavailable: %s", type(exc).__name__)
            return self._unavailable("inference_unavailable", f"PDP inference failed ({type(exc).__name__})")

        height, width = image.shape[:2]
        detections = []
        for prediction in candidates:
            try:
                class_id = int(prediction["class_id"])
                confidence = float(prediction["confidence"])
                x1, y1, x2, y2 = (float(value) for value in prediction["bbox"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(confidence) or confidence < self.confidence or not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                continue
            if x2 <= x1 or y2 <= y1 or class_id not in (self._class_names or {}):
                continue
            detection_id = f"pdp_{len(detections)}"
            detections.append({
                "region_id": detection_id,
                "class_id": class_id,
                "class_name": (self._class_names or {})[class_id],
                "confidence": round(confidence, 6),
                "bbox": [x1, y1, x2, y2],
                "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "source_image_sha256": source_image_sha256,
                "evidence_region_ids": [detection_id],
                "source": "experimental_legal_lense_pdp_detector",
            })

        geometry_boxes = [
            {"region_id": item["region_id"], "polygon": item["polygon"], "source_evidence": item,
             "evidence_region_ids": item["evidence_region_ids"]}
            for item in detections
        ]
        geometry = evaluate_pdp_geometry(geometry_boxes, image_shape=(height, width))
        return {
            "status": "experimental_candidate",
            "available": True,
            "production_authority": False,
            "used_for_compliance": False,
            "model_path": str(self.model_path),
            "model_hash": (self._validation or {}).get("model_hash") or (self._validation or {}).get("checkpoint_sha256"),
            "validation_report": str(self.validation_path),
            "confidence_threshold": self.confidence,
            "detections": detections,
            "geometry": geometry,
            "reasons": [],
        }

    def _parse_predictions(self, predictions: Any) -> list[dict[str, Any]]:
        if self.predictor is not None:
            return predictions if isinstance(predictions, list) else []
        candidates = []
        for result in predictions or []:
            names = getattr(result, "names", {})
            for box in getattr(result, "boxes", []) or []:
                xyxy = box.xyxy[0].tolist()
                candidates.append({"class_id": int(box.cls[0]), "confidence": float(box.conf[0]), "bbox": xyxy})
                del names
        return candidates