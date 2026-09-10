"""
Data contracts for the OCR layer.

These models describe TEXT EXTRACTION output only. Nothing in this module
makes, implies, or stores a compliance decision (compliant / non-compliant /
violation). That judgment belongs to the future deterministic Legal
Metrology rules engine, not to OCR.

Kept as pydantic models (already a transitive dependency of paddleocr) so
they can be reused as-is for FastAPI request/response models later without
rewriting the schema.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Detection(BaseModel):
    """A single recognized text line."""

    text: str = Field(..., description="Recognized text content")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Recognition confidence score")
    bbox: List[int] = Field(
        ..., min_length=4, max_length=4,
        description="Axis-aligned bounding box [x1, y1, x2, y2] in pixel coordinates",
    )
    polygon: List[List[int]] = Field(
        ..., description="Original detection polygon (typically 4 points: TL, TR, BR, BL), preserved as-is from PaddleOCR",
    )


class OCRError(BaseModel):
    """Structured error payload used when OCR could not be completed."""

    code: str = Field(..., description="Machine-readable error code, e.g. 'file_not_found'")
    message: str = Field(..., description="Human-readable explanation")


class BoundingBox(BaseModel):
    """Axis-aligned pixel bounding box in the (possibly cropped) source image."""

    x1: int
    y1: int
    x2: int
    y2: int


class ObjectDetection(BaseModel):
    """A single object detected by the YOLO26 localization step.

    This describes a generic object (COCO classes, e.g. "bottle", "book",
    "box") found by the *pretrained* model - it is NOT a Legal Metrology
    field (MRP, net quantity, manufacturer, ...). Field-level understanding
    still comes entirely from PaddleOCR + the compliance rules engine.
    """

    class_id: int = Field(..., description="Model class index")
    class_name: str = Field(..., description="Human-readable class label from the model")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score")
    bbox: BoundingBox


class RegionOCRResult(BaseModel):
    """OCR output for a single YOLO-detected declaration region.

    Populated only when a YOLOService in "multi_region" detection mode
    locates one or more declaration regions (see
    backend/ocr/yolo_service.py's YOLO_DETECTION_MODE) - empty/unused in the
    default "single_region" mode. Each region gets its own PaddleOCR call,
    tagged with the class YOLO assigned it (e.g. "mrp", "entity_details").

    Coordinate note: `bbox` is in the ORIGINAL (uncropped) image, since the
    region's location is meaningful regardless of mode. `detections[].bbox`
    is local to THIS region's own crop (same precedent as the existing
    single-region `region_cropped` behavior, which never translated
    coordinates back to the original image either) - not the original
    image's coordinates.
    """

    class_id: int = Field(..., description="YOLO class index for this declaration region")
    class_name: str = Field(..., description="YOLO class name, e.g. 'mrp', 'entity_details'")
    detection_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="YOLO detection confidence for this region (not an OCR confidence)",
    )
    bbox: BoundingBox = Field(..., description="Region bounding box in the ORIGINAL (uncropped) image")
    full_text: str = Field("", description="All OCR text recognized within this region")
    detections: List[Detection] = Field(
        default_factory=list,
        description="Per-line OCR detections within this region (bbox/polygon local to the region's own crop)",
    )


class OCRResult(BaseModel):
    """Stable output contract for one image passed through the OCR layer."""

    success: bool
    image: str = Field(..., description="Filename (basename) of the processed image")
    image_path: Optional[str] = Field(None, description="Full input path as given to the service")
    full_text: str = Field("", description="All detected text lines joined with newlines")
    detections: List[Detection] = Field(default_factory=list)
    detection_count: int = Field(0, description="Number of text detections returned")
    preprocessing_applied: bool = Field(False, description="Whether OpenCV preprocessing ran before OCR")
    object_detections: List[ObjectDetection] = Field(
        default_factory=list,
        description="Objects located by the YOLO26 localization step, if enabled (may be empty even on success)",
    )
    region_cropped: bool = Field(
        False,
        description=(
            "Whether the authoritative full_text/detections above came from a "
            "YOLO-cropped region instead of the full image. Always False today: "
            "full-image OCR is unconditionally authoritative regardless of YOLO "
            "detections or detection_mode, since the current pretrained generic "
            "checkpoint has no concept of Legal Metrology declarations and must "
            "never decide which pixels reach OCR. Reserved for a future, "
            "validated custom LegalLense model."
        ),
    )
    regions: List[RegionOCRResult] = Field(
        default_factory=list,
        description=(
            "Auxiliary/experimental per-declaration-class OCR results when "
            "YOLOService.detection_mode == 'multi_region' - additional data "
            "alongside the authoritative full_text/detections above, never a "
            "replacement for them. Empty in 'single_region' mode (the default, "
            "used by the pretrained generic model)."
        ),
    )
    error: Optional[OCRError] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "image": "product.jpg",
                "image_path": "test_images/product.jpg",
                "full_text": "MRP RS 999\nNET QTY 500G",
                "detections": [
                    {
                        "text": "MRP RS 999",
                        "confidence": 0.97,
                        "bbox": [10, 20, 120, 45],
                        "polygon": [[10, 20], [120, 20], [120, 45], [10, 45]],
                    }
                ],
                "detection_count": 1,
                "preprocessing_applied": False,
                "error": None,
            }
        }
    }
