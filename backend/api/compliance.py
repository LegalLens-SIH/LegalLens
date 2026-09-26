from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.database import get_database
from backend.models.compliance import ComplianceResult
from backend.services.audit import append_audit_event
from backend.services.compliance_engine import ComplianceEngine

router = APIRouter(prefix="/api/compliance", tags=["compliance"])
engine = ComplianceEngine()


class ComplianceRequest(BaseModel):
    ocr_result: dict[str, Any] = Field(..., description="Original OCR response; it is preserved in the derived normalized record")
    validation_profile: str = "e_commerce_product_listing"


@router.post("/evaluate", response_model=ComplianceResult)
def evaluate_compliance(request: ComplianceRequest) -> ComplianceResult:
    db = get_database()
    try:
        result = engine.evaluate(request.ocr_result, request.validation_profile)
        entity_id = str(request.ocr_result.get("document_id") or request.ocr_result.get("id") or "manual_evaluation")
        append_audit_event(
            db,
            event_type="compliance_evaluation",
            entity_type="compliance",
            entity_id=entity_id,
            user_id=request.ocr_result.get("userId"),
            actor_role="system",
            action="compliance_evaluated",
            details={
                "validation_profile": request.validation_profile,
                "overall_status": result.overall_status,
                "compliance_score": result.compliance_score,
                "summary": result.summary,
            },
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc