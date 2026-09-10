"""
PaddleOCR text-extraction service for LegalLense.

Scope (deliberately narrow): image in -> recognized text, confidence,
bounding boxes out. This module does NOT interpret, validate, or judge
Legal Metrology compliance in any way - that is the job of a future
deterministic rules engine sitting downstream of this service.

Usage (framework-independent, ready for later FastAPI import):

    from backend.ocr.paddle_ocr_service import PaddleOCRService

    service = PaddleOCRService()
    result = service.run("path/to/label.jpg")
    if result.success:
        print(result.full_text)

The PaddleOCR engine is expensive to construct (it loads several models),
so PaddleOCRService lazily builds one engine per (lang, orientation-flag)
combination and reuses it across calls - important once this is called
repeatedly from a FastAPI process instead of a one-shot CLI script.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from .preprocessing import PreprocessOptions, preprocess_image
from .schemas import Detection, OCRError, OCRResult, RegionOCRResult
from .yolo_service import YOLODetectionError, YOLOInitializationError, YOLOService

logger = logging.getLogger("legallense.ocr.service")

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# PP-OCRv6 (the current default models pulled in by lang="en") crashes on this
# CPU's oneDNN/PIR executor path in paddlepaddle 3.3.1 with:
#   NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
#   not support [pir::ArrayAttribute<pir::DoubleAttribute>]
# Disabling MKL-DNN sidesteps it entirely (confirmed working end-to-end) at a
# small inference-speed cost on CPU. See backend/ocr/README.md for details.
DEFAULT_ENABLE_MKLDNN = False


class OCRInitializationError(RuntimeError):
    """Raised when the underlying PaddleOCR engine fails to construct."""


class PaddleOCRService:
    """Thin, reusable wrapper around paddleocr.PaddleOCR."""

    _engine_cache: dict = {}

    def __init__(
        self,
        lang: str = "en",
        use_doc_orientation_classify: bool = False,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = True,
        enable_mkldnn: bool = DEFAULT_ENABLE_MKLDNN,
        text_rec_score_thresh: Optional[float] = None,
    ) -> None:
        self.lang = lang
        self.use_doc_orientation_classify = use_doc_orientation_classify
        self.use_doc_unwarping = use_doc_unwarping
        self.use_textline_orientation = use_textline_orientation
        self.enable_mkldnn = enable_mkldnn
        self.text_rec_score_thresh = text_rec_score_thresh
        self._engine = None

    # -- engine lifecycle -------------------------------------------------

    def _cache_key(self):
        return (
            self.lang,
            self.use_doc_orientation_classify,
            self.use_doc_unwarping,
            self.use_textline_orientation,
            self.enable_mkldnn,
        )

    def _get_engine(self):
        if self._engine is not None:
            return self._engine

        key = self._cache_key()
        cached = PaddleOCRService._engine_cache.get(key)
        if cached is not None:
            self._engine = cached
            return self._engine

        try:
            from paddleocr import PaddleOCR  # imported lazily: heavy import
        except Exception as exc:  # pragma: no cover - environment issue
            raise OCRInitializationError(f"Failed to import paddleocr: {exc}") from exc

        logger.info(
            "Initializing PaddleOCR engine (lang=%s, mkldnn=%s, textline_orientation=%s)",
            self.lang, self.enable_mkldnn, self.use_textline_orientation,
        )
        try:
            engine = PaddleOCR(
                lang=self.lang,
                use_doc_orientation_classify=self.use_doc_orientation_classify,
                use_doc_unwarping=self.use_doc_unwarping,
                use_textline_orientation=self.use_textline_orientation,
                enable_mkldnn=self.enable_mkldnn,
            )
        except Exception as exc:
            logger.exception("PaddleOCR engine initialization failed")
            raise OCRInitializationError(f"PaddleOCR failed to initialize: {exc}") from exc

        PaddleOCRService._engine_cache[key] = engine
        self._engine = engine
        return engine

    # -- validation ---------------------------------------------------------

    @staticmethod
    def validate_image_path(image_path: str) -> Path:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        if not path.is_file():
            raise FileNotFoundError(f"Path is not a file: {image_path}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{path.suffix}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
            )
        return path

    @staticmethod
    def load_image(path: Path) -> np.ndarray:
        """Decode the image, raising a clear error on corrupt/unreadable files."""
        import cv2

        # cv2.imread never raises on a bad file - it silently returns None -
        # so an explicit check is required to avoid a confusing downstream crash.
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            raise ValueError(f"Image file is empty: {path}")
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Could not decode image (corrupted or unreadable): {path}")
        return image

    # -- inference ------------------------------------------------------

    def _ocr_array(self, engine, image: np.ndarray) -> list:
        """Run PaddleOCR on an already-decoded/cropped image array and parse
        the result into our Detection schema. Shared by the authoritative
        full-image path below and multi_region's auxiliary per-region OCR
        calls, so the actual PaddleOCR invocation only lives in one place."""
        predict_kwargs = {}
        if self.text_rec_score_thresh is not None:
            predict_kwargs["text_rec_score_thresh"] = self.text_rec_score_thresh
        raw_results = engine.predict(image, **predict_kwargs)
        return self._parse_result(raw_results)

    def run(
        self,
        image_path: str,
        preprocess_options: Optional[PreprocessOptions] = None,
        yolo_service: Optional[YOLOService] = None,
    ) -> OCRResult:
        """Run OCR on a single image file and return a stable OCRResult.

        Never raises for expected failure modes (missing file, bad format,
        corrupt image, engine/inference errors) - those come back as
        `OCRResult(success=False, error=...)` so callers (including a future
        FastAPI endpoint) can handle them uniformly instead of catching
        exceptions.

        Full-image OCR is unconditionally authoritative here, regardless of
        `yolo_service`. The pretrained generic YOLO26 checkpoint
        (yolo26n.pt, COCO classes) has no concept of Legal Metrology
        declarations - it must never decide which pixels reach OCR, and a
        generic detection must never suppress or replace OCR evidence. If
        `yolo_service` is given, its detections are recorded in
        `object_detections` as auxiliary/diagnostic data only (for
        visualization, profiling, and future custom-model support) - OCR
        never crops to a YOLO-suggested region. Any YOLO error (model
        missing, inference failure) is logged and has no effect on OCR,
        which always proceeds on the full, untouched image.

        If `yolo_service.detection_mode == "multi_region"` (opt-in via
        YOLO_DETECTION_MODE, see yolo_service.py - never the default), this
        additionally dispatches to `_run_multi_region`, which runs the SAME
        authoritative full-image OCR as this method, plus auxiliary
        per-region OCR calls into `OCRResult.regions` - experimental data
        for a future custom model, never a substitute for the full-image
        result.
        """
        image_name = Path(image_path).name

        try:
            path = self.validate_image_path(image_path)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Input validation failed for %s: %s", image_path, exc)
            code = "file_not_found" if isinstance(exc, FileNotFoundError) else "unsupported_file_type"
            return OCRResult(
                success=False, image=image_name, image_path=str(image_path),
                error=OCRError(code=code, message=str(exc)),
            )

        logger.info("Received image: %s", path)

        try:
            image = self.load_image(path)
        except ValueError as exc:
            logger.warning("Failed to decode image %s: %s", path, exc)
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                error=OCRError(code="unreadable_image", message=str(exc)),
            )

        object_detections: list = []
        detection_mode = "single_region"
        if yolo_service is not None:
            detection_mode = getattr(yolo_service, "detection_mode", "single_region")
            try:
                object_detections = yolo_service.detect(image)
            except (YOLOInitializationError, YOLODetectionError, ValueError) as exc:
                logger.warning("YOLO localization skipped for %s (diagnostic only, no effect on OCR input): %s", path, exc)
            except Exception:
                logger.exception("Unexpected YOLO error for %s (diagnostic only, no effect on OCR input)", path)

        # Multi-region additionally runs auxiliary per-region OCR calls
        # alongside the same authoritative full-image OCR this method runs
        # below - see _run_multi_region's docstring. Only reachable when
        # yolo_service.detection_mode == "multi_region" (opt-in, never the
        # default) AND at least one region was actually detected; zero
        # detections falls through to the plain full-image path below,
        # which is identical either way.
        if detection_mode == "multi_region" and object_detections:
            return self._run_multi_region(
                image, image_name, path, yolo_service, object_detections, preprocess_options,
            )

        # No crop is applied here, regardless of what YOLO detected: the
        # pretrained generic checkpoint has no concept of Legal Metrology
        # declarations, so `object_detections` above is retained purely as
        # auxiliary/diagnostic data (see run()'s docstring). `region_cropped`
        # is therefore always False in this path - full-image OCR is what
        # actually happens, unconditionally.
        region_cropped = False

        preprocessing_applied = False
        if preprocess_options is not None:
            try:
                image = preprocess_image(image, preprocess_options)
                preprocessing_applied = True
            except Exception as exc:
                logger.exception("Preprocessing failed for %s", path)
                return OCRResult(
                    success=False, image=image_name, image_path=str(path),
                    error=OCRError(code="preprocessing_failed", message=str(exc)),
                )

        try:
            engine = self._get_engine()
        except OCRInitializationError as exc:
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                error=OCRError(code="engine_init_failed", message=str(exc)),
            )

        logger.info("Running PaddleOCR inference on %s", path)
        try:
            detections = self._ocr_array(engine, image)
        except Exception as exc:
            logger.exception("PaddleOCR inference failed for %s", path)
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                preprocessing_applied=preprocessing_applied,
                error=OCRError(code="inference_failed", message=str(exc)),
            )

        logger.info("OCR produced %d detection(s) for %s", len(detections), path)

        if not detections:
            return OCRResult(
                success=True, image=image_name, image_path=str(path),
                full_text="", detections=[], detection_count=0,
                preprocessing_applied=preprocessing_applied,
                object_detections=object_detections, region_cropped=region_cropped,
                error=OCRError(code="empty_result", message="No text detected in image"),
            )

        full_text = "\n".join(d.text for d in detections)
        return OCRResult(
            success=True, image=image_name, image_path=str(path),
            full_text=full_text, detections=detections, detection_count=len(detections),
            preprocessing_applied=preprocessing_applied,
            object_detections=object_detections, region_cropped=region_cropped,
        )

    def _run_multi_region(
        self,
        image: np.ndarray,
        image_name: str,
        path: Path,
        yolo_service: YOLOService,
        object_detections: list,
        preprocess_options: Optional[PreprocessOptions],
    ) -> OCRResult:
        """Multi-region path: runs the SAME authoritative full-image OCR as
        the default path (populating full_text/detections/detection_count
        identically to single_region/no-YOLO), and ADDITIONALLY runs one
        auxiliary PaddleOCR call per detected declaration region, tagged by
        class, into OCRResult.regions.

        `regions` is experimental data for a future custom LegalLense model
        - never a substitute for the full-image result. A generic detection
        (COCO classes from yolo26n.pt) must never cause OCR evidence to be
        lost just because it fell outside, or wasn't covered by, a detected
        box: whatever is in `regions` is *in addition to*, never *instead
        of*, the full-image OCR below. A failure OCR-ing any single region
        (preprocessing or inference) is logged and that region is skipped -
        never fatal to the request, and never affects the full-image result.

        Only reached from run() when yolo_service.detection_mode ==
        "multi_region" and at least one region was detected - see run()'s
        docstring and yolo_service.py's YOLO_DETECTION_MODE.
        """
        try:
            engine = self._get_engine()
        except OCRInitializationError as exc:
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                object_detections=object_detections,
                error=OCRError(code="engine_init_failed", message=str(exc)),
            )

        preprocessing_applied = False
        full_image = image
        if preprocess_options is not None:
            try:
                full_image = preprocess_image(image, preprocess_options)
                preprocessing_applied = True
            except Exception as exc:
                logger.exception("Preprocessing failed for %s", path)
                return OCRResult(
                    success=False, image=image_name, image_path=str(path),
                    object_detections=object_detections,
                    error=OCRError(code="preprocessing_failed", message=str(exc)),
                )

        logger.info("Running authoritative full-image PaddleOCR inference on %s (multi_region mode)", path)
        try:
            detections = self._ocr_array(engine, full_image)
        except Exception as exc:
            logger.exception("PaddleOCR inference failed for %s", path)
            return OCRResult(
                success=False, image=image_name, image_path=str(path),
                preprocessing_applied=preprocessing_applied,
                object_detections=object_detections,
                error=OCRError(code="inference_failed", message=str(exc)),
            )

        full_text = "\n".join(d.text for d in detections)
        logger.info("Authoritative full-image OCR produced %d detection(s) for %s", len(detections), path)

        # Auxiliary/experimental: per-declaration-region OCR, tagged by
        # class. Uses the ORIGINAL (not preprocessed) image per-region crop,
        # then applies the same preprocessing option to each crop
        # individually, consistent with how the full image was treated
        # above. Never allowed to affect the authoritative result computed
        # above, in either direction.
        regions: list[RegionOCRResult] = []
        crop_pairs = yolo_service.all_crop_regions(image, object_detections)
        for detection, (x1, y1, x2, y2) in crop_pairs:
            crop = image[y1:y2, x1:x2]

            if preprocess_options is not None:
                try:
                    crop = preprocess_image(crop, preprocess_options)
                except Exception:
                    logger.exception(
                        "Preprocessing failed for auxiliary region %s %s in %s - skipping this region",
                        detection.class_name, (x1, y1, x2, y2), path,
                    )
                    continue

            try:
                region_detections = self._ocr_array(engine, crop)
            except Exception:
                logger.exception(
                    "PaddleOCR inference failed for auxiliary region %s in %s - skipping this region",
                    detection.class_name, path,
                )
                continue

            region_text = "\n".join(d.text for d in region_detections)
            regions.append(RegionOCRResult(
                class_id=detection.class_id,
                class_name=detection.class_name,
                detection_confidence=detection.confidence,
                bbox=detection.bbox,
                full_text=region_text,
                detections=region_detections,
            ))

        logger.info(
            "Multi-region: %d auxiliary region(s) OCR'd (of %d detected) alongside the authoritative "
            "full-image result for %s",
            len(regions), len(crop_pairs), path,
        )

        if not detections:
            return OCRResult(
                success=True, image=image_name, image_path=str(path),
                full_text="", detections=[], detection_count=0,
                preprocessing_applied=preprocessing_applied,
                object_detections=object_detections, region_cropped=False, regions=regions,
                error=OCRError(code="empty_result", message="No text detected in image"),
            )

        return OCRResult(
            success=True, image=image_name, image_path=str(path),
            full_text=full_text, detections=detections, detection_count=len(detections),
            preprocessing_applied=preprocessing_applied,
            object_detections=object_detections, region_cropped=False, regions=regions,
        )

    @staticmethod
    def _parse_result(raw_results) -> list:
        """Convert PaddleOCR's internal OCRResult page objects into our Detection schema."""
        detections: list = []
        if not raw_results:
            return detections

        for page in raw_results:
            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            boxes = page.get("rec_boxes")
            polys = page.get("rec_polys")

            for i, text in enumerate(texts):
                if not text or not text.strip():
                    continue
                confidence = float(scores[i]) if i < len(scores) else 0.0

                if boxes is not None and i < len(boxes):
                    bbox = [int(v) for v in boxes[i]]
                else:
                    bbox = [0, 0, 0, 0]

                if polys is not None and i < len(polys):
                    polygon = [[int(x), int(y)] for x, y in polys[i]]
                else:
                    polygon = [
                        [bbox[0], bbox[1]], [bbox[2], bbox[1]],
                        [bbox[2], bbox[3]], [bbox[0], bbox[3]],
                    ]

                detections.append(Detection(
                    text=text.strip(), confidence=confidence, bbox=bbox, polygon=polygon,
                ))

        return detections
