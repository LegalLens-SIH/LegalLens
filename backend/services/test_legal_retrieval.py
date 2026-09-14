"""Tests for the legal-basis retrieval service (specs/001-legal-rag).

Covers: corpus load-time validation (a malformed corpus fails loudly, never
silently), deterministic rule_id/field lookup, coverage completeness for the
verified fields, "not available" behavior for missing coverage, and the
central safety invariant - a "not available" result never carries
provisions.

unit_sale_price (LMPC-R6-MANDATORY-DECLARATIONS) was resolved in a targeted
follow-up verification pass: Rule 6(11), as substituted by the Legal
Metrology (Packaged Commodities) Amendment Rules, 2022 (GSR 226(E), dated
28th March 2022) - see legal_corpus/legal_metrology_packaged_commodities_2011.json's
IN-LM-PCR-2011-GSR226E-2022 source entry for full provenance detail. It is
no longer in VERIFIED_LMPC_R6_FIELDS's "deliberately absent" category.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.legal_retrieval import (
    LegalCorpusError,
    get_legal_basis,
    load_corpus,
    lookup_by_rule_id,
)

REAL_CORPUS_PATH = Path(__file__).resolve().parents[1] / "legal_corpus" / "legal_metrology_packaged_commodities_2011.json"

# The 8 LMPC-R6 fields this corpus verifiedly covers.
VERIFIED_LMPC_R6_FIELDS = [
    "manufacturer_packer_importer_details",
    "country_of_origin",
    "common_generic_name_of_commodity",
    "net_quantity",
    "month_and_year_of_manufacture_or_packing",
    "maximum_retail_price_mrp",
    "unit_sale_price",
    "consumer_care_details",
]
VERIFIED_LMPC_R24_FIELDS = [
    "name_and_address_of_manufacturer_or_packer",
    "identity_of_commodity",
    "total_number_of_retail_packages_or_net_quantity",
]


def test_real_corpus_loads_without_error():
    sources, provisions = load_corpus(REAL_CORPUS_PATH)
    assert len(sources) >= 5
    assert len(provisions) == 11


# --- Coverage completeness (T016, T017) ---

@pytest.mark.parametrize("field", VERIFIED_LMPC_R6_FIELDS)
def test_every_verified_lmpc_r6_field_has_a_linked_provision(field):
    matches = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", field)
    assert len(matches) >= 1, f"expected at least one provision linked to LMPC-R6/{field}"


@pytest.mark.parametrize("field", VERIFIED_LMPC_R24_FIELDS)
def test_every_verified_lmpc_r24_field_has_a_linked_provision(field):
    matches = lookup_by_rule_id("LMPC-R24-WHOLESALE-DECLARATIONS", field)
    assert len(matches) >= 1, f"expected at least one provision linked to LMPC-R24/{field}"


def test_unit_sale_price_now_has_a_verified_rule_6_11_provision():
    """unit_sale_price was resolved in a targeted follow-up verification
    pass (GSR 226(E), dated 28th March 2022, substituting Rule 6(11)) - it
    must no longer return empty (a regression of that fix would silently
    resurrect fabrication risk in the frontend Legal Basis panel)."""
    matches = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", "unit_sale_price")
    assert len(matches) == 1
    provision = matches[0]
    assert provision.rule_sub_rule_clause == "Rule 6(11)"
    assert provision.is_currently_effective is True
    assert provision.effective_status == "amended"


def test_unit_sale_price_provision_text_and_source_citation_are_correct():
    provision = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", "unit_sale_price")[0]
    # The substantive obligation and its two provisos, verbatim from the
    # verified notification text (see the corpus source's verification_note
    # for how this text was cross-checked).
    assert "unit sale price in rupees" in provision.text
    assert "per gram" in provision.text and "per kilogram" in provision.text
    assert "alcoholic beverages or spirituous liquor" in provision.text
    assert "retail sale price is equal to the unit sale price" in provision.text
    # G.S.R. 226(E) source provenance is preserved and traceable.
    assert provision.source.source_id == "IN-LM-PCR-2011-GSR226E-2022"
    assert "GSR 226(E)" in provision.source.citation
    assert "28th March 2022" in provision.source.citation
    assert provision.source.is_amendment is True
    assert provision.source.amends_source_id == "IN-LM-PCR-2011-GSR202E"


def test_unit_sale_price_effective_date_is_represented():
    """The notification's originally-stated commencement (01.10.2022) was
    deferred by later notifications to a final effective date of
    01.04.2023 - the corpus must represent the CURRENT correct date, not
    the superseded one, and must mark the provision as in force today."""
    provision = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", "unit_sale_price")[0]
    assert "01.04.2023" in provision.source.citation
    assert provision.is_currently_effective is True


# --- Source correctness / provenance (T026) ---

def test_every_provision_traces_to_a_real_source_with_full_provenance():
    """Corpus-wide provenance audit - spec SC-001."""
    for field in VERIFIED_LMPC_R6_FIELDS:
        for provision in lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", field):
            assert provision.source.citation
            assert provision.source.version
            assert provision.source.verification_note
    for field in VERIFIED_LMPC_R24_FIELDS:
        for provision in lookup_by_rule_id("LMPC-R24-WHOLESALE-DECLARATIONS", field):
            assert provision.source.citation
            assert provision.source.version
            assert provision.source.verification_note


# --- Wrong-rule retrieval (T028) ---

def test_lookup_never_returns_a_provision_from_a_different_rule_id():
    r6_matches = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS")
    r24_matches = lookup_by_rule_id("LMPC-R24-WHOLESALE-DECLARATIONS")
    r6_ids = {p.provision_id for p in r6_matches}
    r24_ids = {p.provision_id for p in r24_matches}
    assert r6_ids.isdisjoint(r24_ids)
    # Every R6 provision's own rule_sub_rule_clause starts with "Rule 6",
    # every R24 provision's with "Rule 24" - a second, independent check
    # that no cross-contamination occurred.
    assert all(p.rule_sub_rule_clause.startswith("Rule 6") for p in r6_matches)
    assert all(p.rule_sub_rule_clause.startswith("Rule 24") for p in r24_matches)


def test_lookup_scoped_to_field_never_returns_a_different_fields_provision():
    matches = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", "maximum_retail_price_mrp")
    assert len(matches) == 1
    assert matches[0].rule_sub_rule_clause == "Rule 6(1)(e)"


def test_adding_unit_sale_price_did_not_change_any_other_rule_or_field_mapping():
    """Regression guard for this targeted corpus update: every OTHER
    already-verified field's clause/source must be byte-for-byte the same
    as before GSR 226(E) was added, and the new Rule 6(11) entry must not
    leak into any neighboring field's lookup."""
    expected_clauses = {
        "manufacturer_packer_importer_details": "Rule 6(1)(a)",
        "country_of_origin": "Rule 6(1)(aa)",
        "common_generic_name_of_commodity": "Rule 6(1)(b)",
        "net_quantity": "Rule 6(1)(c)",
        "month_and_year_of_manufacture_or_packing": "Rule 6(1)(d)",
        "maximum_retail_price_mrp": "Rule 6(1)(e)",
        "consumer_care_details": "Rule 6(2)",
    }
    for field, clause in expected_clauses.items():
        matches = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS", field)
        assert len(matches) == 1, f"{field} should still resolve to exactly one provision"
        assert matches[0].rule_sub_rule_clause == clause
    for field, clause in {
        "name_and_address_of_manufacturer_or_packer": "Rule 24(a)",
        "identity_of_commodity": "Rule 24(b)",
        "total_number_of_retail_packages_or_net_quantity": "Rule 24(c)",
    }.items():
        matches = lookup_by_rule_id("LMPC-R24-WHOLESALE-DECLARATIONS", field)
        assert len(matches) == 1
        assert matches[0].rule_sub_rule_clause == clause
    # A rule-level (no field) LMPC-R6 request now returns exactly 8
    # provisions - the 7 pre-existing ones plus the new Rule 6(11) - never
    # duplicated, never contaminating a neighboring field.
    all_r6 = lookup_by_rule_id("LMPC-R6-MANDATORY-DECLARATIONS")
    assert len(all_r6) == 8
    assert len({p.provision_id for p in all_r6}) == 8


