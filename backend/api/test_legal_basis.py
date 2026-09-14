"""Tests for GET /api/compliance/legal-basis (specs/001-legal-rag).

No authentication dependency exists on this endpoint (it is read-only,
scoped to an existing rule_id/field, and carries no manufacturer- or
official-specific data of its own) - so these tests use a plain TestClient
with no dependency_overrides, unlike test_manufacturer.py/test_official.py.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.compliance_engine import ComplianceEngine

client = TestClient(app)


# --- Found case (T022, T023) ---

def test_lmpc_r6_maximum_retail_price_returns_found_with_citation_and_source():
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R6-MANDATORY-DECLARATIONS", "field": "maximum_retail_price_mrp"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "found"
    assert body["retrieval_method"] == "deterministic_link"
    assert len(body["provisions"]) == 1
    provision = body["provisions"][0]
    assert provision["rule_sub_rule_clause"] == "Rule 6(1)(e)"
    assert provision["text"]
    assert provision["source"]["citation"]
    assert provision["source"]["version"]


def test_lmpc_r24_identity_of_commodity_returns_found():
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R24-WHOLESALE-DECLARATIONS", "field": "identity_of_commodity"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "found"
    assert body["provisions"][0]["rule_sub_rule_clause"] == "Rule 24(b)"


def test_lmpc_r6_rule_level_request_without_field_returns_all_verified_provisions():
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R6-MANDATORY-DECLARATIONS"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "found"
    assert len(body["provisions"]) == 8  # all 8 fields now verified, including unit_sale_price (GSR 226(E))


def test_lmpc_r6_unit_sale_price_returns_found_with_rule_6_11_citation_and_gsr226_source():
    """Targeted follow-up verification (see legal_corpus's
    IN-LM-PCR-2011-GSR226E-2022 source entry): unit_sale_price is no longer
    unresolved. Covers items 1, 2, 3, 4, 5 of that verification pass'
    required tests."""
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R6-MANDATORY-DECLARATIONS", "field": "unit_sale_price"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "found"
    assert body["retrieval_method"] == "deterministic_link"
    assert len(body["provisions"]) == 1
    provision = body["provisions"][0]
    assert provision["rule_sub_rule_clause"] == "Rule 6(11)"
    assert "unit sale price in rupees" in provision["text"]
    assert "retail sale price is equal to the unit sale price" in provision["text"]
    assert provision["effective_status"] == "amended"
    assert provision["is_currently_effective"] is True
    source = provision["source"]
    assert "GSR 226(E)" in source["citation"]
    assert "28th March 2022" in source["citation"]
    assert "01.04.2023" in source["citation"]  # verified final effective date, post-deferral
    assert source["is_amendment"] is True


# --- Missing-source / not-available behavior (T024, T025 - User Story 3) ---

def test_field_with_no_corpus_coverage_returns_not_available_not_an_error():
    # unit_sale_price no longer belongs here as of the GSR 226(E) follow-up
    # verification pass - see test_lmpc_r6_unit_sale_price_returns_found_with_rule_6_11_citation_and_gsr226_source.
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R6-MANDATORY-DECLARATIONS", "field": "some_field_never_added_to_the_corpus"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "not_available"
    assert body["provisions"] == []


def test_rule_id_with_zero_corpus_coverage_returns_not_available():
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMGEN-R12-VERIFICATION-INTERVALS"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "not_available"
    assert body["provisions"] == []


def test_unknown_rule_id_is_rejected_as_422_distinct_from_not_available():
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "NOT-A-REAL-RULE-ID"})
    assert response.status_code == 422


# --- Compliance-status immutability (T029, User Story 5) ---

def test_legal_basis_retrieval_never_affects_compliance_evaluation():
    """The constitutional core-boundary test: record a finding's status via
    the existing, completely separate /api/compliance/evaluate path, then
    call the legal-basis endpoint (success and not-available cases), and
    confirm ComplianceEngine's own output is byte-for-byte unaffected -
    proving there is no code path connecting the two."""
    engine = ComplianceEngine()
    ocr_dict = {
        "success": True, "image": "test.png", "full_text": "MANUFACTURED BY ACME FOODS, PUNE\nMRP Rs. 99 (Inclusive of all taxes)\nNet Weight: 250 g\n",
        "detections": [
            {"text": "MANUFACTURED BY ACME FOODS, PUNE", "confidence": 0.95, "bbox": [0, 0, 10, 10], "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
            {"text": "MRP Rs. 99 (Inclusive of all taxes)", "confidence": 0.95, "bbox": [0, 20, 10, 30], "polygon": [[0, 20], [10, 20], [10, 30], [0, 30]]},
            {"text": "Net Weight: 250 g", "confidence": 0.95, "bbox": [0, 40, 10, 50], "polygon": [[0, 40], [10, 40], [10, 50], [0, 50]]},
        ],
        "detection_count": 3,
    }
    before = engine.evaluate(ocr_dict, "e_commerce_product_listing")

    # Exercise the legal-basis endpoint - including unit_sale_price, now
    # that it resolves to Rule 6(11) (GSR 226(E)) rather than "not_available".
    # ComplianceEngine's own unit_sale_price validation (_unit_sale_price_result
    # in compliance_engine.py) must remain entirely unaffected by this
    # corpus change - it has no code path to backend/legal_corpus at all.
    client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R6-MANDATORY-DECLARATIONS", "field": "maximum_retail_price_mrp"})
    client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R6-MANDATORY-DECLARATIONS", "field": "unit_sale_price"})

    after = engine.evaluate(ocr_dict, "e_commerce_product_listing")

    # document_id (fresh per evaluate() call) and audit.evaluated_at
    # (a timestamp) are EXPECTED to differ between two separate calls -
    # that has nothing to do with this test's actual invariant. What must
    # be identical is every decision-relevant field: status, score, and the
    # full rule-by-rule findings themselves.
    assert before.overall_status == after.overall_status
    assert before.compliance_score == after.compliance_score
    assert before.summary == after.summary
    assert [r.model_dump() for r in before.rule_results] == [r.model_dump() for r in after.rule_results]
    assert before.missing_fields == after.missing_fields
    assert before.warnings == after.warnings
    assert before.recommendations == after.recommendations


# --- Citation correctness (T027, SC-003) ---

def test_citations_match_their_own_provision_content_no_mismatch():
    response = client.get("/api/compliance/legal-basis", params={"rule_id": "LMPC-R24-WHOLESALE-DECLARATIONS"})
    body = response.json()
    clauses_seen = {p["rule_sub_rule_clause"] for p in body["provisions"]}
    assert clauses_seen == {"Rule 24(a)", "Rule 24(b)", "Rule 24(c)"}
    for provision in body["provisions"]:
        # Rule 24(a) is about manufacturer/importer/packer identity - its own
        # text must actually be about that, not a mismatched neighbor's text.
        if provision["rule_sub_rule_clause"] == "Rule 24(a)":
            assert "manufacturer" in provision["text"].lower()
        if provision["rule_sub_rule_clause"] == "Rule 24(b)":
            assert "identity of the commodity" in provision["text"].lower()
        if provision["rule_sub_rule_clause"] == "Rule 24(c)":
            assert "net quantity" in provision["text"].lower() or "retail package" in provision["text"].lower()
