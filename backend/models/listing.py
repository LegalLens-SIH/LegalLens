"""
Data contracts for Phase 2A e-commerce listing verification.

Scope: an OFFICIAL manually supplies a product listing URL for a scan they
already own. This module describes the retrieved listing's data and its
deterministic comparison against the ALREADY-COMPUTED physical-package
compliance fields for that scan - never a legal verdict of its own.

Architecture boundary (see PHASE 2 IMPLEMENTATION REPORT for the full
rationale): the physical package's deterministic ComplianceEngine result
remains the sole legal authority. Nothing in this module, or in the
services that populate it (listing_retrieval.py, listing_extraction.py,
listing_comparison.py), is ever fed back into ComplianceEngine.evaluate(),
and `FieldComparison.is_legal_violation` is always False - a listing
mismatch is evidence for human review, never an automated compliance
finding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# One unified status covering both HTTP-level retrieval outcomes
# (backend/services/listing_retrieval.py) and content-level extraction
# outcomes (backend/services/listing_extraction.py) - SUCCESS is the only
# value that makes a comparison meaningful; every other value is an
# explicit, honestly-labeled failure/limitation state, never silently
# treated as "no data" or coerced into a compliance failure.
ListingRetrievalStatus = Literal[
    "SUCCESS",
    "INVALID_URL",
    "UNSUPPORTED_SCHEME",
    "SSRF_BLOCKED",
    "DNS_FAILURE",
    "CONNECTION_FAILURE",
    "TIMEOUT",
    "TOO_MANY_REDIRECTS",
    "REDIRECT_BLOCKED",
    "HTTP_ERROR",
    "UNSUPPORTED_CONTENT_TYPE",
    "RESPONSE_TOO_LARGE",
    "EMPTY_PAGE",
    # Retrieval itself succeeded (HTTP 200, allowed content-type) but the
    # page yielded no extractable text or structured data at all - the
    # conservative, honest outcome for a JavaScript-only marketplace page,
    # per this feature's explicit "never fabricate extraction" requirement.
    "LISTING_UNAVAILABLE",
    "UNKNOWN_ERROR",
]

ComparisonOutcome = Literal[
    "MATCH",
    "MISMATCH",
    "LISTING_FIELD_UNAVAILABLE",
    "PHYSICAL_FIELD_UNAVAILABLE",
    "NOT_APPLICABLE",
    "REVIEW_REQUIRED",
]

ExtractionSource = Literal["json-ld", "meta-tag", "text-label", "page-title"]


class ListingFieldEvidence(BaseModel):
    """Provenance for one extracted listing field - identifies exactly
    where the value came from and the source text/context, so a value can
    never be presented without a trace back to what was actually on the
    page."""

    field: str
    extracted_value: str
    extraction_source: ExtractionSource
    source_context: str = Field("", description="Short, truncated snippet of the source text/structured-data node this value was read from")


class ListingData(BaseModel):
    """Structured, conservatively-extracted data for one retrieved
    listing page. Every field is Optional and None means "not found" -
    never guessed, never defaulted to an empty string standing in for a
    real absence."""

    source_url: str
    final_url: Optional[str] = None
    retrieval_status: ListingRetrievalStatus
    retrieved_at: Optional[datetime] = None
    page_title: Optional[str] = None
    product_name: Optional[str] = None
    brand: Optional[str] = None
    # Selling price shown on the listing - evidence only, NEVER compared
    # against the physical package's printed MRP for compliance purposes
    # (see backend/services/listing_comparison.py's price_advisory_note).
    price: Optional[str] = None
    # The listing's OWN declared MRP field (schema.org additionalProperty/
    # PropertyValue or an explicit "MRP:" label) - semantically equivalent
    # to the physical package's printed MRP, so this (never `price`) is
    # what gets compared.
    mrp: Optional[str] = None
    net_quantity: Optional[str] = None
    country_of_origin: Optional[str] = None
    manufacturer: Optional[str] = None
    packer_importer: Optional[str] = None
    consumer_care: Optional[str] = None
    evidence: list[ListingFieldEvidence] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)
    unavailable_fields: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class FieldComparison(BaseModel):
    field: str
    label: str
    physical_value: Optional[str] = None
    listing_value: Optional[str] = None
    outcome: ComparisonOutcome
    explanation: str
    # Structural invariant, always False - see this module's docstring.
    # Kept as an explicit field (rather than omitted) so a consumer can
    # assert on it directly instead of merely assuming absence means false.
    is_legal_violation: bool = False


LISTING_COMPARISON_DISCLAIMER = (
    "This e-commerce listing comparison is supplementary evidence only. It "
    "does NOT determine legal compliance. The physical package's "
    "deterministic Legal Metrology compliance result remains the sole "
    "authority for any legal finding. A listing/physical mismatch, or a "
    "difference between the printed MRP and an online selling price, is "
    "not automatically a statutory violation and requires human review by "
    "an authorized Legal Metrology Officer."
)


class ListingVerificationResult(BaseModel):
    listing: ListingData
    comparisons: list[FieldComparison] = Field(default_factory=list)
    discrepancy_count: int = 0
    review_required_count: int = 0
    price_note: Optional[str] = None
    disclaimer: str = LISTING_COMPARISON_DISCLAIMER