# --- get_legal_basis: the core safety invariant ---

def test_get_legal_basis_found_case_has_populated_provisions():
    result = get_legal_basis("LMPC-R6-MANDATORY-DECLARATIONS", "maximum_retail_price_mrp")
    assert result.status == "found"
    assert result.retrieval_method == "deterministic_link"
    assert len(result.provisions) == 1
    assert "retail sale price" in result.provisions[0].text


def test_get_legal_basis_not_available_case_never_carries_provisions():
    """The single most important invariant: status != 'found' implies
    provisions == [] - never a citation, never text, for an unmatched query.
    Uses a field with no corpus entry at all (unit_sale_price no longer
    qualifies as of the GSR 226(E) verification pass - see
    test_unit_sale_price_now_has_a_verified_rule_6_11_provision)."""
    result = get_legal_basis("LMPC-R6-MANDATORY-DECLARATIONS", "some_field_never_added_to_the_corpus")
    assert result.status == "not_available"
    assert result.retrieval_method is None
    assert result.provisions == []


def test_get_legal_basis_returns_the_correct_unit_sale_price_provision():
    """End-to-end through the same entry point the API layer calls (not
    just lookup_by_rule_id) - proves retrieval for
    (LMPC-R6-MANDATORY-DECLARATIONS, unit_sale_price) now returns Rule
    6(11), not a guess and not an empty result."""
    result = get_legal_basis("LMPC-R6-MANDATORY-DECLARATIONS", "unit_sale_price")
    assert result.status == "found"
    assert result.retrieval_method == "deterministic_link"
    assert len(result.provisions) == 1
    assert result.provisions[0].rule_sub_rule_clause == "Rule 6(11)"


