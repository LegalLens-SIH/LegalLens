from __future__ import annotations

import copy
from pathlib import Path

import pytest

from backend.services.compliance_engine import ComplianceEngine
from backend.api.ocr import _image_report
from backend.ocr.schemas import OCRResult
from backend.services.legal_rules_engine import (
    LegalRulesEvaluator,
    RulesetValidationError,
    Truth,
    UNKNOWN,
    _apply_operator,
    _evidence_context,
    _trace_evidence,
    _evaluate_condition,
    _overall_data_driven_status,
    calculate_json_defined_values,
    calculate_maximum_permissible_error,
    calculate_pdp_area,
    load_legal_ruleset,
    _resolve_value_from,
    validate_ruleset,
)


SOURCE_RULESET = Path(__file__).resolve().parents[2] / "legal_metrology_packaged_commodities_rules.json"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["FAIL", "REVIEW_REQUIRED"], "NON_COMPLIANT"),
        (["REVIEW_REQUIRED", "NOT_ASSESSED"], "REVIEW_REQUIRED"),
        (["NOT_ASSESSED"], "REVIEW_REQUIRED"),
        (["PASS", "NOT_APPLICABLE"], "PASS"),
        (["PASS"], "PASS"),
        (["NOT_APPLICABLE"], "NOT_APPLICABLE"),
    ],
)
def test_canonical_json_aggregate_preserves_failure_unknown_and_applicability_semantics(statuses, expected):
    results = [{"status": status} for status in statuses]

    assert _overall_data_driven_status(results) == expected


def test_attached_ruleset_loads_all_rules_and_reports_no_check_rules_explicitly():
    ruleset = load_legal_ruleset()

    assert ruleset.path == SOURCE_RULESET.resolve()
    assert ruleset.rule_count == 75
    assert ruleset.rules_with_checks == 62
    assert ruleset.rules_without_checks == 13
    assert ruleset.sha256
    assert len({rule["id"] for rule in ruleset.rules}) == 75
    assert any("operators used by rules" in warning for warning in ruleset.validation_warnings)
    assert any("fact namespaces used by rules" in warning for warning in ruleset.validation_warnings)
    assert not any("Unresolved source lookup table" in warning for warning in ruleset.validation_warnings)
    assert any("derives candidates from lookup_tables.fourth_schedule_quantity_expression" in warning for warning in ruleset.validation_warnings)


def test_ruleset_validation_rejects_duplicate_ids_and_unknown_operators():
    ruleset = load_legal_ruleset()
    duplicate = copy.deepcopy(ruleset.data)
    duplicate["rules"].append(copy.deepcopy(duplicate["rules"][0]))
    with pytest.raises(RulesetValidationError, match="Duplicate rule id"):
        validate_ruleset(duplicate, ruleset.path)

    invalid_operator = copy.deepcopy(ruleset.data)
    invalid_operator["rules"][0]["check"]["op"] = "approx_equal"
    with pytest.raises(RulesetValidationError, match="unsupported"):
        validate_ruleset(invalid_operator, ruleset.path)

    invalid_condition = copy.deepcopy(ruleset.data)
    invalid_condition["rules"][0]["check"] = {"all": []}
    with pytest.raises(RulesetValidationError, match="non-empty list"):
        validate_ruleset(invalid_condition, ruleset.path)

    invalid_rule = copy.deepcopy(ruleset.data)
    invalid_rule["rules"][0]["unrecognized_key"] = True
    with pytest.raises(RulesetValidationError, match="unsupported keys"):
        validate_ruleset(invalid_rule, ruleset.path)


