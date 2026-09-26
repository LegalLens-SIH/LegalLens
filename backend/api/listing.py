"""
Phase 2A: e-commerce listing verification API.

Official-only, supplementary evidence layer for a scan the official
already owns. Fetches a manually-supplied product listing URL, extracts
only explicitly-available fields, and deterministically compares them
against the ALREADY-COMPUTED physical-package compliance fields for that
scan. Never re-runs OCR, never calls ComplianceEngine, and never changes a
scan's compliance/status/score - the result is stored under
scan["listingVerification"] as an additive sibling field, the same pattern
backend/api/ocr.py's create_scan already uses for authenticity/
structuredExtraction/sourceImageSha256.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.auth import official_user
from backend.database import get_database
from backend.models.listing import LISTING_COMPARISON_DISCLAIMER
from backend.services.audit import append_audit_event
from backend.services.listing_comparison import compare_listing_to_physical, price_advisory_note
from backend.services.listing_extraction import extract_listing_data
from backend.services.listing_retrieval import ListingFetcher
from backend.services.evidence_package import build_evidence_package, record_evidence_package

logger = logging.getLogger("legallense.api.listing")

router = APIRouter(prefix="/api/scans", tags=["listing-verification"])

# One shared instance (no per-request state, cheap to construct) - same
# process-lifetime pattern as backend/api/ocr.py's _service/_gemini_service.
_fetcher = ListingFetcher()

MAX_URL_LENGTH = 2048


class ListingVerificationRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=MAX_URL_LENGTH)


def _physical_fields_from_scan(scan: dict[str, Any]) -> dict[str, Any]:
    """First-seen-wins flattening of every rule's required_fields into one
    lookup by field name - the physical compliance fields Phase 2A compares
    against (net_quantity, country_of_origin, manufacturer_packer_importer_
    details, maximum_retail_price_mrp) each appear in exactly one rule
    today, so this never needs to resolve a genuine cross-rule conflict."""
    compliance = scan.get("compliance") or {}
    fields: dict[str, Any] = {}
    for rule in compliance.get("rule_results") or []:
        for name, result in (rule.get("required_fields") or {}).items():
            fields.setdefault(name, result)
    return fields


def _get_owned_scan(scan_id: str, user: dict[str, Any]) -> dict[str, Any]:
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan


@router.post("/{scan_id}/listing-verification")
def verify_listing(scan_id: str, payload: ListingVerificationRequest, user: dict[str, Any] = Depends(official_user)) -> dict[str, Any]:
    scan = _get_owned_scan(scan_id, user)

    outcome = _fetcher.fetch(payload.url)
    listing = extract_listing_data(payload.url, outcome)
    listing.retrieved_at = datetime.now(timezone.utc)

    physical_fields = _physical_fields_from_scan(scan)
    comparisons = compare_listing_to_physical(listing, physical_fields)
    result_dict: dict[str, Any] = {
        "listing": listing.model_dump(mode="json"),
        "comparisons": [c.model_dump(mode="json") for c in comparisons],
        "discrepancy_count": sum(1 for c in comparisons if c.outcome == "MISMATCH"),
        "review_required_count": sum(1 for c in comparisons if c.outcome == "REVIEW_REQUIRED"),
        "price_note": price_advisory_note(listing),
        "disclaimer": LISTING_COMPARISON_DISCLAIMER,
        "source_page_sha256": outcome.body_sha256,
    }

    database = get_database()
    retrieval_audit = append_audit_event(
        database,
        event_type="listing_verification",
        entity_type="scan",
        entity_id=scan_id,
        user_id=user["_id"],
        actor_role=user.get("role"),
        action="listing_data_compared" if listing.retrieval_status == "SUCCESS" else "listing_retrieval_unavailable",
        details={
            "source_url": payload.url,
            "final_url": listing.final_url,
            "retrieval_status": listing.retrieval_status,
            "source_page_sha256": outcome.body_sha256,
            "discrepancy_count": result_dict["discrepancy_count"],
            "review_required_count": result_dict["review_required_count"],
            "is_compliance_verdict": False,
        },
    )
    parent_package = database.evidence_packages.find_one({"package_id": scan.get("evidencePackageId")}) if scan.get("evidencePackageId") else None
    raw_artifacts = {
        "listing_verification": {
            "artifact_type": "listing_verification",
            "value": result_dict,
            "source": "authenticated_listing_review",
            "derived_from": [scan.get("evidencePackageId")] if scan.get("evidencePackageId") else [],
            "metadata": {"source_url": payload.url, "final_url": listing.final_url, "retrieval_status": listing.retrieval_status},
        },
        "parent_scan_evidence": {
            "artifact_type": "parent_evidence_package_reference",
            "value": {
                "package_id": (parent_package or {}).get("package_id"),
                "merkle_root": (parent_package or {}).get("merkle_root"),
            },
            "source": "existing_scan_evidence",
        },
    }
    if outcome.body_sha256:
        raw_artifacts["source_page_digest"] = {
            "artifact_type": "external_page_sha256",
            "value": outcome.body_sha256,
            "source": "safe_http_fetch",
            "metadata": {"source_url": payload.url, "final_url": listing.final_url, "content_type": outcome.content_type},
        }
    package = build_evidence_package(
        evidence_id=f"{scan_id}:listing-verification",
        raw_artifacts=raw_artifacts,
        audit_refs=[retrieval_audit["eventHash"]],
        ruleset_digest=(scan.get("compliance") or {}).get("audit", {}).get("ruleset_digest"),
    )
    package_audit = record_evidence_package(database, package, entity_id=scan_id, entity_type="scan")
    result_dict["evidence_package_id"] = package.package_id
    result_dict["evidence_merkle_root"] = package.merkle_root
    result_dict["evidence_package_audit_hash"] = package_audit["eventHash"]
    database.scans.update_one(
        {"id": scan_id, "userId": user["_id"]},
        {"$set": {"listingVerification": result_dict}},
    )
    logger.info(
        "Listing verification for scan %s: status=%s discrepancies=%d review_required=%d",
        scan_id, listing.retrieval_status, result_dict["discrepancy_count"], result_dict["review_required_count"],
    )
    return result_dict


@router.get("/{scan_id}/listing-verification")
def get_listing_verification(scan_id: str, user: dict[str, Any] = Depends(official_user)) -> dict[str, Any] | None:
    scan = get_database().scans.find_one({"id": scan_id, "userId": user["_id"]}, {"listingVerification": 1})
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan.get("listingVerification")