def test_get_legal_basis_unknown_rule_id_also_not_available_not_an_error():
    result = get_legal_basis("LMGEN-R12-VERIFICATION-INTERVALS")
    assert result.status == "not_available"
    assert result.provisions == []


# --- Corpus validation (T013): a malformed corpus fails loudly at load time ---

def _write_corpus(tmp_path: Path, sources: list[dict], provisions: list[dict]) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"sources": sources, "provisions": provisions}), encoding="utf-8")
    return path


def test_load_corpus_rejects_source_missing_verification_note(tmp_path):
    bad_source = {
        "source_id": "X", "title": "T", "citation": "C", "version": "V",
        "acquired_at": "2026-01-01", "is_amendment": False, "amends_source_id": None,
        "verification_note": "",
    }
    path = _write_corpus(tmp_path, [bad_source], [])
    with pytest.raises(LegalCorpusError):
        load_corpus(path)


def test_load_corpus_rejects_provision_missing_text(tmp_path):
    good_source = {
        "source_id": "X", "title": "T", "citation": "C", "version": "V",
        "acquired_at": "2026-01-01", "is_amendment": False, "amends_source_id": None,
        "verification_note": "checked",
    }
    bad_provision = {
        "provision_id": "P1", "source_id": "X", "rule_sub_rule_clause": "Rule 1",
        "text": "", "effective_status": "original", "is_currently_effective": True,
        "linked_rule_id": "SOME-RULE", "linked_field": "some_field",
    }
    path = _write_corpus(tmp_path, [good_source], [bad_provision])
    with pytest.raises(LegalCorpusError):
        load_corpus(path)


def test_load_corpus_rejects_provision_with_unknown_source_id(tmp_path):
    orphan_provision = {
        "provision_id": "P1", "source_id": "DOES-NOT-EXIST", "rule_sub_rule_clause": "Rule 1",
        "text": "some text", "effective_status": "original", "is_currently_effective": True,
        "linked_rule_id": "SOME-RULE", "linked_field": "some_field",
    }
    path = _write_corpus(tmp_path, [], [orphan_provision])
    with pytest.raises(LegalCorpusError):
        load_corpus(path)