def _retail_facts(**package_overrides):
    package = {
        "package_type": "retail",
        "net_quantity_basis": "mass",
        "net_quantity_value": 250,
        "net_quantity_unit": "g",
        "net_quantity_in_g_or_ml": 250,
        "net_quantity_in_kg": 0.25,
        "net_quantity_in_kg_or_l": 0.25,
        "commodity": "Lotions",
        "is_sold_via_ecommerce": True,
        "is_cement_fertilizer_or_farm_produce_bag": False,
        "is_tobacco_product": False,
        "is_fast_food_packed_by_restaurant_or_hotel": False,
        "is_drug_under_DPCO_2013": False,
        "is_medical_device_declared_as_drug": False,
        "is_thread_in_coil_for_handloom_weavers": False,
        "is_garment_or_hosiery_sold_loose_inspectable": False,
        "is_loose_commodity_ordered_via_ecommerce": False,
        "is_imported": False,
        "is_combination_package": False,
        "is_group_package": False,
        "is_multi_piece_package": False,
        "is_alcoholic_beverage": False,
        "is_blown_formed_or_molded_on_container": False,
        "is_export_package": False,
        "is_multi_pack_of_same_commodity": False,
    }
    package.update(package_overrides)
    label = {
        "all_required_declarations_present": {"value": True, "evidence_ids": ["IMG-A:REGION-4"]},
        "generic_name": "Lotions",
        "net_quantity_basis": "mass",
        "net_quantity_declared_value": 250,
        "net_quantity_declared_unit": "g",
        "mrp_value": 99,
        "mrp_equals_unit_sale_price": False,
        "unit_sale_price_value": 0.39,
        "unit_sale_price_unit": "per g",
        "unit_sale_price_decimals": 2,
        "numeral_height_mm": 2,
        "net_qty_numeral_height_mm": 1.8,
        "net_qty_clear_space_above_mm": 1.8,
        "net_qty_clear_space_below_mm": 1.8,
        "net_qty_clear_space_left_mm": 3.6,
        "net_qty_clear_space_right_mm": 3.6,
        "languages": ["English"],
        "when_packed_qualifier_present": True,
    }
    return {"package": package, "label": label}


def test_all_seventy_five_source_rules_are_evaluated_with_trace_and_evidence():
    facts = _retail_facts(pdp_area_cm2=75, lot_size=200)
    evaluator = LegalRulesEvaluator()

    result = evaluator.evaluate({"facts": facts}, validation_profile="e_commerce_product_listing")

    loaded_ids = {rule["id"] for rule in evaluator.ruleset.rules}
    evaluated_ids = {rule["rule_id"] for rule in result["rules"]}
    assert len(loaded_ids) == 75
    assert evaluated_ids == loaded_ids
    assert result["summary"]["total_rules_loaded"] == 75
    assert result["summary"]["total_rules_evaluated"] == 75
    assert result["summary"]["total_rules_with_executable_checks"] == 62
    assert result["summary"]["status_counts"]["NOT_ASSESSED"] == 13

    declaration_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-4-1")
    assert declaration_rule["status"] == "PASS"
    assert "IMG-A:REGION-4" in declaration_rule["evidence_ids"]
    assert declaration_rule["trace"]

    unit_price_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-6-11")
    assert unit_price_rule["status"] == "PASS"
    font_size_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-7-2")
    assert font_size_rule["status"] == "PASS"
    whitespace_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-8-1-space")
    assert whitespace_rule["status"] == "PASS"
    third_schedule_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-11-4")
    assert third_schedule_rule["status"] == "PASS"
    fourth_schedule_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-12-1-2")
    assert fourth_schedule_rule["status"] == "REVIEW_REQUIRED"
    languages_rule = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-9-4")
    assert languages_rule["status"] == "PASS"


