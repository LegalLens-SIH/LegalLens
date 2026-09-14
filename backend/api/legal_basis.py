"""
HTTP layer for legal-basis retrieval (specs/001-legal-rag). Read-only.
Mirrors backend/api/compliance.py's shape exactly: request validation and
response shaping only - all actual lookup logic lives in
backend/services/legal_retrieval.py.

This router never touches ComplianceEngine output, `officialDecision`, or
any other field either treats as authoritative (constitution Principle I/II,
spec FR-027) - it only reads an already-computed rule_id/field and returns
verified legal text or an explicit "not available" result.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.models.legal import LegalBasisResult
from backend.services.compliance_engine import load_ruleset
from backend.services.legal_retrieval import get_legal_basis

router = APIRouter(prefix="/api/compliance", tags=["legal-basis"])

# Reuses the exact same ruleset ComplianceEngine itself evaluates against
# (backend/rules/legal_metrology_rules_2011.json) - never a second,
# independently-maintained rule_id vocabulary. Loaded once at import time,
# same pattern as compliance_engine.py's own module-level ruleset loading.
_KNOWN_RULE_IDS = {rule["rule_id"] for framework in load_ruleset()["frameworks"] for rule in framework["rules"]}


@router.get("/legal-basis", response_model=LegalBasisResult)
def read_legal_basis(rule_id: str = Query(...), field: str | None = Query(default=None)) -> LegalBasisResult:
    if rule_id not in _KNOWN_RULE_IDS:
        raise HTTPException(status_code=422, detail=f"Unknown rule_id '{rule_id}'")
    return get_legal_basis(rule_id, field)
