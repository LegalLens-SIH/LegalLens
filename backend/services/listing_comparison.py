"""
Deterministic physical-package <-> e-commerce-listing comparison for
Phase 2A.

Scope: compares ALREADY-COMPUTED physical compliance field values (from
ComplianceEngine's rule_results, passed in as plain dicts - this module
never imports compliance_engine.py, keeping listing verification
structurally isolated from the compliance engine) against a ListingData's
extracted values.

Rules this module follows strictly, per the Phase 2A specification:
  - No fuzzy/edit-distance matching anywhere. Only exact-after-
    conservative-normalization comparisons, or an explicit substring
    containment check for free-text manufacturer/packer blocks (never a
    similarity score).
  - Comparing fields whose semantics differ is never done: the listing's
    selling `price` is NEVER compared against the physical MRP (see
    price_advisory_note) - only the listing's own declared `mrp` field is.
  - Every comparison's `is_legal_violation` is always False - a
    discrepancy here is evidence for human review, never an automated
    compliance verdict. This is a structural invariant asserted by
    backend/services/test_listing_comparison.py.
  - Ambiguity (unparseable values, incompatible units, free-text
    formatting variance) resolves to REVIEW_REQUIRED, never to a
    confident MISMATCH claim.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.models.listing import ComparisonOutcome, FieldComparison, ListingData

# (physical compliance field name, ListingData attribute, human label)
_COMPARISON_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("net_quantity", "net_quantity", "Net Quantity"),
    ("country_of_origin", "country_of_origin", "Country of Origin"),
    ("manufacturer_packer_importer_details", "manufacturer", "Manufacturer / Packer / Importer"),
    ("maximum_retail_price_mrp", "mrp", "Maximum Retail Price (MRP)"),
)

_QUANTITY_PATTERN = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b",
    re.I,
)
_WEIGHT_UNITS_TO_GRAMS = {"kg": 1000.0, "kgs": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0, "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0}
_VOLUME_UNITS_TO_ML = {"l": 1000.0, "litre": 1000.0, "litres": 1000.0, "liter": 1000.0, "liters": 1000.0, "ml": 1.0, "mls": 1.0, "millilitre": 1.0, "millilitres": 1.0, "milliliter": 1.0, "milliliters": 1.0}

_AMOUNT_PATTERN = re.compile(r"[0-9][0-9,]*(?:\.[0-9]{1,2})?")
_ORIGIN_PREFIX_PATTERN = re.compile(r"^(?:made\s+in|product\s+of|country\s+of\s+origin\s*:?)\s*", re.I)

QUANTITY_MATCH_TOLERANCE = 0.005  # 0.5% relative tolerance for rounding/display differences


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _physical_value(field_key: str, raw_value: Any) -> Optional[str]:
    """ComplianceEngine's FieldResult.value is usually a plain string, but
    on a PASS'd maximum_retail_price_mrp specifically, compliance_engine.py's
    _mrp_result returns a structured {"detected_value", "normalized_value",
    "currency"} dict instead (see its PASS branch) - this must be unwrapped
    to its numeric amount rather than naively str()-ified, which would
    otherwise embed a Python dict repr into the comparison and corrupt
    _compare_mrp's numeric parsing."""
    if field_key == "maximum_retail_price_mrp" and isinstance(raw_value, dict):
        normalized = raw_value.get("normalized_value")
        if normalized is not None:
            return _clean(normalized)
        return _clean(raw_value.get("detected_value"))
    return _clean(raw_value)