def test_rule_trace_carries_source_text_and_separate_confidence_values():
    evaluator = LegalRulesEvaluator()
    normalized_fields = {
        "country_of_origin": {
            "value": "India",
            "detected": True,
            "confidence": 0.94,
            "region_ids": ["region_7"],
            "regions": [{"region_id": "region_7", "text": "MADE IN INDIA", "confidence": 0.94}],
        },
    }

    result = evaluator.evaluate(
        normalized_fields=normalized_fields,
        validation_profile="e_commerce_product_listing",
        structured_extraction={
            "origin": {
                "country_of_origin": {
                        "value": "India",
                    "raw_text": "MADE IN INDIA",
                    "ocr_confidence": 0.94,
                    "extraction_confidence": 0.81,
                    "evidence_region_ids": ["region_7"],
                },
            },
        },
    )

    rule = next(item for item in result["rules"] if item["rule_id"] == "LM-6-1-aa")
    trace = next(item for item in rule["trace"] if item.get("field") == "label.country_of_origin")

    assert rule["title"] == rule["rule_name"]
    assert rule["category"] == "origin"
    assert trace["evidence"] == [{
        "evidence_id": "region_7",
        "source_text": "MADE IN INDIA",
        "ocr_confidence": 0.94,
        "extraction_confidence": 0.81,
    }]


def test_consumer_care_block_confidence_is_not_attributed_to_missing_name():
    context = _evidence_context(
        {"consumer_care_details": {
            "regions": [{"region_id": "region_care", "text": "Contact: 1800123456", "confidence": 0.98}],
        }},
        {"consumer_care": {"details": {
            "value": {"name": None, "telephone_number": "1800123456"},
            "raw_text": "Contact: 1800123456",
            "ocr_confidence": 0.98,
            "extraction_confidence": 0.9,
            "evidence_region_ids": ["region_care"],
        }}},
    )

    assert context["region_care"]["source_text"] == "Contact: 1800123456"
    assert context["region_care"]["ocr_confidence"] == 0.98
    trace_evidence = _trace_evidence(["region_care"], "UNKNOWN", context)
    assert trace_evidence[0]["extraction_confidence"] is None


def test_unknown_consumer_care_name_uses_heading_evidence_not_referenced_manufacturer():
    evaluator = LegalRulesEvaluator()
    result = evaluator.evaluate(
        normalized_fields={
            "consumer_care_details": {
                "value": {"name": None, "address": "See manufacturer address", "telephone_number": "1800123456", "email_address": "care@example.test"},
                "detected": True,
                "confidence": 0.98,
                "region_ids": ["care_heading", "manufacturer_address", "care_phone", "care_email"],
                "regions": [
                    {"region_id": "care_heading", "text": "For Queries or Feedback:", "confidence": 0.98},
                    {"region_id": "manufacturer_address", "text": "Manufactured by Acme, 1 Main Street", "confidence": 0.99},
                    {"region_id": "care_phone", "text": "Call 1800123456", "confidence": 0.98},
                    {"region_id": "care_email", "text": "care@example.test", "confidence": 0.98},
                ],
            },
        },
        validation_profile="e_commerce_product_listing",
        structured_extraction={
            "business_details": {"manufacturer": {
                "value": "Acme, 1 Main Street",
                "raw_text": "Manufactured by Acme, 1 Main Street",
                "ocr_confidence": 0.99,
                "extraction_confidence": 0.9,
                "evidence_region_ids": ["manufacturer_address"],
            }},
            "consumer_care": {"details": {
                "value": {"name": None, "address": "See manufacturer address"},
                "raw_text": "For Queries or Feedback: See manufacturer address",
                "ocr_confidence": 0.98,
                "extraction_confidence": 0.9,
                "evidence_region_ids": ["care_heading", "manufacturer_address"],
            }},
        },
    )

    rule = next(item for item in result["rules"] if item["rule_id"] == "LM-6-2")
    trace = next(item for item in rule["trace"] if item.get("field") == "label.consumer_care_name")

    assert rule["status"] == "REVIEW_REQUIRED"
    assert trace["actual"] == "UNKNOWN"
    assert [item["evidence_id"] for item in trace["evidence"]] == ["care_heading"]
    assert trace["evidence"][0]["source_text"] == "For Queries or Feedback:"
    assert trace["evidence"][0]["extraction_confidence"] is None


