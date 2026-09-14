from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

# REVIEW_REQUIRED added: the overall/rule-level status for "no confirmed
# violation exists, but one or more applicable checks are low-confidence
# (NEEDS_MANUAL_VERIFICATION) and require manual review" - distinct from
# NON_COMPLIANT (a confirmed failure) and from COMPLIANT (every applicable
# check confirmed). Kept alongside the pre-existing NEEDS_MANUAL_VERIFICATION
# value (still used as-is by the instrument-verification rules, unrelated to
# this change) rather than replacing it, for schema backward compatibility.
ComplianceStatus = Literal["COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT", "NOT_APPLICABLE", "NEEDS_MANUAL_VERIFICATION", "REVIEW_REQUIRED"]
FieldStatus = Literal["PASS", "FAIL", "PARTIAL", "NEEDS_MANUAL_VERIFICATION", "NOT_DETECTED"]


class RegionEvidence(BaseModel):
    """One OCR detection that supports a field's resolved value - the
    authoritative mapping stays "OCR detection -> region_<index>", the SAME
    addressing backend/services/structured_extraction.py's evidence_region_ids
    already uses (see compliance_engine.py's _field_regions). bbox is the
    flat [x1, y1, x2, y2] pixel list, matching backend/ocr/schemas.py's
    Detection.bbox shape exactly - not a re-encoded/duplicate representation."""

    region_id: str
    bbox: list[int] = Field(..., min_length=4, max_length=4)
    text: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class NormalizedField(BaseModel):
    value: Any = None
    detected: bool = False
    confidence: float = Field(0, ge=0, le=1)
    source: str = "ocr"
    # Evidence linking the OCR text/regions that produced `value` - empty
    # when no specific detection line could be matched (see
    # compliance_engine.py's _field_regions docstring for the conservative
    # fallback policy: never invented, only ever a real match or empty).
    region_ids: list[str] = Field(default_factory=list)
    regions: list[RegionEvidence] = Field(default_factory=list)


class NormalizedOCRResult(BaseModel):
    document_id: str
    product_type: str = "unknown"
    package_type: str = "retail"
    raw_text: str = ""
    raw_ocr_result: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, NormalizedField] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FieldResult(BaseModel):
    status: FieldStatus
    value: Any = None
    confidence: float = 0
    missing: list[str] = Field(default_factory=list)
    explanation: str = ""
    # New, additive, backward-compatible (see task: evidence-region
    # propagation) - the same evidence NormalizedField already carries,
    # copied through unchanged so a compliance finding can point back to
    # the exact OCR detection(s)/bounding box(es) behind it. Never
    # independently recomputed here - see compliance_engine.py's _field_result
    # and friends, which just pass `field.region_ids`/`field.regions` through.
    region_ids: list[str] = Field(default_factory=list)
    regions: list[RegionEvidence] = Field(default_factory=list)


class Evidence(BaseModel):
    requirement: str
    ocr_evidence: str = "Not detected by OCR"
    validation: str
    result: str
    region_ids: list[str] = Field(default_factory=list)
    regions: list[RegionEvidence] = Field(default_factory=list)


class RuleResult(BaseModel):
    rule_id: str
    rule_name: str
    status: ComplianceStatus
    # None when every applicable check under this rule is low-confidence
    # (nothing CONFIRMED either way to score) - never fabricated as 0 or 100.
    score: Optional[float] = None
    required_fields: dict[str, FieldResult] = Field(default_factory=dict)
    explanation: str
    evidence: list[str] = Field(default_factory=list)
    evidence_details: list[Evidence] = Field(default_factory=list)
    recommended_action: str = ""


class ComplianceSummary(BaseModel):
    # Rule-level counts (unchanged meaning, preserved for backward
    # compatibility with existing consumers of this schema).
    total_rules: int = 0
    passed: int = 0
    failed: int = 0
    partial: int = 0
    manual_verification: int = 0
    not_applicable: int = 0
    total_checks: int = 0
    # Check-level counts (new): every applicable field/rule-level check,
    # collapsed into exactly the three buckets this scoring model uses -
    # CONFIRMED compliant, CONFIRMED non-compliant (includes PARTIAL - an
    # evidence-backed incomplete declaration, not uncertainty), and
    # low-confidence (excluded from the score, never a hidden pass or fail).
    # This is the authoritative source for compliance_score's calculation;
    # the rule-level counts above are not.
    compliant: int = 0
    non_compliant: int = 0
    low_confidence: int = 0


class ComplianceResult(BaseModel):
    success: bool = True
    document_id: str
    ruleset_id: str
    ruleset_version: str
    validation_profile: str
    overall_status: ComplianceStatus
    # None (never 0, never 100) when there are applicable checks but none of
    # them are confirmed either way - see compliance_engine.py's
    # _aggregate_checks. Represented as null over the API/in the frontend's
    # existing "scan.score == null" convention (already used for a pending
    # scan) - reused here rather than inventing a second null-score meaning.
    compliance_score: Optional[float] = Field(default=None, ge=0, le=100)
    summary: ComplianceSummary
    rule_results: list[RuleResult] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    audit: dict[str, Any]