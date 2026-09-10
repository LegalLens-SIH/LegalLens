"""
YOLO26 object-localization service for LegalLense.

Scope (deliberately narrow, mirrors paddle_ocr_service.py): image in ->
bounding boxes of detected objects out. This wraps the official Ultralytics
YOLO26 model and is used to locate the product/label region in a photo
before OCR runs, so PaddleOCR can focus on the label instead of a busy
background.

IMPORTANT - what this does NOT do:
The pretrained checkpoint (yolo26n.pt) only knows generic COCO classes
(e.g. "bottle", "book", "box"). It has no concept of Legal Metrology fields
(MRP, Net Quantity, Manufacturer, Country of Origin, ...) - nothing here
claims otherwise. Field-level understanding remains entirely PaddleOCR's
(text) plus backend.services.compliance_engine's (rules) job. This module
only ever returns generic object detections; callers decide what (if
anything) to do with them.

Config (all optional, read once at import time):

    YOLO_ENABLED               "true"/"false" - master on/off switch (default: true)
    YOLO_MODEL_PATH             Path or model name Ultralytics can load
                                 (default: "yolo26n.pt"). Swap in a future
                                 custom-trained checkpoint here, e.g.
                                 "models/legal_lense_yolo26.pt", with no
                                 code changes.
    YOLO_CONFIDENCE_THRESHOLD  Minimum detection confidence (default: 0.25)
    YOLO_DEVICE                 "cpu", "cuda:0", etc. (default: auto-detect)
    YOLO_DETECTION_MODE         "single_region" (default) or "multi_region".
                                 single_region: today's behavior - crop to
                                 ONE best-guess region (largest by area) and
                                 run one OCR call on it. This is the correct,
                                 unchanged default for the pretrained generic
                                 model, and MUST stay the default so nothing
                                 about existing behavior changes.
                                 multi_region: return every detection above
                                 the confidence threshold, tagged by class,
                                 and let PaddleOCRService run one OCR call
                                 PER region (see paddle_ocr_service.py's
                                 `_run_multi_region`). Only switch to this
                                 once a validated custom multi-class
                                 checkpoint is active - see
                                 training/README.md's "known integration
                                 gap" section for why single_region on a
                                 multi-class model would be actively wrong
                                 (it would keep only the single largest of 8
                                 declaration boxes and discard the rest).

Usage:

    from backend.ocr.yolo_service import YOLOService

    service = YOLOService()              # loads the model once, reused after
    detections = service.detect(image)   # image: decoded BGR np.ndarray
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

from .schemas import BoundingBox, ObjectDetection

logger = logging.getLogger("legallense.ocr.yolo_service")

DEFAULT_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolo26n.pt")
DEFAULT_CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.25"))
DEFAULT_DEVICE = os.getenv("YOLO_DEVICE", "").strip() or None
YOLO_ENABLED = os.getenv("YOLO_ENABLED", "true").strip().lower() not in {"false", "0", "no"}

VALID_DETECTION_MODES = {"single_region", "multi_region"}
_RAW_DETECTION_MODE = os.getenv("YOLO_DETECTION_MODE", "single_region").strip().lower()
if _RAW_DETECTION_MODE not in VALID_DETECTION_MODES:
    logger.warning(
        "Invalid YOLO_DETECTION_MODE=%r, falling back to 'single_region'. Valid values: %s",
        _RAW_DETECTION_MODE, sorted(VALID_DETECTION_MODES),
    )
    _RAW_DETECTION_MODE = "single_region"
DEFAULT_DETECTION_MODE = _RAW_DETECTION_MODE


class YOLOInitializationError(RuntimeError):
    """Raised when the underlying Ultralytics YOLO model fails to load."""


class YOLODetectionError(RuntimeError):
    """Raised when inference on an already-loaded model fails."""


class YOLOService:
    """Thin, reusable wrapper around ultralytics.YOLO.

    The model is expensive to construct (it loads network weights), so
    YOLOService lazily builds one model per (path, device) combination and
    caches it at the class level - the (slow) load happens once per process,
    not once per request, matching PaddleOCRService's engine-caching design.
    """

    _model_cache: dict = {}

    def __init__(
        self,
        model_path: Optional[str] = None,
        device: Optional[str] = None,
        confidence: float = DEFAULT_CONFIDENCE,
        detection_mode: Optional[str] = None,
    ) -> None:
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.device = device or DEFAULT_DEVICE or self._detect_device()
        self.confidence = confidence
        mode = (detection_mode or DEFAULT_DETECTION_MODE).strip().lower()
        if mode not in VALID_DETECTION_MODES:
            logger.warning(
                "Invalid detection_mode=%r, falling back to 'single_region'. Valid values: %s",
                mode, sorted(VALID_DETECTION_MODES),
            )
            mode = "single_region"
        self.detection_mode = mode
        self._model = None

    # -- device selection -------------------------------------------------

    @staticmethod
    def _detect_device() -> str:
        """Use a GPU if torch can see one, otherwise fall back to CPU. Never
        raises - an import/detection failure just means "no GPU available"."""
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            logger.debug("Torch CUDA check failed; defaulting to CPU", exc_info=True)
        return "cpu"

    # -- model lifecycle ----------------------------------------------------

    def _get_model(self):
        if self._model is not None:
            return self._model

        key = (self.model_path, self.device)
        cached = YOLOService._model_cache.get(key)
        if cached is not None:
            self._model = cached
            return self._model

        try:
            from ultralytics import YOLO  # imported lazily: heavy import
        except Exception as exc:  # pragma: no cover - environment issue
            raise YOLOInitializationError(f"Failed to import ultralytics: {exc}") from exc

        logger.info("Loading YOLO model '%s' on device '%s'", self.model_path, self.device)
        try:
            model = YOLO(self.model_path)
        except Exception as exc:
            logger.exception("YOLO model failed to load")
            raise YOLOInitializationError(
                f"YOLO model failed to load ('{self.model_path}'): {exc}"
            ) from exc

        YOLOService._model_cache[key] = model
        self._model = model
        return model

    # -- inference ------------------------------------------------------

    def detect(self, image: np.ndarray) -> list[ObjectDetection]:
        """Run object detection on an already-decoded BGR image array.

        Raises YOLOInitializationError if the model cannot be loaded, or
        YOLODetectionError if inference itself fails - callers (e.g.
        PaddleOCRService) are expected to catch both and continue OCR on the
        untouched, un-cropped image rather than fail the whole request.
        """
        if image is None or image.size == 0:
            raise ValueError("Cannot run detection on an empty image")

        model = self._get_model()

        try:
            results = model.predict(
                source=image, device=self.device, conf=self.confidence, verbose=False,
            )
        except Exception as exc:
            logger.exception("YOLO inference failed")
            raise YOLODetectionError(f"YOLO inference failed: {exc}") from exc

        detections: list[ObjectDetection] = []
        try:
            for result in results:
                boxes = result.boxes
                if boxes is None or len(boxes) == 0:
                    continue
                names = result.names
                for box in boxes:
                    xyxy = box.xyxy[0].tolist()
                    class_id = int(box.cls[0])
                    class_name = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
                    detections.append(ObjectDetection(
                        class_id=class_id,
                        class_name=class_name,
                        confidence=float(box.conf[0]),
                        bbox=BoundingBox(
                            x1=int(xyxy[0]), y1=int(xyxy[1]), x2=int(xyxy[2]), y2=int(xyxy[3]),
                        ),
                    ))
        finally:
            # Inference tensors (results/boxes) are not needed past this
            # point - drop the reference promptly instead of letting them
            # ride along in a caller's frame for the rest of the request.
            del results

        logger.info("YOLO detected %d object(s) (device=%s)", len(detections), self.device)
        return detections

    @staticmethod
    def best_crop_region(
        image: np.ndarray,
        detections: list[ObjectDetection],
        padding_ratio: float = 0.05,
        min_area_ratio: float = 0.10,
    ) -> Optional[tuple[int, int, int, int]]:
        """Return (x1, y1, x2, y2) to crop to before OCR, or None to use the
        full image.

        Selects the LARGEST-area detection, not the highest-confidence one.
        This matters in practice: a product label photo is one big object
        (the package) that may also contain small, confidently-misclassified
        sub-regions - e.g. a product photo printed on the packaging (a
        picture of a cookie) getting classified as a real "donut" at higher
        confidence than the packet itself is classified as "book". Cropping
        to the highest-confidence box in that case would crop OUT most of
        the label text. Ranking by area picks the packet (the dominant
        foreground object) instead.

        `min_area_ratio` is a safety floor: if even the largest detection
        covers less than this fraction of the frame, it's more likely a
        small incidental object than the product itself, so no crop is
        applied and OCR runs on the full image instead of gambling on a
        possibly-wrong tight crop.
        """
        if not detections:
            return None

        h, w = image.shape[:2]
        image_area = h * w
        if image_area <= 0:
            return None

        def area(det: ObjectDetection) -> int:
            box = det.bbox
            return max(0, box.x2 - box.x1) * max(0, box.y2 - box.y1)

        best = max(detections, key=area)
        box_area = area(best)
        if box_area / image_area < min_area_ratio:
            return None

        return YOLOService._pad_and_clamp(image.shape, best.bbox, padding_ratio)

    @staticmethod
    def _pad_and_clamp(
        image_shape: tuple, box: BoundingBox, padding_ratio: float,
    ) -> Optional[tuple[int, int, int, int]]:
        """Expand `box` by `padding_ratio` and clamp to the image bounds
        described by `image_shape` (an ndarray.shape tuple). Returns None if
        the result degenerates to zero area. Shared by best_crop_region
        (single_region mode) and all_crop_regions (multi_region mode) so the
        padding math can't drift between the two."""
        h, w = image_shape[:2]
        pad_x = int((box.x2 - box.x1) * padding_ratio)
        pad_y = int((box.y2 - box.y1) * padding_ratio)
        x1 = max(0, box.x1 - pad_x)
        y1 = max(0, box.y1 - pad_y)
        x2 = min(w, box.x2 + pad_x)
        y2 = min(h, box.y2 + pad_y)
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    @staticmethod
    def all_crop_regions(
        image: np.ndarray,
        detections: list[ObjectDetection],
        padding_ratio: float = 0.05,
    ) -> list[tuple[ObjectDetection, tuple[int, int, int, int]]]:
        """Return (detection, padded_crop_box) for EVERY detection above the
        confidence threshold, not just the largest - the multi_region
        counterpart to best_crop_region's single-best-guess selection. Used
        when YOLOService.detection_mode == "multi_region" (see
        paddle_ocr_service.py's `_run_multi_region`): each detected
        declaration class gets its own crop and its own OCR call, instead of
        collapsing everything to one region.

        Unlike best_crop_region, there is no `min_area_ratio` floor here -
        every above-threshold detection is meaningful in multi_region mode
        (each one IS a specific declaration, not a candidate for "the
        product region"), so a small box is not treated as noise. Detections
        whose padded box degenerates to zero area are skipped defensively.
        """
        if not detections or image is None or image.size == 0:
            return []
        pairs: list[tuple[ObjectDetection, tuple[int, int, int, int]]] = []
        for det in detections:
            box = YOLOService._pad_and_clamp(image.shape, det.bbox, padding_ratio)
            if box is not None:
                pairs.append((det, box))
        return pairs