def test_missing_named_quantity_basis_lookup_uses_fourth_schedule_source_table():
    evaluator = LegalRulesEvaluator()
    facts = {"package": {"commodity": "Electric cables"}, "label": {}}
    value, detail = _resolve_value_from(
        "lookup_tables.quantity_basis_by_commodity_state_or_fourth_schedule",
        evaluator.ruleset,
        facts,
        {},
    )

    assert value == ["length", "mass"]
    assert "fourth_schedule_quantity_expression" in detail


@pytest.mark.parametrize(
    ("actual", "operator", "expected", "result"),
    [
        (2, "eq", 2, Truth.TRUE), (2, "ne", 3, Truth.TRUE),
        (2, "in", [1, 2], Truth.TRUE), (3, "not_in", [1, 2], Truth.TRUE),
        (3, "gt", 2, Truth.TRUE), (3, "gte", 3, Truth.TRUE),
        (2, "lt", 3, Truth.TRUE), (2, "lte", 2, Truth.TRUE),
        ("123456", "matches", "^[1-9][0-9]{5}$", Truth.TRUE),
        ("Hindi, English", "contains_any", ["Hindi", "English"], Truth.TRUE),
        ("dozen pieces", "not_contains_any", ["dozen", "gross"], Truth.FALSE),
        ("Maximum Retail Price", "contains", "retail", Truth.TRUE),
        (5, "between", [4, 6], Truth.TRUE),
        (UNKNOWN, "exists", None, Truth.UNKNOWN),
        (None, "not_exists", None, Truth.TRUE),
    ],
)
def test_every_source_operator_has_three_valued_semantics(actual, operator, expected, result):
    assert _apply_operator(actual, operator, expected) is result


def test_all_any_and_not_use_kleene_unknown_semantics():
    ruleset = load_legal_ruleset()
    facts = {"package": {"known": False}}
    all_result, _ = _evaluate_condition({"all": [
        {"field": "package.known", "op": "eq", "value": True},
        {"field": "package.missing", "op": "eq", "value": 1},
    ]}, facts, {}, ruleset, {}, "test")
    any_result, _ = _evaluate_condition({"any": [
        {"field": "package.known", "op": "eq", "value": False},
        {"field": "package.missing", "op": "eq", "value": 1},
    ]}, facts, {}, ruleset, {}, "test")
    not_result, _ = _evaluate_condition({"not": {"field": "package.missing", "op": "exists"}}, facts, {}, ruleset, {}, "test")
    assert all_result is Truth.FALSE
    assert any_result is Truth.TRUE
    assert not_result is Truth.UNKNOWN


def test_low_confidence_ocr_facts_stay_unknown_but_keep_evidence_ids():
    fields = {
        "common_generic_name_of_commodity": {
            "value": "A. PRODUCT NAME (2 units); B. PRODUCT NAME (2 units)",
            "detected": True,
            "confidence": 0.72,
            "region_ids": ["region_95", "region_96", "region_98", "region_99"],
        },
    }
    components = [
        {"name": "A. PRODUCT NAME", "quantity": "(2 units)", "name_region_ids": ["region_95"], "quantity_region_ids": ["region_98"]},
        {"name": "B. PRODUCT NAME", "quantity": "(2 units)", "name_region_ids": ["region_96"], "quantity_region_ids": ["region_99"]},
    ]
    result = LegalRulesEvaluator().evaluate(
        {"package_type": "retail"},
        normalized_fields=fields,
        normalized_metadata={"product_components": components},
        validation_profile="e_commerce_product_listing",
    )
    generic = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-6-1-b")
    multi = next(rule for rule in result["rules"] if rule["rule_id"] == "LM-6-1-b-multi")

    assert generic["status"] == "REVIEW_REQUIRED"
    assert generic["evidence_ids"] == ["region_95", "region_96", "region_98", "region_99"]
    assert multi["status"] == "REVIEW_REQUIRED"
    assert set(multi["evidence_ids"]) == {"region_95", "region_96", "region_98", "region_99"}


