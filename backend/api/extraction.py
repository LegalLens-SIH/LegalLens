"""
HTTP layer for structured extraction. Mirrors backend/api/compliance.py's
shape exactly: a single POST endpoint that takes an already-computed OCR
result and returns a derived view of it - here, the structured extraction
JSON (backend/models/extraction.py) instead of a compliance verdict. No OCR
inference happens here; see backend/ocr/paddle_ocr_service.py.
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.models.extraction import StructuredExtraction
from backend.services.structured_extraction import build_structured_extraction

router = APIRouter(prefix="/api/extraction", tags=["extraction"])


class ExtractionRequest(BaseModel):
    ocr_result: dict[str, Any] = Field(..., description="Original OCR response, same shape POST /api/compliance/evaluate accepts")


@router.post("/evaluate", response_model=StructuredExtraction)
def evaluate_extraction(request: ExtractionRequest) -> StructuredExtraction:
    try:
        return build_structured_extraction(request.ocr_result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
