"""
Data contracts for the Legal RAG legal-basis layer (specs/001-legal-rag).

These models describe verified legal provisions and the read-only result of
looking one up for an existing ComplianceEngine finding. Nothing here makes,
implies, or stores a compliance decision - that remains entirely
backend/services/compliance_engine.py's job, unchanged. See
backend/services/legal_retrieval.py for how these are populated, and
specs/001-legal-rag/data-model.md for the full design rationale.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

EffectiveStatus = Literal["original", "amended", "superseded"]
RetrievalStatus = Literal["found", "not_available"]
RetrievalMethod = Literal["deterministic_link"]


class LegalSourceDocument(BaseModel):
    """A verified, authoritative legal text (or amendment to one)."""

    source_id: str
    title: str
    citation: str
    version: str
    acquired_at: str
    is_amendment: bool = False
    amends_source_id: Optional[str] = None
    verification_note: str


class LegalProvision(BaseModel):
    """One structured unit of legal text extracted from a LegalSourceDocument."""

    provision_id: str
    rule_sub_rule_clause: str
    text: str
    effective_status: EffectiveStatus
    is_currently_effective: bool
    source: LegalSourceDocument


class LegalBasisResult(BaseModel):
    """
    The output of one legal-basis request for one compliance finding.

    The single most important invariant in this model (see data-model.md):
    when `status != "found"`, `provisions` MUST be empty - there is no code
    path in backend/services/legal_retrieval.py that allows a "not
    available" result to simultaneously carry provision text or a citation.
    """

    rule_id: str
    field: Optional[str] = None
    status: RetrievalStatus
    retrieval_method: Optional[RetrievalMethod] = None
    provisions: list[LegalProvision] = Field(default_factory=list)