def test_rule_three_large_package_gate_excludes_chapter_two_only():
    facts = _retail_facts(net_quantity_value=30000, net_quantity_unit="g", net_quantity_in_g_or_ml=30000, net_quantity_in_kg=30, net_quantity_in_kg_or_l=30)
    result = LegalRulesEvaluator().evaluate({"facts": facts}, validation_profile="e_commerce_product_listing")
    assert result["applicability_gates"]["rule_3_chapter_ii_scope"] == "FALSE"
    chapter_two_results = [rule for rule in result["rules"] if rule["chapter"] == "II"]
    assert chapter_two_results
    assert all(rule["status"] == "NOT_APPLICABLE" for rule in chapter_two_results)


def test_rule_26_small_package_exemption_and_tobacco_exception_are_data_gated():
    evaluator = LegalRulesEvaluator()
    small_package = _retail_facts(net_quantity_value=10, net_quantity_unit="g", net_quantity_in_g_or_ml=10, net_quantity_in_kg=0.01, net_quantity_in_kg_or_l=0.01)
    exempt = evaluator.evaluate({"facts": small_package}, validation_profile="e_commerce_product_listing")
    assert exempt["applicability_gates"]["rule_26_package_exemption"] == "TRUE"
    assert all(rule["status"] == "NOT_APPLICABLE" for rule in exempt["rules"])

    tobacco = _retail_facts(net_quantity_value=10, net_quantity_unit="g", net_quantity_in_g_or_ml=10, net_quantity_in_kg=0.01, net_quantity_in_kg_or_l=0.01, is_tobacco_product=True)
    not_exempt = evaluator.evaluate({"facts": tobacco}, validation_profile="e_commerce_product_listing")
    assert not_exempt["applicability_gates"]["rule_26_package_exemption"] == "FALSE"
    assert not_exempt["rules"][0]["status"] != "NOT_APPLICABLE"


def test_mpe_pdp_fifth_schedule_sixth_schedule_and_deficiency_calculations():
    ruleset = load_legal_ruleset()
    package = {
        "net_quantity_basis": "mass", "net_quantity_value": 75, "net_quantity_unit": "g",
        "net_quantity_in_g_or_ml": 75, "net_quantity_in_kg": 0.075,
        "lot_size": 100,
        "pdp_geometry": {"shape": "rectangular", "height_cm": 10, "width_cm": 5},
        "is_blown_formed_or_molded_on_container": False,
    }
    assert calculate_maximum_permissible_error(package, ruleset)["mpe"] == 4.5
    assert calculate_pdp_area(package, ruleset)["pdp_area_cm2"] == 50

    samples = [{"net_quantity": 75, "required_declarations_present": True} for _ in range(50)]
    measurements = {"actual_net_quantities": samples, "actual_net_quantity_in_g_or_ml": 69, "tare_weight": 1}
    output = calculate_json_defined_values({"package": package, "inspection": measurements}, ruleset)
    assert output["package_level_deficiency"]["error_in_deficiency"] == 6
    assert output["package_level_deficiency"]["exceeds_mpe"] is True
    assert output["package_level_deficiency"]["exceeds_twice_mpe"] is False
    assert output["lot_level"]["status"] == "PASS"
    assert output["lot_level"]["plan"]["sample_size_n"] == 50
    assert output["sixth_schedule_tare"]["procedure"] == "single_tare_for_all_samples"

    ambiguous_length = calculate_maximum_permissible_error(
        {"net_quantity_basis": "length", "net_quantity_value": 12, "net_quantity_unit": "m"}, ruleset,
    )
    assert ambiguous_length["status"] == "NOT_ASSESSED"


