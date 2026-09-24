"""Tests for backend/services/listing_comparison.py."""

from __future__ import annotations

from backend.models.listing import ListingData
from backend.services.listing_comparison import compare_listing_to_physical, price_advisory_note


def _listing(**overrides) -> ListingData:
    base = dict(source_url="https://shop.example/p", retrieval_status="SUCCESS")
    base.update(overrides)
    return ListingData(**base)


def _physical(**fields) -> dict:
    """Mimics ComplianceResult.rule_results[*].required_fields' plain-dict shape."""
    return {name: {"value": value, "status": "PASS"} for name, value in fields.items()}


def test_matching_net_quantity_same_unit():
    listing = _listing(net_quantity="500 g")
    physical = _physical(net_quantity="500 g")
    comparisons = compare_listing_to_physical(listing, physical)
    result = next(c for c in comparisons if c.field == "net_quantity")
    assert result.outcome == "MATCH"


def test_matching_net_quantity_across_equivalent_units():
    listing = _listing(net_quantity="0.5 kg")
    physical = _physical(net_quantity="500 g")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "MATCH"


def test_mismatched_net_quantity_is_flagged():
    listing = _listing(net_quantity="1 kg")
    physical = _physical(net_quantity="500 g")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "MISMATCH"


def test_incompatible_quantity_units_is_review_required_not_mismatch():
    listing = _listing(net_quantity="500 ml")
    physical = _physical(net_quantity="500 g")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "REVIEW_REQUIRED"


def test_unparseable_quantity_is_review_required():
    listing = _listing(net_quantity="a lot")
    physical = _physical(net_quantity="500 g")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "REVIEW_REQUIRED"


def test_country_of_origin_match_with_normalization():
    listing = _listing(country_of_origin="Made in India")
    physical = _physical(country_of_origin="India")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "country_of_origin")
    assert result.outcome == "MATCH"


def test_country_of_origin_mismatch():
    listing = _listing(country_of_origin="China")
    physical = _physical(country_of_origin="India")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "country_of_origin")
    assert result.outcome == "MISMATCH"


def test_mrp_match_against_compliance_engine_pass_dict_shape():
    """On a PASS'd maximum_retail_price_mrp, compliance_engine.py's
    _mrp_result returns value={"detected_value":..., "normalized_value":...,
    "currency": "INR"}, NOT a plain string - this must be unwrapped
    correctly, not str()-ified into a corrupted dict repr."""
    listing = _listing(mrp="99")
    physical = {"maximum_retail_price_mrp": {
        "status": "PASS",
        "value": {"detected_value": "MRP Rs. 99 (Inclusive of all taxes)", "normalized_value": 99.0, "currency": "INR"},
    }}
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "maximum_retail_price_mrp")
    assert result.outcome == "MATCH"
    assert result.physical_value == "99.0"


def test_mrp_match():
    listing = _listing(mrp="100")
    physical = _physical(maximum_retail_price_mrp="Rs. 100.00")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "maximum_retail_price_mrp")
    assert result.outcome == "MATCH"


def test_mrp_mismatch():
    listing = _listing(mrp="120")
    physical = _physical(maximum_retail_price_mrp="Rs. 100.00")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "maximum_retail_price_mrp")
    assert result.outcome == "MISMATCH"


def test_price_difference_from_mrp_is_never_compared_or_flagged():
    """The explicit spec example: physical MRP=100, listing selling
    price=85 must NEVER be treated as a comparison/violation - `price` is
    never used as a comparison input at all, only `mrp` is."""
    listing = _listing(price="85", mrp=None)
    physical = _physical(maximum_retail_price_mrp="100")
    comparisons = compare_listing_to_physical(listing, physical)
    mrp_result = next(c for c in comparisons if c.field == "maximum_retail_price_mrp")
    assert mrp_result.outcome == "LISTING_FIELD_UNAVAILABLE"
    assert mrp_result.listing_value is None
    note = price_advisory_note(listing)
    assert note is not None
    assert "NOT compared against the printed MRP" in note


def test_manufacturer_containment_match():
    listing = _listing(manufacturer="ABC Foods")
    physical = _physical(manufacturer_packer_importer_details="Manufactured by ABC Foods Pvt Ltd, 123 Industrial Area, Mumbai - 400001")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "manufacturer_packer_importer_details")
    assert result.outcome == "MATCH"


def test_manufacturer_wording_difference_is_review_required_not_mismatch():
    """Free-text manufacturer/address blocks vary in formatting - a
    difference must never be asserted as a confident MISMATCH."""
    listing = _listing(manufacturer="XYZ Enterprises")
    physical = _physical(manufacturer_packer_importer_details="Manufactured by ABC Foods Pvt Ltd, Mumbai")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "manufacturer_packer_importer_details")
    assert result.outcome == "REVIEW_REQUIRED"


def test_manufacturer_falls_back_to_packer_importer_field():
    listing = _listing(manufacturer=None, packer_importer="ABC Foods")
    physical = _physical(manufacturer_packer_importer_details="Packed by ABC Foods Pvt Ltd, Mumbai")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "manufacturer_packer_importer_details")
    assert result.outcome == "MATCH"


def test_listing_field_unavailable_when_listing_never_had_it():
    listing = _listing(net_quantity=None)
    physical = _physical(net_quantity="500 g")
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "LISTING_FIELD_UNAVAILABLE"


def test_physical_field_unavailable_when_physical_never_had_it():
    listing = _listing(net_quantity="500 g")
    physical = _physical()  # nothing detected physically
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "PHYSICAL_FIELD_UNAVAILABLE"


def test_not_applicable_when_neither_side_has_the_field():
    listing = _listing()
    physical = _physical()
    result = next(c for c in compare_listing_to_physical(listing, physical) if c.field == "net_quantity")
    assert result.outcome == "NOT_APPLICABLE"


def test_no_comparisons_produced_when_retrieval_failed():
    listing = _listing(retrieval_status="SSRF_BLOCKED", net_quantity=None)
    physical = _physical(net_quantity="500 g")
    comparisons = compare_listing_to_physical(listing, physical)
    assert comparisons == []


def test_price_advisory_note_is_none_when_no_price_present():
    listing = _listing(price=None)
    assert price_advisory_note(listing) is None


def test_price_advisory_note_is_none_when_retrieval_failed():
    listing = _listing(retrieval_status="TIMEOUT", price="85")
    assert price_advisory_note(listing) is None


def test_is_legal_violation_is_always_false_structurally():
    """Structural invariant: no comparison outcome, across every field and
    scenario, may ever set is_legal_violation=True."""
    scenarios = [
        (_listing(net_quantity="1 kg"), _physical(net_quantity="500 g")),          # MISMATCH
        (_listing(net_quantity="500 g"), _physical(net_quantity="500 g")),          # MATCH
        (_listing(net_quantity="a lot"), _physical(net_quantity="500 g")),          # REVIEW_REQUIRED
        (_listing(net_quantity=None), _physical(net_quantity="500 g")),             # LISTING_FIELD_UNAVAILABLE
        (_listing(net_quantity="500 g"), _physical()),                             # PHYSICAL_FIELD_UNAVAILABLE
        (_listing(), _physical()),                                                  # NOT_APPLICABLE
    ]
    for listing, physical in scenarios:
        for comparison in compare_listing_to_physical(listing, physical):
            assert comparison.is_legal_violation is False
