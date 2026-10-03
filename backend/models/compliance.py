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
ComplianceStatus = Literal["PASS", "COMPLIANT", "PARTIALLY_COMPLIANT", "NON_COMPLIANT", "NOT_APPLICABLE", "NEEDS_MANUAL_VERIFICATION", "REVIEW_REQUIRED"]
FieldStatus = Literal["PASS", "FAIL", "PARTIAL", "NEEDS_MANUAL_VERIFICATION", "NOT_DETECTED"]

DataDrivenRuleStatus = Literal["PASS", "FAIL", "REVIEW_REQUIRED", "NOT_APPLICABLE", "NOT_ASSESSED"]

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
    # Phase 1: distinguishes a finding that is fully verifiable from the
    # uploaded image/OCR alone (escalates=False, the default and the vast
    # majority of rules - e.g. LMPC-R6's declaration checks) from one that
    # can NEVER be confirmed from a photo regardless of OCR quality or
    # confidence, and therefore always requires physical/offline
    # verification by a human (escalates=True) - see compliance_engine.py's
    # _rule_result for exactly which two rules set this and why (LMPC-R11:
    # requires weighing the physical package; LMGEN-R14: requires
    # inspecting a physical seal/certificate). This is a STRUCTURAL
    # property of what the rule can establish, never a confidence score -
    # it does not vary scan-to-scan for the same rule, and is independent
    # of `status` (a rule can be escalates=True and still COMPLIANT, e.g.
    # when physical-verification evidence was separately supplied).
    escalates: bool = False


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
    # These check-level buckets remain the basis for the legacy profile score
    # exposed under legacy_comparison; they are not a JSON legal-coverage score.
    compliant: int = 0
    non_compliant: int = 0
    low_confidence: int = 0


class DataDrivenEvidence(BaseModel):
    evidence_id: str
    source_text: Optional[str] = None
    ocr_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    extraction_confidence: Optional[float] = Field(default=None, ge=0, le=1)


class DataDrivenRuleTrace(BaseModel):
    path: str
    field: Optional[str] = None
    operator: Optional[str] = None
    combinator: Optional[str] = None
    actual: Any = None
    expected: Any = None
    result: Optional[Literal["TRUE", "FALSE", "UNKNOWN"]] = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[DataDrivenEvidence] = Field(default_factory=list)
    detail: str = ""


class DataDrivenRuleResult(BaseModel):
    rule_id: str
    rule_ref: str
    rule_name: str
    title: str = ""
    category: str = ""
    severity: str
    chapter: str
    status: DataDrivenRuleStatus
    applicability: Literal["TRUE", "FALSE", "UNKNOWN"]
    has_executable_check: bool
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    trace: list[DataDrivenRuleTrace] = Field(default_factory=list)


class DataDrivenEvaluation(BaseModel):
    overall_status: Literal["PASS", "COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED", "NOT_APPLICABLE"]
    rules: list[DataDrivenRuleResult] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    applicability_gates: dict[str, Any] = Field(default_factory=dict)
    calculations: dict[str, Any] = Field(default_factory=dict)


class LegacyComplianceComparison(BaseModel):
    overall_status: ComplianceStatus
    compliance_score: Optional[float] = Field(default=None, ge=0, le=100)
    summary: Optional[ComplianceSummary] = None
    rule_results: list[RuleResult] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class ComplianceResult(BaseModel):
    success: bool = True
    document_id: str
    ruleset_id: str
    ruleset_version: str
    validation_profile: str
    overall_status: ComplianceStatus
    # The source JSON ruleset has no percentage-scoring formula. This stays
    # null rather than presenting the legacy profile score as legal coverage.
    compliance_score: Optional[float] = Field(default=None, ge=0, le=100)
    legacy_comparison: Optional[LegacyComplianceComparison] = None
    summary: ComplianceSummary = Field(
        ..., description="Legacy profile summary retained for compatibility; not the canonical JSON ruleset result."
    )
    rule_results: list[RuleResult] = Field(
        default_factory=list,
        description="Legacy profile rule results retained for compatibility; see data_driven_evaluation for canonical rules.",
    )
    data_driven_evaluation: Optional[DataDrivenEvaluation] = None
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Legacy profile findings retained for compatibility; JSON uncertainty is represented in data_driven_evaluation.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Compatibility warnings retained from the legacy profile evaluation; see legacy_comparison.",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Compatibility recommendations retained from the legacy profile evaluation; see legacy_comparison.",
    )
    audit: dict[str, Any]
    # Phase 1: unconditional, deterministic disclaimer - populated by
    # ComplianceEngine.evaluate() on every result, regardless of outcome
    # (see DISCLAIMER_TEXT there). A dedicated field (not folded into
    # `warnings`, which is variable-length/conditional) so a consumer can
    # rely on its presence and exact meaning without parsing free-form
    # warning text.
    disclaimer: str = ""