def _parse_quantity(value: str) -> Optional[tuple[float, str]]:
    match = _QUANTITY_PATTERN.search(value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit in _WEIGHT_UNITS_TO_GRAMS:
        return amount * _WEIGHT_UNITS_TO_GRAMS[unit], "weight"
    if unit in _VOLUME_UNITS_TO_ML:
        return amount * _VOLUME_UNITS_TO_ML[unit], "volume"
    return None


def _compare_quantity(physical_value: str, listing_value: str) -> tuple[ComparisonOutcome, str]:
    physical_parsed = _parse_quantity(physical_value)
    listing_parsed = _parse_quantity(listing_value)
    if physical_parsed is None or listing_parsed is None:
        return "REVIEW_REQUIRED", (
            f"Could not parse a recognizable quantity and unit from '{physical_value}' and/or "
            f"'{listing_value}'; manual comparison required."
        )
    physical_amount, physical_basis = physical_parsed
    listing_amount, listing_basis = listing_parsed
    if physical_basis != listing_basis:
        return "REVIEW_REQUIRED", (
            f"Physical package quantity ('{physical_value}', {physical_basis}) and listing quantity "
            f"('{listing_value}', {listing_basis}) use incompatible units and cannot be safely compared."
        )
    if physical_amount == 0:
        return "REVIEW_REQUIRED", "Physical package quantity resolved to zero; cannot compute a reliable comparison."
    relative_diff = abs(physical_amount - listing_amount) / physical_amount
    if relative_diff <= QUANTITY_MATCH_TOLERANCE:
        return "MATCH", f"Physical ('{physical_value}') and listing ('{listing_value}') quantities match."
    return "MISMATCH", (
        f"Physical package declares '{physical_value}' but the listing declares '{listing_value}' - "
        "these quantities do not match."
    )


def _normalize_country(value: str) -> str:
    value = _ORIGIN_PREFIX_PATTERN.sub("", value.strip())
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value.casefold()


def _compare_country(physical_value: str, listing_value: str) -> tuple[ComparisonOutcome, str]:
    normalized_physical = _normalize_country(physical_value)
    normalized_listing = _normalize_country(listing_value)
    if not normalized_physical or not normalized_listing:
        return "REVIEW_REQUIRED", "Country of origin could not be normalized for comparison; manual review required."
    if normalized_physical == normalized_listing:
        return "MATCH", f"Physical ('{physical_value}') and listing ('{listing_value}') country of origin match."
    return "MISMATCH", (
        f"Physical package declares country of origin '{physical_value}' but the listing declares "
        f"'{listing_value}' - these do not match."
    )


def _parse_amount(value: str) -> Optional[float]:
    match = _AMOUNT_PATTERN.search(value.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _compare_mrp(physical_value: str, listing_value: str) -> tuple[ComparisonOutcome, str]:
    physical_amount = _parse_amount(physical_value)
    listing_amount = _parse_amount(listing_value)
    if physical_amount is None or listing_amount is None:
        return "REVIEW_REQUIRED", (
            f"Could not parse a numeric MRP amount from '{physical_value}' and/or '{listing_value}'; "
            "manual comparison required."
        )
    if abs(physical_amount - listing_amount) <= 0.01:
        return "MATCH", f"Physical MRP ('{physical_value}') and listing-declared MRP ('{listing_value}') match."
    return "MISMATCH", (
        f"Physical package MRP is '{physical_value}' but the listing declares MRP '{listing_value}' - "
        "these do not match. Note: a listing's SELLING PRICE differing from MRP is normal and separate "
        "from this comparison (see price_note)."
    )


def _normalize_free_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _compare_manufacturer(physical_value: str, listing_value: str) -> tuple[ComparisonOutcome, str]:
    normalized_physical = _normalize_free_text(physical_value)
    normalized_listing = _normalize_free_text(listing_value)
    if not normalized_physical or not normalized_listing:
        return "REVIEW_REQUIRED", "Manufacturer/packer details could not be normalized for comparison."
    if normalized_listing in normalized_physical or normalized_physical in normalized_listing:
        return "MATCH", (
            f"Listing manufacturer/packer text ('{listing_value}') appears within the physical package's "
            f"declaration ('{physical_value}')."
        )
    # Deliberately REVIEW_REQUIRED, never MISMATCH: manufacturer/packer text
    # is free-form (full postal address vs. a bare company name), and a
    # confident MISMATCH claim here would be an unsafe, non-fuzzy-but-still-
    # unreliable inference given normal formatting variance.
    return "REVIEW_REQUIRED", (
        f"Physical package declares '{physical_value}' and the listing declares '{listing_value}' - "
        "wording differs and this cannot be confidently classified as matching or mismatched from text "
        "alone; manual comparison required."
    )


def _compare_field(field: str, physical_value: str, listing_value: str) -> tuple[ComparisonOutcome, str]:
    if field == "net_quantity":
        return _compare_quantity(physical_value, listing_value)
    if field == "country_of_origin":
        return _compare_country(physical_value, listing_value)
    if field == "maximum_retail_price_mrp":
        return _compare_mrp(physical_value, listing_value)
    if field == "manufacturer_packer_importer_details":
        return _compare_manufacturer(physical_value, listing_value)
    return "REVIEW_REQUIRED", "No deterministic comparison rule is defined for this field; manual review required."  # pragma: no cover - unreachable given _COMPARISON_FIELDS


def compare_listing_to_physical(listing: ListingData, physical_fields: dict[str, Any]) -> list[FieldComparison]:
    """physical_fields: {field_name: {"value": ..., "status": ..., ...}}
    - the plain dict shape of ComplianceResult.rule_results[*].required_fields,
    passed in by the API layer (backend/api/listing.py) so this module never
    imports backend.models.compliance or backend.services.compliance_engine.

    Returns [] when retrieval did not succeed - there is nothing safe to
    compare, and an empty comparison list is never mistaken for "all
    fields matched" by any consumer (see ListingVerificationResult.listing.
    retrieval_status, which is always surfaced alongside `comparisons`).
    """
    if listing.retrieval_status != "SUCCESS":
        return []

    comparisons: list[FieldComparison] = []
    for physical_key, listing_attr, label in _COMPARISON_FIELDS:
        physical_raw = physical_fields.get(physical_key) or {}
        physical_value = _physical_value(physical_key, physical_raw.get("value") if isinstance(physical_raw, dict) else None)

        if physical_key == "manufacturer_packer_importer_details":
            listing_value = _clean(listing.manufacturer) or _clean(listing.packer_importer)
        else:
            listing_value = _clean(getattr(listing, listing_attr, None))

        if not physical_value and not listing_value:
            comparisons.append(FieldComparison(
                field=physical_key, label=label, outcome="NOT_APPLICABLE",
                explanation="Neither the physical package scan nor the listing has this field available for comparison.",
            ))
            continue
        if not physical_value:
            comparisons.append(FieldComparison(
                field=physical_key, label=label, listing_value=listing_value, outcome="PHYSICAL_FIELD_UNAVAILABLE",
                explanation="This field was not confirmed on the physical package scan, so it cannot be compared.",
            ))
            continue
        if not listing_value:
            comparisons.append(FieldComparison(
                field=physical_key, label=label, physical_value=physical_value, outcome="LISTING_FIELD_UNAVAILABLE",
                explanation="This field was not found on the retrieved listing page, so it cannot be compared.",
            ))
            continue

        outcome, explanation = _compare_field(physical_key, physical_value, listing_value)
        comparisons.append(FieldComparison(
            field=physical_key, label=label, physical_value=physical_value, listing_value=listing_value,
            outcome=outcome, explanation=explanation,
        ))

    return comparisons


def price_advisory_note(listing: ListingData) -> Optional[str]:
    """A non-comparison, informational note only - the listing's selling
    price is NEVER turned into a FieldComparison against the physical MRP
    (see this module's docstring); this is the one place that relationship
    is surfaced, explicitly labeled as non-legal."""
    if listing.retrieval_status != "SUCCESS" or not listing.price:
        return None
    return (
        f"Listing selling price ('{listing.price}') is commercial information only and is NOT "
        "compared against the printed MRP for legal compliance purposes; a lower online selling "
        "price than MRP is normal and is not a statutory violation."
    )