def test_existing_compliance_engine_exposes_json_rules_without_replacing_legacy_contract():
    ocr = {
        "full_text": "MRP Rs. 99 (Inclusive of all taxes)\nNET WEIGHT: 250 g",
        "detections": [
            {"text": "MRP Rs. 99 (Inclusive of all taxes)", "confidence": 0.99},
            {"text": "NET WEIGHT: 250 g", "confidence": 0.99},
        ],
    }
    result = ComplianceEngine().evaluate(ocr, "e_commerce_product_listing")
    payload = result.model_dump(mode="json")

    assert len(result.rule_results) == 1
    assert result.rule_results[0].rule_id == "LMPC-R6-MANDATORY-DECLARATIONS"
    assert len(result.data_driven_evaluation.rules) == 75
    assert payload["data_driven_evaluation"]["provenance"]["source_file"] == "legal_metrology_packaged_commodities_rules.json"


def test_image_report_api_shaper_exposes_all_json_rule_results():
    ocr = {
        "full_text": "MRP Rs. 99 (Inclusive of all taxes)\nNET WEIGHT: 250 g",
        "detections": [
            {"text": "MRP Rs. 99 (Inclusive of all taxes)", "confidence": 0.99},
            {"text": "NET WEIGHT: 250 g", "confidence": 0.99},
        ],
    }
    compliance = ComplianceEngine().evaluate(ocr, "e_commerce_product_listing")

    class StructuredExtractionStub:
        @staticmethod
        def model_dump():
            return {"product": {}}

    report = _image_report(
        scan_id="SCN-RULESET", report_id="RPT-RULESET", image_id="IMG-RULESET",
        image={"filename": "label.jpg", "side": "Back", "sha256": "a" * 64},
        result=OCRResult(success=True, image="label.jpg"),
        structured_extraction=StructuredExtractionStub(), compliance=compliance, source_index=0,
    )

    exposed = report["compliance"]["data_driven_evaluation"]
    assert len(exposed["rules"]) == 75
    assert exposed["summary"]["total_rules_with_executable_checks"] == 62
    assert exposed["provenance"]["source_sha256"]
    assert report["compliance"]["overall_status"] == exposed["overall_status"]
    assert report["status"] == "manual_review"
    assert report["compliance"]["compliance_score"] is None
    assert report["compliance"]["legacy_comparison"]["overall_status"] == compliance.legacy_comparison.overall_status
    assert report["compliance"]["legacy_comparison"]["rule_results"] == report["compliance"]["rule_results"]


def test_realistic_multi_product_ocr_reaches_the_dynamic_rule_trace():
    lines = [
        "NUTRITIONAL INFORMATION", "Serving Size (per Cookie)-12.5g",
        "A. CHOCO ALMOND nutty cookies", "B. WHITE CHOCO CASHEW nutty cookies",
        "C. PEANUT BUTTER nutty cookies", "(2 units x 12.5g)",
        "(2 units x 12.5g)", "(2 units x 12.5g)", "NETWEIGHT: 75 g (6 units x 12.5 g)",
    ]
    ocr = {
        "full_text": "\n".join(lines),
        "detections": [{"text": line, "confidence": 0.98, "bbox": [index * 100, 100, index * 100 + 90, 125]} for index, line in enumerate(lines)],
    }

    result = ComplianceEngine().evaluate(ocr, "e_commerce_product_listing")
    dynamic_rules = result.data_driven_evaluation.rules
    generic = next(rule for rule in dynamic_rules if rule.rule_id == "LM-6-1-b")
    multi_product = next(rule for rule in dynamic_rules if rule.rule_id == "LM-6-1-b-multi")

    assert len(dynamic_rules) == 75
    assert generic.status == "REVIEW_REQUIRED"
    assert multi_product.status == "REVIEW_REQUIRED"
    assert {f"region_{index}" for index in range(2, 8)}.issubset(set(multi_product.evidence_ids))
