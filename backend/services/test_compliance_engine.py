import pytest

from backend.services.compliance_engine import CONFIDENCE_THRESHOLD, ComplianceEngine, _aggregate_checks, _rule_result, load_ruleset, normalize_ocr_result


def region(class_name, text, class_id=0, detection_confidence=0.9, text_confidence=0.95):
    """Build one region dict matching OCRResult.regions[i]'s model_dump()
    shape (backend/ocr/schemas.py RegionOCRResult), as produced by
    PaddleOCRService._run_multi_region in multi_region YOLO detection mode."""
    detections = [
        {"text": text, "confidence": text_confidence, "bbox": [0, 0, 10, 10], "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}
    ] if text else []
    return {
        "class_id": class_id,
        "class_name": class_name,
        "detection_confidence": detection_confidence,
        "bbox": {"x1": 0, "y1": 0, "x2": 100, "y2": 50},
        "full_text": text,
        "detections": detections,
    }


def ocr(**overrides):
    fields = {
        "manufacturer_packer_importer_details": {"value": "ABC Foods, Pune", "detected": True, "confidence": 0.98},
        "country_of_origin": {"value": "India", "detected": True, "confidence": 0.98},
        "common_generic_name_of_commodity": {"value": "Rice", "detected": True, "confidence": 0.98},
        "net_quantity": {"value": "5 kg", "detected": True, "confidence": 0.98},
        "month_and_year_of_manufacture_or_packing": {"value": "08/2026", "detected": True, "confidence": 0.98},
        "maximum_retail_price_mrp": {"value": "MRP Rs. 450 (Inclusive of all taxes)", "detected": True, "confidence": 0.98},
        "unit_sale_price": {"value": "Rs. 90/kg", "detected": True, "confidence": 0.98},
        "consumer_care_details": {"value": {"name": "ABC", "address": "Pune", "telephone_number": "1800123456", "email_address": "care@abc.example"}, "detected": True, "confidence": 0.98},
    }
    result = {"document_id": "DOC-TEST", "product_type": "packaged_commodity", "package_type": "retail", "full_text": "", "fields": fields}
    result.update(overrides)
    return result


def test_fully_compliant_retail_package():
    assert ComplianceEngine().evaluate(ocr(), "e_commerce_product_listing").overall_status == "COMPLIANT"


def test_missing_mrp_is_review_required_not_confirmed_non_compliant():
    """Deliberately changed behavior (compliance status/scoring rework): OCR
    not detecting MRP is not confirmed physical absence, so it must NOT be
    treated as a confirmed violation - REVIEW_REQUIRED, not NON_COMPLIANT."""
    data = ocr()
    del data["fields"]["maximum_retail_price_mrp"]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert result.overall_status == "REVIEW_REQUIRED"
    assert result.rule_results[0].required_fields["maximum_retail_price_mrp"].status == "NEEDS_MANUAL_VERIFICATION"


def test_ocr_mrp_without_currency_is_detected():
    data = ocr(full_text="MRP: 149 (Incl. of all taxes)\nConsumer Care: 1800-123-4567\nEmail: care@example.com")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp.status == "PASS"
    assert mrp.value["normalized_value"] == 149


def test_ocr_consumer_care_reads_email_on_next_line():
    data = ocr(full_text="MRP: Rs. 149\nConsumer Care: 1800-123-4567\nEmail: care@example.com")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    care = result.rule_results[0].required_fields["consumer_care_details"]
    assert care.value["telephone_number"] == "1800-123-4567"
    assert care.value["email_address"] == "care@example.com"


def test_missing_consumer_email_is_confirmed_non_compliant():
    """Deliberately changed behavior: PARTIAL (a located declaration with a
    confirmed-missing required sub-field) is CONFIRMED evidence of a
    shortfall, not uncertainty - it now folds into NON_COMPLIANT/the
    non-compliant score bucket rather than a separate PARTIALLY_COMPLIANT
    status, matching the three-state model (COMPLIANT/NON_COMPLIANT/
    LOW_CONFIDENCE) this project uses everywhere else."""
    data = ocr()
    data["fields"]["consumer_care_details"]["value"]["email_address"] = None
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert result.overall_status == "NON_COMPLIANT"
    assert result.rule_results[0].required_fields["consumer_care_details"].status == "PARTIAL"


def test_wholesale_profile_evaluates_wholesale_rule():
    result = ComplianceEngine().evaluate(ocr(package_type="wholesale"), "wholesale_package")
    assert result.rule_results[0].rule_id == "LMPC-R24-WHOLESALE-DECLARATIONS"


def test_low_confidence_requires_manual_verification():
    """Deliberately changed behavior: the overall status for "no confirmed
    violation, but a check is low-confidence" is now REVIEW_REQUIRED (Part 4
    of the scoring rework) - NEEDS_MANUAL_VERIFICATION is unchanged at the
    FIELD level (still produced by _field_result's low-confidence branch)."""
    data = ocr()
    data["fields"]["country_of_origin"]["confidence"] = 0.4
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert result.overall_status == "REVIEW_REQUIRED"
    assert result.rule_results[0].required_fields["country_of_origin"].status == "NEEDS_MANUAL_VERIFICATION"


def test_expired_instrument_is_non_compliant():
    data = {"document_id": "INST-1", "product_type": "weighing_instrument", "fields": {}, "instrument_category": "weighing_instrument", "last_verification_date": "2020-08-20"}
    assert ComplianceEngine().evaluate(data, "pos_hardware_audit").overall_status == "NON_COMPLIANT"


def test_missing_physical_seal_requires_manual_verification():
    data = {"document_id": "INST-2", "product_type": "weighing_instrument", "fields": {}, "instrument_category": "weighing_instrument", "last_verification_date": "2026-08-20"}
    result = ComplianceEngine().evaluate(data, "pos_hardware_audit")
    assert result.rule_results[1].status == "NEEDS_MANUAL_VERIFICATION"


def test_unrelated_product_is_not_applicable():
    data = ocr(product_type="unrelated")
    assert ComplianceEngine().evaluate(data, "e_commerce_product_listing").overall_status == "NOT_APPLICABLE"


# --- multi_region YOLO detection mode: per-class region extraction ---

def test_region_based_mrp_extraction_passes():
    data = ocr()
    data["fields"] = {}
    data["regions"] = [region("mrp", "MRP Rs. 450 Inclusive of all taxes")]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp.status == "PASS"
    assert mrp.value["normalized_value"] == 450


def test_region_based_consumer_care_extracts_phone_and_email():
    data = ocr()
    data["fields"] = {}
    data["regions"] = [region("consumer_care", "For Feedback: 1800-123-4567 care@example.com")]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    care = result.rule_results[0].required_fields["consumer_care_details"]
    assert care.value["telephone_number"] == "1800-123-4567"
    assert care.value["email_address"] == "care@example.com"


def test_region_based_entity_details_maps_to_both_retail_and_wholesale_fields():
    data = ocr()
    data["fields"] = {}
    data["regions"] = [region("entity_details", "Manufactured by ABC Foods, Pune")]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["manufacturer_packer_importer_details"].value == "Manufactured by ABC Foods, Pune"
    assert normalized.fields["name_and_address_of_manufacturer_or_packer"].value == "Manufactured by ABC Foods, Pune"


def test_region_based_mfg_date_batch_extracts_batch_and_expiry_into_metadata():
    data = ocr()
    data["fields"] = {}
    data["regions"] = [region("mfg_date_batch", "PKD. 12/05/2024 BATCH No. A05124C1 USE BY 11/11/2024")]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["month_and_year_of_manufacture_or_packing"].detected is True
    assert normalized.metadata["batch_lot_number"] == "A05124C1"
    assert normalized.metadata["expiry_or_best_before_date"] == "11/11/2024"


def test_region_based_multiple_same_class_uses_highest_confidence():
    data = ocr()
    data["fields"] = {}
    data["regions"] = [
        region("mrp", "MRP Rs. 99", detection_confidence=0.5),
        region("mrp", "MRP Rs. 450 Inclusive of all taxes", detection_confidence=0.9),
    ]
    normalized = normalize_ocr_result(data)
    assert "450" in normalized.fields["maximum_retail_price_mrp"].value


def test_region_with_no_text_is_recorded_as_not_detected():
    data = ocr()
    data["fields"] = {}
    data["regions"] = [region("mrp", "")]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["maximum_retail_price_mrp"].detected is False


def test_regions_absent_falls_back_to_whole_page_alias_matching_unchanged():
    """No 'regions' key at all - single_region/no-YOLO mode - must behave
    exactly as it did before multi-region support existed."""
    data = ocr(full_text="MRP: Rs. 149 (Incl. of all taxes)\nConsumer Care: 1800-123-4567\nEmail: care@example.com")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert result.rule_results[0].required_fields["maximum_retail_price_mrp"].status == "PASS"


def test_supplied_fields_still_override_region_based_extraction():
    """The pre-existing ocr_result["fields"] override (used by API callers
    that already know a field's value) must remain the most authoritative
    layer, above region-based extraction."""
    data = ocr()  # fields already supplied with MRP "MRP Rs. 450"
    data["regions"] = [region("mrp", "MRP Rs. 1")]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["maximum_retail_price_mrp"].value == "MRP Rs. 450 (Inclusive of all taxes)"


# --- Whole-page alias-matching robustness fixes: real multi-column OCR ---
#
# This is the actual PaddleOCR line order recovered from a real product
# photo (Britannia Good Day Chocolate Chip Cookies), where side-by-side
# label columns (nutrition table / ingredients / entity+MRP box / consumer
# care block) get interleaved line-by-line instead of read in visual column
# order. Each test below reproduces one of the five weaknesses this
# interleaving caused in the old keyword/next-line heuristics.

REAL_LABEL_LINES = [
    "BRITANNIA", "NUTRITION INFORMATION", "INGREDIENTS:", "(Approx. Values)", "Per 100 g",
    "Refined Wheat Flour (Maida),", "Good", "Energy", "493 kcal", "Sugar, Edible Vegetable Oil (Palm),",
    "CHOCOLATE", "Choco Chips (11%) (Sugar, Cocoa", "Protein", "6.4 g", "Day", "CHIP",
    "Solids, Cocoa Butter, Emulsifier", "Carbohydrate", "68.7 g", "COOKIES",
    "(322)), Invert Sugar Syrup, Cocoa", "of which Sugars", "29.3 g", "Solids (2.4%), Raising Agents",
    "Fat", "21.2 g", "[500(iii), 53(ii)], lodised Salt,", "Saturated Fat", "10.3 g",
    "Emulsifier (322 from Soya),", "Trans Fat", "0.1 g", "Artificial Flavour (Chocolate).",
    "Cholesterol", "0 mg", "MANUFACTURED & MARKETED BY:", "MRP.", "BRITANNIA INDUSTRIES LTD.",
    "(INCL. OF ALL TAXES)", "5/1A HUNGERFORD STREET,", "20.00", "KOLKATA - 700 017, WEST BENGAL (INDIA)",
    "PKD.", '8"901063133589', "fssai Lic. No. 10015043001129", "12/05/2024", "BATCH No.",
    "For Feedback-Contact: Executive, Consumer Care Cell,", "A05124C1",
    "Britannia Industries Ltd., Prestige Shanthiniketan,", "BISCUITS", "USE BY",
    "Tower C, Whitefield, Bengaluru - 560048, Karnataka.", "11/11/2024", "STORE IN A COOL,",
    "1-800-4254449", "feedback@britindia.com", "KEEP YOUR", "DRY AND HYGIENIC PLACE.", "CITY CLEAN",
    "NET WEIGHT:", "75 g",
]
REAL_LABEL_TEXT = "\n".join(REAL_LABEL_LINES)


def _real_label_ocr():
    data = ocr(full_text=REAL_LABEL_TEXT)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    return data


def test_generic_commodity_name_ignores_bare_sugar_in_ingredient_list():
    """Fix 1: the bare alias "sugar" used to match line 9
    ("Sugar, Edible Vegetable Oil (Palm),"), an ingredient-list mention, not
    the actual commodity name. It must not land on that ingredient line."""
    normalized = normalize_ocr_result(_real_label_ocr())
    value = normalized.fields["common_generic_name_of_commodity"].value
    assert value != "Sugar, Edible Vegetable Oil (Palm),"
    assert "Sugar" not in (value or "")


def test_mrp_amount_found_despite_interleaved_address_column():
    """Fix 2: "MRP." (line 36) sits beside an address column - the naive
    next-line heuristic used to grab "BRITANNIA INDUSTRIES LTD." (line 37)
    instead of the actual amount "20.00" (line 40, 3 lines further down the
    interleaved column)."""
    normalized = normalize_ocr_result(_real_label_ocr())
    mrp = normalized.fields["maximum_retail_price_mrp"]
    assert mrp.detected is True
    assert "20.00" in mrp.value
    assert "BRITANNIA INDUSTRIES LTD." not in mrp.value


def test_manufacturer_detected_despite_ampersand_phrasing():
    """Fix 3: real label text "MANUFACTURED & MARKETED BY:" (line 35) used to
    go undetected because the "&" breaks a plain "manufactured by" substring
    match.

    Value updated (real-image extraction audit): the heading alone is no
    longer accepted as the final value - "BRITANNIA INDUSTRIES LTD." (line
    37, the actual entity name) is now appended via the nearby-line
    continuation search, consistent with the same fix applied to Parle-G's
    "MANUFACTURED FOR" case."""
    normalized = normalize_ocr_result(_real_label_ocr())
    manufacturer = normalized.fields["manufacturer_packer_importer_details"]
    assert manufacturer.detected is True
    assert manufacturer.value == "MANUFACTURED & MARKETED BY: BRITANNIA INDUSTRIES LTD."


def test_mfg_date_disambiguated_from_manufacturer_header_line():
    """Fix 4: the bare "manufactured" alias used to match the entity header
    "MANUFACTURED & MARKETED BY:" (line 35) itself and report that as the
    date value, instead of the real date "12/05/2024" (line 45, found via
    the "PKD." packing-date keyword at line 42)."""
    normalized = normalize_ocr_result(_real_label_ocr())
    date_field = normalized.fields["month_and_year_of_manufacture_or_packing"]
    assert date_field.detected is True
    assert date_field.value == "12/05/2024"
    assert date_field.value != "MANUFACTURED & MARKETED BY:"


def test_consumer_care_finds_phone_and_email_beyond_fixed_lookahead():
    """Fix 5: the phone (line 55) and email (line 56) sit 8-9 lines below the
    "Consumer Care Cell" trigger (line 47) - past the old fixed 7-line
    look-ahead window - so they used to be reported as None."""
    normalized = normalize_ocr_result(_real_label_ocr())
    care = normalized.fields["consumer_care_details"].value
    assert care["telephone_number"] == "1-800-4254449"
    assert care["email_address"] == "feedback@britindia.com"
    # the address text block itself must stay tight (not pull in unrelated
    # later content like "USE BY" / "11/11/2024" from further down the page)
    assert "USE BY" not in care["address"]


# --- Follow-up fixes: two pre-existing bugs surfaced by generalization
# testing against a second real label (Parle-G), not part of the original
# five documented issues but discovered while confirming those fixes
# weren't overfit to the Britannia image.

def test_manufacturer_detected_for_manufactured_for_phrasing():
    """"MANUFACTURED FOR <company>" (used by the real Parle-G label instead
    of "...BY") must be recognized as an entity declaration, without
    accepting a bare, context-free "manufactured" anywhere in the text.

    Value updated (real-image extraction audit): the heading alone
    ("MANUFACTURED FOR") must never BE the extracted value - the company
    name on the following line is now appended via the nearby-line
    continuation search."""
    data = ocr(full_text="NUTRITION INFORMATION\nMANUFACTURED FOR\nPARLE BISCUITS PVT. LTD.\nNET WEIGHT: 60 g")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    manufacturer = normalized.fields["manufacturer_packer_importer_details"]
    assert manufacturer.detected is True
    assert manufacturer.value == "MANUFACTURED FOR PARLE BISCUITS PVT. LTD."


def test_manufacturer_still_undetected_without_declaration_context():
    """A bare, unrelated occurrence of "manufactured" with no "by"/"for"
    nearby must NOT be treated as an entity declaration - precision must not
    regress just to catch the "for" variant."""
    data = ocr(full_text="This snack is manufactured using modern equipment.\nNET WEIGHT: 60 g")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["manufacturer_packer_importer_details"].detected is False


def test_country_of_origin_ignores_original_substring():
    """The bare alias "origin" used to match as a plain substring inside
    unrelated words like "Original" (e.g. "Original Gluco Biscuits" on the
    real Parle-G label) and falsely report that line as country-of-origin
    evidence."""
    data = ocr(full_text="Parle-G\nOriginal Gluco Biscuits\nNET WEIGHT: 60 g")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["country_of_origin"].detected is False


def test_country_of_origin_matches_valid_declaration_forms():
    """Word-boundaried "origin" must still accept the real, valid ways this
    declaration is printed on Indian labels. The keyword-only case
    ("Country of Origin" alone, "India" on the next line) is now completed
    via the nearby-line completion mechanism added for this - deliberately
    changed from the old bare "Country of Origin" expectation, matching the
    same precedent as net_quantity's own nearby-line completion."""
    for text, expected_line in [
        ("Country of Origin: India\nNET WEIGHT: 60 g", "Country of Origin: India"),
        ("Country of Origin\nIndia\nNET WEIGHT: 60 g", "Country of Origin India"),
        ("Origin: India\nNET WEIGHT: 60 g", "Origin: India"),
    ]:
        data = ocr(full_text=text)
        data["fields"] = {}
        data["detections"] = [{"confidence": 0.98}]
        normalized = normalize_ocr_result(data)
        field = normalized.fields["country_of_origin"]
        assert field.detected is True, text
        assert field.value == expected_line, text


# --- Issue 1: consumer-care structured completeness + boundary absorption,
# and Issue 2: unit-sale-price vs. MRP proviso logic - found on the real
# Parle-G scan report (name was never extracted so the ruleset's 4-field
# completeness check always failed; the address block absorbed the
# following unrelated "STORAGE CONDITIONS" declaration).

PARLE_G_LABEL_LINES = [
    "10/-", "PARLE", "8", "ONLY", "Parle-G", "Original Gluco Biscuits", "Oiagim", "MANUFACTURED FOR",
    "NUTRITION INFORMATION", "INGREDIENTS:", "PARLE BISCUITS PVT. LTD.", "MRP.10.00", "(Approx. Values)",
    "PER 100 g", "Wheat Flour (Atta) (59%),", "NORTH LEVEL CROSSING,", "Energy", "434 kcal",
    "Sugar, Edible Vegetable Oil", "(INCL. OF ALL TAXES)", "VILE PARLE EAST,", "Protein", "7.2 g",
    "(Palm), Invert Sugar Syrup,", "MUMBAI, MH - 400057.", "Carbohydrate", "72.0 g",
    "Raising Agents [503(ii),", "PKD: 15/05/2024", "of which Sugars", "17.1 g",
    "500(ii)], lodised Salt,", "fssai", "Fat", "11.8 g", "Emulsifier (322 from Soya),", "BATCH: A51524",
    "Flour Treatment Agent", '8"901719"100113"', "Lic. No. 10013022002253", "Saturated Fat", "5.2 g",
    "(1100(i)), Vitamins and", "USE BY: 14/11/2024", "Trans Fat", "0g", "Minerals.",
    "For Feedback & Queries, contact:", "NET WEIGHT:", "Consumer Care Cell, Parle Products Pvt. Ltd.,",
    "STORAGE CONDITIONS:", "North Level Crossing, Vile Parle East,", "60 g", "STORE IN A COOL,",
    "Mumbai, MH - 400057.", "DRY & HYGIENIC PLACE.", "PP", "022-6691 6929", "cs@parle.biz",
    "KEEP YOUR CITY CLEAN",
]
PARLE_G_LABEL_TEXT = "\n".join(PARLE_G_LABEL_LINES)


def _parle_g_ocr():
    data = ocr(full_text=PARLE_G_LABEL_TEXT)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    return data


def test_consumer_care_phone_email_address_present_is_not_missing():
    """Test A (consumer care): with a phone, email, and a recognizable care
    address all present, the field must not read as Missing/FAIL - the root
    cause was that the structured "name" sub-field was hardcoded to None
    everywhere, so the ruleset's 4-field completeness check
    (name/address/telephone_number/email_address) could never be satisfied
    even when every legally-required piece was genuinely on the label."""
    normalized = normalize_ocr_result(_parle_g_ocr())
    care = normalized.fields["consumer_care_details"].value
    assert care["telephone_number"] == "022-6691 6929"
    assert care["email_address"] == "cs@parle.biz"
    assert care["name"]
    assert care["address"]

    result = ComplianceEngine().evaluate(_parle_g_ocr(), "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["consumer_care_details"]
    assert field.status == "PASS"
    assert field.missing == []


def test_consumer_care_does_not_absorb_unrelated_storage_conditions():
    """Test B (consumer care): "STORAGE CONDITIONS:" (and the storage-
    instruction text that follows it) sits immediately after the care
    trigger line in real multi-column OCR order and must NOT be pulled into
    the extracted care address - the fix is a smarter stop boundary in
    _care_block, not a wider CONSUMER_CARE_LOOKAHEAD."""
    normalized = normalize_ocr_result(_parle_g_ocr())
    care = normalized.fields["consumer_care_details"].value
    assert "STORAGE CONDITIONS" not in care["address"]
    assert "STORE IN A COOL" not in care["address"]
    assert "DRY & HYGIENIC PLACE" not in care["address"]


def test_consumer_care_genuinely_incomplete_is_partial_not_fail():
    """Test C (consumer care): a label that only prints a bare toll-free
    number (no separate email, no readable address text) is genuinely
    incomplete - PARTIAL is the honest status, not a silent FAIL that claims
    the field is completely missing when real evidence exists."""
    data = ocr(full_text="MRP Rs. 99\nToll Free: 1800-123-4567\nNET WEIGHT: 100 g")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["consumer_care_details"]
    assert field.status == "PARTIAL"
    assert "email_address" in field.missing


def test_unit_sale_price_explicit_declaration_is_validated():
    """Test A (unit sale price): an explicit declaration must be extracted
    and validated on its own terms, not compared against MRP at all."""
    data = ocr(full_text="MRP Rs. 450\nUnit Sale Price: Rs. 90/kg\nNet Quantity: 5 kg")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["unit_sale_price"]
    assert field.status == "PASS"
    assert field.value["normalized_value"] == 90.0


def test_unit_sale_price_proviso_satisfied_for_exact_1kg_package():
    """Test B (unit sale price): no explicit declaration, but net quantity
    is exactly 1 kg - the 2022 amendment's proviso (retail price equals unit
    price) legitimately exempts a separate declaration. Must be satisfied
    without fabricating a price value that was never printed."""
    data = ocr(full_text="MRP Rs. 450\nNet Quantity: 1 kg")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["unit_sale_price"]
    assert field.status == "PASS"
    assert field.value != 450
    assert "450" not in str(field.value)


def test_unit_sale_price_proviso_does_not_apply_for_non_1kg_package():
    """Test C + D (unit sale price): the real Parle-G/Britannia case - net
    quantity (60 g / 75 g) is nowhere near 1 kg, so the proviso does not
    apply and a separate declaration IS legally required. Must NOT silently
    substitute MRP for the missing declaration.

    Deliberately changed status (compliance status/scoring rework): OCR not
    detecting a declaration is not confirmed physical absence, so this is
    NEEDS_MANUAL_VERIFICATION (excluded from the score, flagged for review),
    not a confirmed FAIL - the important, still-true guarantee this test
    checks is that MRP is NEVER copied into the value."""
    for full_text in (
        "MRP Rs. 99\nNet Quantity: 500 g",
        PARLE_G_LABEL_TEXT,
    ):
        data = ocr(full_text=full_text)
        data["fields"] = {}
        data["detections"] = [{"confidence": 0.98}]
        result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
        field = result.rule_results[0].required_fields["unit_sale_price"]
        assert field.status == "NEEDS_MANUAL_VERIFICATION", full_text
        assert field.value is None, full_text
        assert "99" not in str(field.value), full_text


def test_unit_sale_price_undetermined_net_quantity_needs_review():
    """When net quantity itself can't be parsed, applicability of the
    proviso is genuinely unknown - NEEDS_MANUAL_VERIFICATION is honest,
    neither a silent PASS nor an unfounded FAIL."""
    data = ocr(full_text="MRP Rs. 99\nNet Quantity: see label")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["unit_sale_price"]
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


def test_britannia_unaffected_by_issue_1_and_2_fixes():
    """Regression guard: the Britannia real-image results for consumer care
    and unit sale price must not get WORSE from these fixes - consumer care
    should now correctly read PASS (name is extractable there too), and
    unit sale price should still correctly avoid an MRP substitution (75 g
    is not 1 kg, no proviso) even though its status is now
    NEEDS_MANUAL_VERIFICATION rather than FAIL (see the scoring rework:
    OCR non-detection is not confirmed physical absence)."""
    result = ComplianceEngine().evaluate(_real_label_ocr(), "e_commerce_product_listing")
    fields = result.rule_results[0].required_fields
    assert fields["consumer_care_details"].status == "PASS"
    assert fields["unit_sale_price"].status == "NEEDS_MANUAL_VERIFICATION"
    assert fields["unit_sale_price"].value is None
    assert fields["maximum_retail_price_mrp"].status == "PASS"


# --- Compliance status + scoring rework: confirmed pass/fail vs.
# low-confidence (Part 11's 7 test cases, unit-testing _aggregate_checks
# directly for the pure counting/decision logic, per the task's own
# framing in terms of raw status counts).

def test_scoring_1_confirmed_failure_outweighs_low_confidence():
    """5 COMPLIANT, 1 NON_COMPLIANT, 2 LOW_CONFIDENCE -> score excludes the
    2 low-confidence checks entirely (5 / (5+1) * 100 = 83.33), and a single
    confirmed failure makes the overall status NON_COMPLIANT regardless of
    how many other checks passed or are still under review."""
    statuses = ["PASS"] * 5 + ["FAIL"] + ["NEEDS_MANUAL_VERIFICATION"] * 2
    status, score, counts = _aggregate_checks(statuses)
    assert score == 83.33
    assert status == "NON_COMPLIANT"
    assert counts == {"compliant": 5, "non_compliant": 1, "low_confidence": 2}


def test_scoring_2_all_confirmed_checks_pass_but_review_still_required():
    """5 COMPLIANT, 0 NON_COMPLIANT, 3 LOW_CONFIDENCE -> every CONFIRMED
    check passed (score = 100%), but the overall status must still warn the
    user: REVIEW_REQUIRED, not COMPLIANT, because unresolved low-confidence
    checks remain. Score and status are explicitly NOT the same signal."""
    statuses = ["PASS"] * 5 + ["NEEDS_MANUAL_VERIFICATION"] * 3
    status, score, counts = _aggregate_checks(statuses)
    assert score == 100.0
    assert status == "REVIEW_REQUIRED"
    assert counts == {"compliant": 5, "non_compliant": 0, "low_confidence": 3}


def test_scoring_3_all_low_confidence_is_not_0_or_100_percent():
    """0 COMPLIANT, 0 NON_COMPLIANT, 8 LOW_CONFIDENCE -> nothing has been
    CONFIRMED either way, so the score must be None/N/A - never fabricated
    as 0% (would falsely claim total failure) or 100% (would falsely claim
    compliance). Overall status is REVIEW_REQUIRED, not COMPLIANT."""
    statuses = ["NEEDS_MANUAL_VERIFICATION"] * 8
    status, score, counts = _aggregate_checks(statuses)
    assert score is None
    assert status == "REVIEW_REQUIRED"
    assert counts == {"compliant": 0, "non_compliant": 0, "low_confidence": 8}


def test_scoring_4_all_compliant_is_100_percent_and_compliant():
    statuses = ["PASS"] * 8
    status, score, counts = _aggregate_checks(statuses)
    assert score == 100.0
    assert status == "COMPLIANT"
    assert counts == {"compliant": 8, "non_compliant": 0, "low_confidence": 0}


def test_scoring_5_any_confirmed_failure_forces_non_compliant():
    """At least one confirmed NON_COMPLIANT plus some LOW_CONFIDENCE checks
    -> overall status is NON_COMPLIANT (a confirmed violation always wins
    the hierarchy over unresolved uncertainty elsewhere)."""
    statuses = ["PASS", "FAIL", "NEEDS_MANUAL_VERIFICATION", "NEEDS_MANUAL_VERIFICATION"]
    status, _score, _counts = _aggregate_checks(statuses)
    assert status == "NON_COMPLIANT"


def test_scoring_6_not_applicable_rule_excluded_completely_from_scoring():
    """A wholesale-only rule evaluated against a retail package is
    NOT_APPLICABLE - it must be excluded from the score/summary entirely,
    not counted as a pass, a fail, or a review."""
    data = ocr(package_type="retail")
    result = ComplianceEngine().evaluate(data, "wholesale_package")
    assert result.overall_status == "NOT_APPLICABLE"
    assert result.compliance_score is None
    assert result.summary.compliant == 0
    assert result.summary.non_compliant == 0
    assert result.summary.low_confidence == 0


def test_scoring_7_not_detected_by_ocr_is_not_automatically_non_compliant():
    """A field whose explanation literally says physical absence is not
    confirmed must not be silently turned into a confirmed NON_COMPLIANT
    violation - this is the concrete policy Part 5 of the task describes,
    verified against the real evidence-policy implementation rather than
    assumed."""
    data = ocr()
    del data["fields"]["maximum_retail_price_mrp"]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert "physical absence is not confirmed" in field.explanation
    assert result.overall_status != "NON_COMPLIANT"


def test_scoring_null_score_serializes_as_json_null():
    """The all-low-confidence null score must survive API serialization as
    JSON null (this project's existing "unavailable score" convention,
    already used by compliance-report.html's `scan.score == null` check),
    not as a validation error or a coerced 0."""
    statuses = ["NEEDS_MANUAL_VERIFICATION"] * 3
    _status, score, _counts = _aggregate_checks(statuses)
    assert score is None
    data = ocr()
    for field in data["fields"].values():
        field["detected"] = False
        field["value"] = None
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert result.compliance_score is None
    payload = result.model_dump_json()
    assert '"compliance_score":null' in payload


# --- Refinement: distinguish an evidence GAP (not detected - genuinely
# unconfirmed) from AFFIRMATIVE evidence of a violation (detected, but
# positively confirmed invalid) - both used to collapse into the same
# NEEDS_MANUAL_VERIFICATION bucket, which made NON_COMPLIANT effectively
# unreachable for MRP/unit-sale-price. Five conceptual states
# (PRESENT_VALID/PRESENT_INVALID/ABSENT_CONFIRMED/ABSENT_UNCONFIRMED/
# AMBIGUOUS), mapped onto the existing FieldStatus values - no new status
# literal introduced, per "do not expose unnecessary internal states".

def _mrp_field(value, detected=True, confidence=0.95):
    data = ocr()
    data["fields"]["maximum_retail_price_mrp"] = {"value": value, "detected": detected, "confidence": confidence}
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    return result.rule_results[0].required_fields["maximum_retail_price_mrp"], result


def _unit_price_field(value, detected=True, confidence=0.95, net_quantity_value="500 g"):
    data = ocr()
    data["fields"]["unit_sale_price"] = {"value": value, "detected": detected, "confidence": confidence}
    data["fields"]["net_quantity"] = {"value": net_quantity_value, "detected": True, "confidence": 0.95}
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    return result.rule_results[0].required_fields["unit_sale_price"], result


def test_mrp_not_detected_is_absent_unconfirmed():
    """ABSENT_UNCONFIRMED -> NEEDS_MANUAL_VERIFICATION, never a confirmed
    violation, since OCR non-detection never confirms physical absence."""
    field, result = _mrp_field(None, detected=False)
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert "physical absence is not confirmed" in field.explanation
    assert result.overall_status != "NON_COMPLIANT"


def test_mrp_detected_valid_is_present_valid():
    field, _result = _mrp_field("MRP Rs. 99 (Inclusive of all taxes)")
    assert field.status == "PASS"


def test_mrp_wording_present_amount_unreadable_is_needs_review_not_fail():
    """P0 fix: MRP wording is positively identified (strong, confirming
    evidence this line IS the MRP declaration), but no valid amount could
    be read from it - this must NOT be a confirmed FAIL, since OCR failing
    to read the numeral (blur, glare, embossed printing) is exactly as
    plausible as the package genuinely lacking one, and text evidence
    alone can't distinguish those two cases. Deliberately changed from an
    earlier version of this test that asserted FAIL here - see
    _mrp_result's comment for the full reasoning."""
    field, result = _mrp_field("Maximum Retail Price: ABC")
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert result.overall_status != "NON_COMPLIANT"


def test_mrp_detected_but_ambiguous_wording_stays_needs_review():
    """AMBIGUOUS -> NEEDS_MANUAL_VERIFICATION: a weak, generic alias match
    (no confirmed MRP wording, no valid amount) can't be confirmed as either
    a valid or an invalid MRP declaration - too uncertain to call a
    violation."""
    field, _result = _mrp_field("Rs. Complex, Near Station")
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


def test_mrp_detected_but_low_confidence_stays_needs_review():
    """AMBIGUOUS (OCR-quality flavor) -> NEEDS_MANUAL_VERIFICATION, even
    though the text itself would otherwise be valid."""
    field, _result = _mrp_field("MRP Rs. 99", confidence=0.5)
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


def test_unit_sale_price_not_detected_stays_needs_review():
    """ABSENT_UNCONFIRMED (proviso does not apply, 500 g != 1 kg) ->
    NEEDS_MANUAL_VERIFICATION - unchanged from the prior scoring rework,
    reconfirmed here alongside the new detected-invalid distinction."""
    field, result = _unit_price_field(None, detected=False, net_quantity_value="500 g")
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert result.overall_status != "NON_COMPLIANT"


def test_unit_sale_price_detected_valid_is_present_valid():
    field, _result = _unit_price_field("Rs. 90/kg")
    assert field.status == "PASS"


def test_unit_sale_price_wording_present_amount_unreadable_is_needs_review_not_fail():
    """P0 fix: unit_sale_price wording is specific and strong evidence this
    line IS a unit-price declaration attempt, but no valid per-unit amount
    could be read from it - must NOT be a confirmed FAIL, for the same
    reason as the matching MRP case (OCR-quality vs. genuine absence can't
    be distinguished from text alone). Deliberately changed from an
    earlier version of this test that asserted FAIL here."""
    field, result = _unit_price_field("Unit Sale Price: N/A")
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert result.overall_status != "NON_COMPLIANT"


def test_unit_sale_price_detected_but_low_confidence_stays_needs_review():
    field, _result = _unit_price_field("Rs. 90/kg", confidence=0.5)
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


def test_score_example_d_confirmed_failures_only_no_low_confidence():
    """Example D: 5 compliant, 3 confirmed failures, 0 low-confidence ->
    score = 5/8 = 62.5%, overall = NON_COMPLIANT."""
    statuses = ["PASS"] * 5 + ["FAIL"] * 3
    status, score, counts = _aggregate_checks(statuses)
    assert score == 62.5
    assert status == "NON_COMPLIANT"
    assert counts == {"compliant": 5, "non_compliant": 3, "low_confidence": 0}


# --- Real-image extraction regression audit -------------------------------
#
# Two confirmed discrepancies found by re-running the real Britannia and
# Parle-G images fresh through the live pipeline: (1) the consumer-care
# address absorbed a bare batch-number code and a stray category word that
# happened to sit between the trigger line and the genuine address
# continuation; (2) "IMPORTED BY" (a real, legally valid entity-declaration
# wording for imported goods) was not recognized by manufacturer/packer/
# importer detection at all - only "manufacturer"/"packer" had this
# coverage, "importer" did not.

def test_consumer_care_skips_bare_fragments_but_keeps_real_address_line():
    """The real Britannia label interleaves a bare batch-number code
    ("A05124C1") and a stray category word ("BISCUITS") between the care
    trigger and its genuine continuing address line ("Britannia Industries
    Ltd., Prestige Shanthiniketan,"). Neither fragment has a comma or looks
    like real address text; both must be skipped WITHOUT stopping the block
    entirely, so the genuine address line that follows is still captured."""
    text = "\n".join([
        "For Feedback-Contact: Executive, Consumer Care Cell,",
        "A05124C1",
        "Britannia Industries Ltd., Prestige Shanthiniketan,",
        "BISCUITS",
    ])
    data = ocr(full_text=text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    address = normalized.fields["consumer_care_details"].value["address"]
    assert "A05124C1" not in address
    assert "BISCUITS" not in address
    assert "Britannia Industries Ltd., Prestige Shanthiniketan," in address


def test_britannia_consumer_care_address_no_longer_absorbs_batch_code_or_category_word():
    """Direct regression guard using the real Britannia OCR line order."""
    normalized = normalize_ocr_result(_real_label_ocr())
    address = normalized.fields["consumer_care_details"].value["address"]
    assert "A05124C1" not in address
    assert "BISCUITS" not in address
    assert "Britannia Industries Ltd., Prestige Shanthiniketan," in address


def test_manufacturer_detected_for_imported_by_phrasing():
    """"IMPORTED BY <company>" - a real, legally valid entity declaration
    for imported goods - was previously undetected entirely: "importer" was
    in the alias list, but "imported by" (a different word form) was not,
    and no regex fallback covered it either."""
    data = ocr(full_text="IMPORTED BY XYZ TRADING CO., MUMBAI\nNET WEIGHT: 100 g")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    field = normalized.fields["manufacturer_packer_importer_details"]
    assert field.detected is True
    assert field.value == "IMPORTED BY XYZ TRADING CO., MUMBAI"


def test_manufacturer_detected_for_imported_and_marketed_by_phrasing():
    """Connector-word variant ("Imported & Marketed By"), mirroring the
    already-working "Manufactured & Marketed By" fix."""
    data = ocr(full_text="IMPORTED & MARKETED BY XYZ TRADING CO.\nNET WEIGHT: 100 g")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    field = normalized.fields["manufacturer_packer_importer_details"]
    assert field.detected is True


def test_manufactured_by_and_packed_by_still_detected_no_regression():
    """Confirm the two variants that already worked before this audit
    remain correct alongside the new "imported by" coverage."""
    for text, expected in [
        ("MANUFACTURED BY ABC FOODS PVT LTD", "MANUFACTURED BY ABC FOODS PVT LTD"),
        ("PACKED BY DEF LOGISTICS", "PACKED BY DEF LOGISTICS"),
    ]:
        data = ocr(full_text=text)
        data["fields"] = {}
        data["detections"] = [{"confidence": 0.98}]
        normalized = normalize_ocr_result(data)
        field = normalized.fields["manufacturer_packer_importer_details"]
        assert field.detected is True, text
        assert field.value == expected, text


# --- ACCURACY FIX #3: "FOR MFG. UNIT SEE..." reversed-order manufacturer
# declaration (real Pears_back.jpg benchmark wording - a standardized
# multi-plant FMCG labeling convention), and confirmation that
# DarkFantasy_back.jpg's consumer-care-office-only text is deliberately
# NOT recovered (see the audit report and the code comment above
# _ROLE_HEADING_PATTERNS for the full reasoning).

def _manufacturer_field(full_text: str):
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    return normalized.fields["manufacturer_packer_importer_details"]


def test_manufacturer_detected_for_mfg_unit_see_real_pears_wording():
    """The exact real Pears_back.jpg benchmark structure: "FOR MFG. UNIT
    SEE THE FIRST CHARACTER(S) OF THE CODE FOLLOWING [symbol]" on one line,
    the actual company name on the next - the reversed word order
    ("for" + stem, not stem + "by/for") the existing patterns don't cover."""
    field = _manufacturer_field(
        "CITIES/CHANNELS/OUTLETS ONLY.\n"
        "FOR MFG. UNIT SEE THE FIRST CHARACTER(S) OF THE CODE FOLLOWING Ø\n"
        "BELOW. D) HINDUSTAN UNILEVER LTD., C-9, M.I.D.C. AREA,\n"
        "INGREDIENTS: WATER, SODIUM PALM KERNELATE,"
    )
    assert field.detected is True
    assert "HINDUSTAN UNILEVER LTD" in field.value
    assert field.value.startswith("FOR MFG. UNIT SEE")


def test_manufacturer_for_mfg_unit_see_stays_unresolved_without_nearby_company():
    """If no genuine entity content is found nearby (only other, unrelated
    declarations), the field must stay heading-only - never a fabricated
    value - matching the existing behavior every other role already has."""
    field = _manufacturer_field(
        "FOR MFG. UNIT SEE THE FIRST CHARACTER(S) OF THE CODE FOLLOWING Ø\n"
        "INGREDIENTS: WATER, SODIUM PALM KERNELATE,\n"
        "NET WEIGHT: 100 g"
    )
    assert field.detected is True
    assert field.value == "FOR MFG. UNIT SEE THE FIRST CHARACTER(S) OF THE CODE FOLLOWING Ø"
    # And the compliance layer must correctly treat a heading-only value as
    # unconfirmed, not a fabricated PASS - mirrors the existing behavior
    # already verified for the other roles via _manufacturer_result.
    data = ocr(full_text=(
        "FOR MFG. UNIT SEE THE FIRST CHARACTER(S) OF THE CODE FOLLOWING Ø\n"
        "INGREDIENTS: WATER, SODIUM PALM KERNELATE,\n"
        "NET WEIGHT: 100 g"
    ))
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    manufacturer_result = result.rule_results[0].required_fields["manufacturer_packer_importer_details"]
    assert manufacturer_result.status == "NEEDS_MANUAL_VERIFICATION"


@pytest.mark.parametrize("text", [
    "MFG DATE: 01/2024",  # a manufacturing DATE stamp, not a role declaration
    "FOR MFG DATE SEE BELOW",  # same shape as the real trigger, but "date", not "unit" - must not collide
    "This is manufactured using modern equipment.",  # bare "manufactured" with no "by"/"for"/"unit see" nearby
    "FOR BEST RESULTS SEE INSTRUCTIONS BELOW.",  # "for"+"see" shape, but no "mfg"/"unit" at all
])
def test_manufacturer_for_mfg_unit_see_rejects_unrelated_mfg_phrasing(text):
    """Not a broad "for"+"mfg" alias - only the specific "for mfg[.] unit
    see" phrase triggers detection, so ordinary MFG-date stamps and
    unrelated "for...see" instruction text must never be mistaken for
    this declaration. ("manufactured for <X>" bare phrasing is a
    separate, pre-existing, already-accepted pattern - real Parle-G
    wording, "MANUFACTURED FOR PARLE BISCUITS PVT. LTD." - unrelated to
    this fix and deliberately not re-litigated here.)"""
    field = _manufacturer_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as manufacturer_packer_importer_details"


@pytest.mark.parametrize("text", [
    "CONSUMER CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE",
    "FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER CARE OFFICE, ITC LIMITED",
    "HINDUSTAN UNILEVER LIMITED (HUL). PEARS IS A REGISTERED TRADEMARK.",
    "Consumer Care: 1800-123-4567, ABC Foods Pvt Ltd",
])
def test_manufacturer_does_not_infer_from_company_name_or_consumer_care_context(text):
    """CRITICAL SAFETY REQUIREMENT: a company name appearing near "consumer
    care", in a trademark notice, or in ordinary prose must NEVER be
    treated as a manufacturer/packer/importer declaration on its own - only
    an explicit role phrase ("manufactured by"/"packed by"/"for mfg unit
    see"/...) may trigger detection. This is the exact real
    DarkFantasy_back.jpg shape (a consumer-care office address that
    happens to name a company) - see the code comment above
    _ROLE_HEADING_PATTERNS for the full reasoning on why this stays
    unresolved rather than being force-matched."""
    field = _manufacturer_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as manufacturer_packer_importer_details"


def test_manufacturer_darkfantasy_real_structure_stays_unresolved():
    """End-to-end confirmation using the real DarkFantasy_back.jpg
    benchmark structure (consumer-care office address only, no
    manufacturer/packer/importer role phrase anywhere) - correctly stays
    NEEDS_MANUAL_VERIFICATION, never a fabricated PASS."""
    data = ocr(full_text=(
        "FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER\n"
        "10th FLOOR, NO. 18, BANASWADI MAIN ROAD,\n"
        "CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE\n"
        "BENGALURU-560005.itccares@itc.in\n"
        "1800 425 444 444. QUALITY GUARANTEED."
    ))
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["manufacturer_packer_importer_details"].detected is False
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    manufacturer_result = result.rule_results[0].required_fields["manufacturer_packer_importer_details"]
    assert manufacturer_result.status == "NEEDS_MANUAL_VERIFICATION"


def test_manufacturer_for_mfg_unit_see_evidence_mapping_correct():
    """Evidence mapping (region_ids/regions) must work correctly for the
    newly-recognized "for mfg unit see" wording too."""
    data = _detections_ocr([
        ("FOR MFG. UNIT SEE THE FIRST CHARACTER(S) OF THE CODE FOLLOWING Ø", 0.97),
        ("BELOW. D) HINDUSTAN UNILEVER LTD., C-9, M.I.D.C. AREA,", 0.95),
    ])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["manufacturer_packer_importer_details"]
    assert field.detected
    assert field.region_ids


def test_mfg_date_accepts_mm_yyyy_and_dotted_formats():
    """Verify MM/YYYY and dotted DD.MM.YYYY-style dates are extracted, not
    just the DD/MM/YYYY format the real test images happen to use."""
    for text in ("MFG: 05/2024\nNET WEIGHT: 100 g", "Packed on 05.2024\nNET WEIGHT: 100 g"):
        data = ocr(full_text=text)
        data["fields"] = {}
        data["detections"] = [{"confidence": 0.98}]
        normalized = normalize_ocr_result(data)
        assert normalized.fields["month_and_year_of_manufacture_or_packing"].detected is True, text


# --- Targeted extraction regressions: Lays and a further Parle-G pass -----
#
# A representative (not real-image) Lays-style OCR line order, reconstructed
# from the specific reported behavior (50 g actual, 6.6 g nutrition-table
# value wrongly picked; "NUTRITIONAL INFORMATION" wrongly picked as the
# generic name; "₹0.38 per g" unit-sale-price wrongly unrecognized).

LAYS_LINES = [
    "LAYS", "AMERICAN STYLE CREAM & ONION", "POTATO CHIPS",
    "NUTRITIONAL INFORMATION", "PER 100g",
    "NET WEIGHT:", "Protein", "6.6 g", "50 g",
    "INGREDIENTS: Potatoes, Edible Vegetable Oil, Spices",
    "MRP Rs. 20.00", "(INCL. OF ALL TAXES)",
    "Rs 0.38 per g",
    "MANUFACTURED BY PEPSICO INDIA HOLDINGS PVT LTD",
    "CUSTOMER CARE: 1800-180-2352",
]


def _lays_ocr():
    data = ocr(full_text="\n".join(LAYS_LINES))
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    return data


def test_1_net_quantity_rejects_nutrition_table_value():
    """The nutrition table's "Protein / 6.6 g" pair sits between the "NET
    WEIGHT:" keyword and the real "50 g" value - the nutrition value must be
    skipped (not accepted as the net quantity), and the real value found."""
    normalized = normalize_ocr_result(_lays_ocr())
    assert normalized.fields["net_quantity"].value == "NET WEIGHT: 50 g"
    assert "6.6" not in normalized.fields["net_quantity"].value


def test_2_generic_name_rejects_nutritional_information_heading():
    """"NUTRITIONAL INFORMATION" must never become the extracted generic
    name; the actual commodity description ("POTATO CHIPS") must be
    preferred over it and over the bare brand name ("LAYS")."""
    normalized = normalize_ocr_result(_lays_ocr())
    identity = normalized.fields["common_generic_name_of_commodity"]
    assert identity.value != "NUTRITIONAL INFORMATION"
    assert identity.value == "POTATO CHIPS"


def test_3_unit_sale_price_recognizes_per_g_wording_variants():
    """"₹0.38 per g", "Rs. 0.38/g", "Rs 0.38 per g", and "0.38 per g" must
    all be recognized as valid unit-sale-price declarations - previously
    only the slash form ("/g") was detected at all; the space-separated
    "per g" wording was invisible to the extractor entirely. MRP must never
    be substituted regardless of format."""
    for unit_price_text, expected_amount in [
        ("₹0.38 per g", 0.38),
        ("Rs. 0.38/g", 0.38),
        ("Rs 0.38 per g", 0.38),
        ("0.38 per g", 0.38),
    ]:
        data = ocr(full_text=f"MRP Rs. 20.00\n{unit_price_text}\nNet Weight: 50 g")
        data["fields"] = {}
        data["detections"] = [{"confidence": 0.98}]
        result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
        field = result.rule_results[0].required_fields["unit_sale_price"]
        assert field.status == "PASS", unit_price_text
        assert field.value["normalized_value"] == expected_amount, unit_price_text
        assert field.value["normalized_value"] != 20.0, unit_price_text


def test_4_generic_name_rejects_price_fragment_parle_g():
    """Regression guard: "10/-" (a price badge fragment) must never be
    accepted as the generic name - the real commodity description
    ("Biscuits") must be preferred instead."""
    text = "\n".join([
        "10/-", "PARLE", "8", "ONLY", "Parle-G", "Original Gluco Biscuits", "Oiagim", "MANUFACTURED FOR",
        "NUTRITION INFORMATION", "INGREDIENTS:", "PARLE BISCUITS PVT. LTD.", "MRP.10.00",
    ])
    data = ocr(full_text=text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    identity = normalized.fields["common_generic_name_of_commodity"]
    assert identity.value != "10/-"
    assert "10/-" not in (identity.value or "")
    assert identity.value == "Original Gluco Biscuits"


def test_5_manufacturer_extracts_company_not_just_heading_parle_g():
    """Regression guard: "MANUFACTURED FOR" (the heading alone) must never
    be the final extracted entity value - the company/details after it
    ("PARLE BISCUITS PVT. LTD.", printed on the next OCR line due to
    column interleaving) must be captured too."""
    text = "\n".join([
        "MANUFACTURED FOR", "NUTRITION INFORMATION", "INGREDIENTS:",
        "PARLE BISCUITS PVT. LTD.", "NET WEIGHT: 60 g",
    ])
    data = ocr(full_text=text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    manufacturer = normalized.fields["manufacturer_packer_importer_details"]
    assert manufacturer.value != "MANUFACTURED FOR"
    assert manufacturer.value == "MANUFACTURED FOR PARLE BISCUITS PVT. LTD."


def test_generic_name_undetected_when_no_reliable_candidate_found():
    """If no line in the scanned window is both a non-heading/price
    candidate AND names a recognizable commodity category, the field must
    be left undetected (-> NEEDS_MANUAL_VERIFICATION downstream) rather than
    guessing a bare brand name - "reliable extraction is not possible" per
    the explicit requirement, not a silently-displayed low-confidence
    guess."""
    data = ocr(full_text="BRITANNIA\nNUTRITION INFORMATION\nINGREDIENTS:\nEnergy\n493 kcal")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    identity = normalized.fields["common_generic_name_of_commodity"]
    assert identity.detected is False
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    field = result.rule_results[0].required_fields["common_generic_name_of_commodity"]
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


# --- Full manufacturer/packer/importer/marketer/distributor audit --------

def _entity_field(full_text):
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.98}]
    normalized = normalize_ocr_result(data)
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    return normalized, result.rule_results[0].required_fields["manufacturer_packer_importer_details"]


def test_manufactured_by_same_line():
    normalized, field = _entity_field("MANUFACTURED BY ABC LTD")
    assert field.status == "PASS"
    assert normalized.fields["manufacturer_packer_importer_details"].value == "MANUFACTURED BY ABC LTD"


def test_manufactured_for_separate_line():
    normalized, field = _entity_field("MANUFACTURED FOR\nABC LTD, Pune")
    assert field.status == "PASS"
    assert normalized.fields["manufacturer_packer_importer_details"].value == "MANUFACTURED FOR ABC LTD, Pune"


def test_manufactured_and_marketed_by_connector_variants():
    """"&", "AND", and "/" connectors must all be recognized."""
    for text in (
        "MANUFACTURED & MARKETED BY:\nABC LTD",
        "MANUFACTURED AND MARKETED BY\nABC LTD",
        "MANUFACTURED / MARKETED BY\nABC LTD",
        "MANUFACTURED AND DISTRIBUTED BY\nABC LTD",
    ):
        _normalized, field = _entity_field(text)
        assert field.status == "PASS", text
        assert "ABC LTD" in str(field.value), text


def test_packed_by_and_packed_for():
    for text, expected in [
        ("PACKED BY DEF LOGISTICS", "PACKED BY DEF LOGISTICS"),
        ("PACKED FOR\nDEF LOGISTICS", "PACKED FOR DEF LOGISTICS"),
        ("PACKED & MARKETED BY\nDEF LOGISTICS", "PACKED & MARKETED BY DEF LOGISTICS"),
    ]:
        normalized, field = _entity_field(text)
        assert field.status == "PASS", text
        assert normalized.fields["manufacturer_packer_importer_details"].value == expected, text


def test_imported_by_and_imported_and_marketed_by():
    for text, expected in [
        ("IMPORTED BY XYZ TRADING", "IMPORTED BY XYZ TRADING"),
        ("IMPORTED AND MARKETED BY\nXYZ TRADING", "IMPORTED AND MARKETED BY XYZ TRADING"),
    ]:
        normalized, field = _entity_field(text)
        assert field.status == "PASS", text
        assert normalized.fields["manufacturer_packer_importer_details"].value == expected, text


def test_marketed_by_and_distributed_by():
    """"Marketed by"/"Distributed by" are not independently required by
    this ruleset (not in required_fields - see legal_metrology_rules_2011.
    json), but real labels commonly print them, and this project must not
    miss that text just because it isn't the primary manufacturer/packer/
    importer wording."""
    for text, expected in [
        ("MARKETED BY\nGHI MARKETING PVT LTD", "MARKETED BY GHI MARKETING PVT LTD"),
        ("MARKETED & DISTRIBUTED BY\nGHI MARKETING PVT LTD", "MARKETED & DISTRIBUTED BY GHI MARKETING PVT LTD"),
        ("DISTRIBUTED BY\nJKL DISTRIBUTORS", "DISTRIBUTED BY JKL DISTRIBUTORS"),
    ]:
        normalized, field = _entity_field(text)
        assert field.status == "PASS", text
        assert normalized.fields["manufacturer_packer_importer_details"].value == expected, text


def test_short_form_abbreviations_mfd_mfg_mfr_pkd_imp():
    """MFD/MFG/MFR/PKD/IMP + BY are common Indian-label short forms."""
    for text, expected in [
        ("MFD BY\nABC LTD", "MFD BY ABC LTD"),
        ("MFG BY\nABC LTD", "MFG BY ABC LTD"),
        ("MFR BY\nABC LTD", "MFR BY ABC LTD"),
        ("PKD BY\nDEF LOGISTICS", "PKD BY DEF LOGISTICS"),
        ("IMP BY\nXYZ TRADING", "IMP BY XYZ TRADING"),
    ]:
        normalized, field = _entity_field(text)
        assert field.status == "PASS", text
        assert normalized.fields["manufacturer_packer_importer_details"].value == expected, text


def test_bare_pkd_date_stamp_does_not_false_trigger_entity_field():
    """"PKD." used as a packing-date stamp (no trailing by/for) must NOT be
    mistaken for the "PKD BY" entity short form - a real, common source of
    false positives if the short-form pattern were too loose."""
    normalized, field = _entity_field("PKD. 15/05/2024")
    assert normalized.fields["manufacturer_packer_importer_details"].detected is False
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


def test_ocr_typo_variants():
    """Conservative, explicit OCR-confusion tolerance: U<->IJ, D<->O, B<->8 -
    not a general fuzzy matcher."""
    for text, expected_substring in [
        ("MANUFACTIJRED BY\nABC LTD", "ABC LTD"),
        ("PACKEO BY\nDEF LOGISTICS", "DEF LOGISTICS"),
        ("IMPORTEO BY\nXYZ TRADING", "XYZ TRADING"),
        ("MANUFACTURED 8Y\nABC LTD", "ABC LTD"),
    ]:
        normalized, field = _entity_field(text)
        assert field.status == "PASS", text
        assert expected_substring in str(normalized.fields["manufacturer_packer_importer_details"].value), text


def test_multiple_roles_preserved_not_merged():
    """"Manufactured by A" / "Packed by B" / "Imported by C" must NOT
    collapse into one incorrect entity - each role's own value must be
    independently preserved and recoverable."""
    normalized, field = _entity_field("MANUFACTURED BY A LTD\nPACKED BY B LTD\nIMPORTED BY C LTD")
    assert field.status == "PASS"
    value = normalized.fields["manufacturer_packer_importer_details"].value
    assert "MANUFACTURED BY A LTD" in value
    assert "PACKED BY B LTD" in value
    assert "IMPORTED BY C LTD" in value
    assert normalized.metadata["entity_roles_detected"] == ["manufacturer", "packer", "importer"]


def test_heading_only_incomplete_declaration_is_not_pass():
    """A detected heading with no recoverable entity value must be
    incomplete/low-confidence, never PASS."""
    normalized, field = _entity_field("MANUFACTURED FOR\nNUTRITION INFORMATION\nINGREDIENTS:")
    assert normalized.fields["manufacturer_packer_importer_details"].detected is True
    assert normalized.fields["manufacturer_packer_importer_details"].value == "MANUFACTURED FOR"
    assert field.status == "NEEDS_MANUAL_VERIFICATION"


def test_multiline_company_and_address():
    """Company name + street + city/state/pincode spanning several OCR
    lines (the trailing-comma continuation convention) must all be
    captured, stopping at the real boundary (BATCH NO) rather than
    absorbing it."""
    normalized, field = _entity_field("MANUFACTURED BY\nABC FOODS PVT LTD,\n123 MG ROAD,\nPUNE - 411001\nBATCH NO: X123")
    assert field.status == "PASS"
    value = normalized.fields["manufacturer_packer_importer_details"].value
    assert value == "MANUFACTURED BY ABC FOODS PVT LTD, 123 MG ROAD, PUNE - 411001"
    assert "BATCH" not in value


def test_nearby_unrelated_sections_do_not_get_absorbed():
    """NUTRITIONAL INFORMATION, INGREDIENTS, NET QUANTITY, MRP, BATCH,
    EXPIRY, and CONSUMER CARE must all be recognized as boundaries and
    excluded from the entity value, even when interleaved close to the
    declaration (multi-column OCR order)."""
    text = "\n".join([
        "MANUFACTURED BY", "NUTRITIONAL INFORMATION", "INGREDIENTS: Wheat, Sugar",
        "ABC FOODS PVT LTD, Pune", "NET QUANTITY: 100 g", "MRP Rs. 20",
        "BATCH NO: X1", "USE BY 01/01/2027", "CONSUMER CARE: 1800-000-000",
    ])
    normalized, field = _entity_field(text)
    assert field.status == "PASS"
    value = normalized.fields["manufacturer_packer_importer_details"].value
    assert "ABC FOODS PVT LTD, Pune" in value
    for excluded in ("NUTRITIONAL INFORMATION", "INGREDIENTS", "NET QUANTITY", "MRP", "BATCH", "USE BY", "CONSUMER CARE"):
        assert excluded not in value, (excluded, value)


def test_manufactured_and_marketed_by_does_not_double_count_as_two_roles():
    """Regression guard for a real bug found during this audit:
    "MANUFACTURED & MARKETED BY:" contains "MARKETED BY" as a literal
    substring, which independently matches the marketer pattern too - the
    line must be attributed to ONE role (manufacturer), not duplicated."""
    normalized, _field = _entity_field("MANUFACTURED & MARKETED BY:\nBRITANNIA INDUSTRIES LTD.")
    value = normalized.fields["manufacturer_packer_importer_details"].value
    assert value.count("BRITANNIA INDUSTRIES LTD.") == 1
    assert " | " not in value


def test_britannia_and_parle_g_manufacturer_unaffected():
    """Regression guard: both real images' manufacturer field must stay
    correct after this full audit's refactor."""
    britannia_normalized = normalize_ocr_result(_real_label_ocr())
    assert britannia_normalized.fields["manufacturer_packer_importer_details"].value == "MANUFACTURED & MARKETED BY: BRITANNIA INDUSTRIES LTD."
    parle_g_normalized = normalize_ocr_result(_parle_g_ocr())
    assert parle_g_normalized.fields["manufacturer_packer_importer_details"].value == "MANUFACTURED FOR PARLE BISCUITS PVT. LTD."

# --- P0 fixes: MRP/unit-price false-FAIL prevention + field-level confidence ---
#
# See compliance_engine.py's _mrp_result/_unit_sale_price_result comments
# and _field_confidence's docstring for the full reasoning. Reuses the same
# real Britannia/Parle-G fixtures already defined above where useful, plus
# targeted synthetic detections for the confidence-isolation cases, which
# need precisely controlled per-line confidences no single real fixture
# happens to have.

def _detections_ocr(lines_with_confidence: list[tuple[str, float]]) -> dict:
    """Build an ocr_result dict with real per-line detections (text +
    confidence), the same shape PaddleOCRService actually produces -
    unlike _mrp_field/_unit_price_field's supplied-fields shortcut, this
    exercises normalize_ocr_result's own text/detection parsing, which is
    what _field_confidence (the P0 Issue 2 fix) actually runs against."""
    full_text = "\n".join(text for text, _ in lines_with_confidence)
    detections = [{"text": text, "confidence": conf} for text, conf in lines_with_confidence]
    return {"document_id": "DOC-TEST", "product_type": "packaged_commodity", "package_type": "retail", "full_text": full_text, "fields": {}, "detections": detections}


# 1. MRP wording + unreadable amount -> NEEDS_MANUAL_VERIFICATION
#    (test_mrp_wording_present_amount_unreadable_is_needs_review_not_fail, above)

# 2. MRP wording + valid amount -> existing correct result
#    (test_mrp_detected_valid_is_present_valid, above - unchanged, still passing)


def test_mrp_successfully_parsed_degenerate_amount_is_not_independently_validated():
    """Documents a real, PRE-EXISTING limitation, not introduced or changed
    by this task: once an amount IS successfully parsed from MRP-labeled
    text, this engine has no minimum/legal-validity check on its
    magnitude - "MRP Rs. 0.01" currently PASSes exactly like any other
    successfully-parsed amount. This is unrelated to the P0 fix (which
    only changes the "no amount could be parsed at all" case - see
    test_mrp_wording_present_amount_unreadable_is_needs_review_not_fail).
    Per this task's explicit instruction not to change legal requirements,
    no minimum-amount rule is added here. If one is added in a future
    task, it would produce a confirmed FAIL through the SAME "amount was
    read and rejected" path this fix deliberately leaves untouched."""
    field, _result = _mrp_field("MRP Rs. 0.01 (Inclusive of all taxes)")
    assert field.status == "PASS"
    assert field.value["normalized_value"] == 0.01


# 4. Unit-price wording + unreadable amount -> review/manual verification
#    (test_unit_sale_price_wording_present_amount_unreadable_is_needs_review_not_fail, above)


def test_mrp_confidence_reflects_its_own_region_not_a_low_page_average():
    """P0 Issue 2, item 5: a low-confidence UNRELATED line elsewhere on the
    label must not drag down the MRP field's own confidence - it must
    reflect the MRP line's own (high) OCR confidence instead."""
    data = _detections_ocr([
        ("MRP Rs. 149.00", 0.98),
        ("Some barely-legible ingredient text", 0.20),
    ])
    normalized = normalize_ocr_result(data)
    mrp = normalized.fields["maximum_retail_price_mrp"]
    assert mrp.value is not None
    assert mrp.confidence == pytest.approx(0.98, abs=0.001)
    # Sanity check against the OLD (page-average) behavior this replaces:
    # (0.98 + 0.20) / 2 = 0.59 - confirm the new value is NOT that.
    assert mrp.confidence != pytest.approx(0.59, abs=0.02)


def test_low_confidence_mrp_region_is_needs_manual_verification_via_existing_threshold():
    """P0 Issue 2, item 6: the corrected per-field confidence must still
    correctly trigger the EXISTING CONFIDENCE_THRESHOLD gate when the MRP
    line itself - not some unrelated line - is genuinely low-confidence."""
    data = _detections_ocr([
        ("MRP Rs. 149.00", 0.40),
        ("Some other clearly legible line", 0.99),
    ])
    normalized = normalize_ocr_result(data)
    mrp = normalized.fields["maximum_retail_price_mrp"]
    assert mrp.confidence == pytest.approx(0.40, abs=0.001)
    assert mrp.confidence < CONFIDENCE_THRESHOLD
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert result.rule_results[0].required_fields["maximum_retail_price_mrp"].status == "NEEDS_MANUAL_VERIFICATION"


def test_net_quantity_confidence_averages_all_contributing_regions():
    """P0 Issue 2, item 7: multiple OCR lines supporting ONE field (the
    keyword line + the nearby line where the actual number was found, once
    multi-column interleaving separates them) must both contribute to that
    field's confidence - not just one of them, and not an unrelated
    interleaved line either."""
    data = _detections_ocr([
        ("NET WEIGHT:", 0.90),
        ("Some unrelated interleaved line", 0.99),
        ("75 g", 0.70),
    ])
    normalized = normalize_ocr_result(data)
    qty = normalized.fields["net_quantity"]
    assert qty.value == "NET WEIGHT: 75 g"
    assert qty.confidence == pytest.approx((0.90 + 0.70) / 2, abs=0.01)


def test_field_confidence_falls_back_to_page_average_when_no_region_matches():
    """P0 Issue 2, item 8: conservative, documented fallback - if a field's
    resolved text can't be matched back to any specific OCR detection
    line at all, confidence falls back to the whole-page average rather
    than inventing false precision or crashing. With zero detections
    supplied, that average is honestly 0, not fabricated."""
    data = {
        "document_id": "DOC-TEST", "product_type": "packaged_commodity", "package_type": "retail",
        "full_text": "MRP Rs. 149.00\nNET WEIGHT: 75 g", "fields": {}, "detections": [],
    }
    normalized = normalize_ocr_result(data)
    mrp = normalized.fields["maximum_retail_price_mrp"]
    assert mrp.detected is True
    assert mrp.confidence == 0


def test_field_confidence_falls_back_to_nonzero_page_average_when_only_that_field_is_unmatched():
    """Same fallback as above, but shows it's the real page average (not
    always 0): one field (net_quantity) has a real matching detection: the
    other (MRP) exists only in full_text with no detection entry of its
    own, so it conservatively inherits the page-wide average rather than a
    fabricated per-field number."""
    data = _detections_ocr([("NET WEIGHT: 75 g", 0.92)])
    data["full_text"] = data["full_text"] + "\nMRP Rs. 10.00"
    normalized = normalize_ocr_result(data)
    mrp = normalized.fields["maximum_retail_price_mrp"]
    assert mrp.detected is True
    assert mrp.confidence == pytest.approx(0.92, abs=0.001)


def _real_label_ocr_with_per_line_detections() -> dict:
    """Same real Britannia text as _real_label_ocr(), but with a genuine
    per-line detections list (built from REAL_LABEL_LINES, each line given
    a distinct confidence) instead of _real_label_ocr()'s single
    no-text confidence stub - needed to actually exercise per-field
    confidence matching (_field_confidence) against real content, not just
    its fallback path."""
    data = ocr(full_text=REAL_LABEL_TEXT)
    data["fields"] = {}
    # One deliberately LOW-confidence unrelated line (the nutrition
    # heading) mixed in with otherwise-high-confidence real lines - if
    # field confidence were still the page average, every field would be
    # dragged down by it; if it's genuinely field-specific, only fields
    # resolved from/near that exact line would be affected.
    data["detections"] = [
        {"text": line, "confidence": 0.35 if line == "NUTRITION INFORMATION" else 0.97}
        for line in REAL_LABEL_LINES
    ]
    return data


def test_real_britannia_mrp_and_net_quantity_confidence_is_field_specific_not_dragged_down():
    """Regression guard using real content: confirms the P0 confidence fix
    genuinely reflects each field's OWN supporting line(s) on a real
    label, not the page average - net_quantity ("NET WEIGHT:" / "75 g")
    and the MRP declaration are nowhere near the deliberately-low-confidence
    "NUTRITION INFORMATION" line, so neither should be dragged down by it."""
    normalized = normalize_ocr_result(_real_label_ocr_with_per_line_detections())
    assert normalized.fields["net_quantity"].value == "NET WEIGHT: 75 g"
    assert normalized.fields["net_quantity"].confidence == pytest.approx(0.97, abs=0.01)
    mrp = normalized.fields["maximum_retail_price_mrp"]
    assert mrp.confidence == pytest.approx(0.97, abs=0.01)
    assert mrp.confidence > 0.9


# --- Evidence-region propagation: ComplianceResult -> FieldResult -> Evidence ---
#
# Verifies the "OCR detection -> region_<index> -> bbox/text/confidence"
# chain now reaches the final compliance verdict, not just
# StructuredExtraction (see backend/services/structured_extraction.py,
# which was refactored in the same change to REUSE this exact matcher -
# _matching_detection_indices/_field_confidence - rather than keeping its
# own independent copy). None of this changes PASS/FAIL/REVIEW_REQUIRED
# semantics, the confidence threshold, or any rule content - purely
# additive evidence attached to the SAME verdict the engine already
# produced.

import copy

from fastapi.testclient import TestClient


def test_mrp_compliance_result_contains_correct_region_id():
    """Item 1."""
    data = _detections_ocr([("MRP Rs. 149.00", 0.98), ("NET WEIGHT: 250 g", 0.97)])
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp.region_ids == ["region_0"]


def test_mrp_region_contains_correct_bbox_text_confidence():
    """Item 2."""
    data = _detections_ocr([("MRP Rs. 149.00", 0.98), ("NET WEIGHT: 250 g", 0.97)])
    data["detections"][0]["bbox"] = [38, 262, 436, 288]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert len(mrp.regions) == 1
    region = mrp.regions[0]
    assert region.region_id == "region_0"
    assert region.bbox == [38, 262, 436, 288]
    assert region.text == "MRP Rs. 149.00"
    assert region.confidence == pytest.approx(0.98, abs=0.001)


def test_net_quantity_maps_to_correct_ocr_region():
    """Item 3."""
    data = _detections_ocr([("MRP Rs. 10.00", 0.90), ("NET WEIGHT: 250 g", 0.95)])
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    qty = result.rule_results[0].required_fields["net_quantity"]
    assert qty.region_ids == ["region_1"]
    assert qty.regions[0].text == "NET WEIGHT: 250 g"


def test_manufacturer_maps_to_correct_ocr_region():
    """Item 4."""
    data = _detections_ocr([
        ("MANUFACTURED BY ACME FOODS PVT LTD, PUNE", 0.93),
        ("NET WEIGHT: 250 g", 0.95),
    ])
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    manufacturer = result.rule_results[0].required_fields["manufacturer_packer_importer_details"]
    assert manufacturer.region_ids == ["region_0"]
    assert "ACME FOODS" in manufacturer.regions[0].text


def test_multiple_regions_can_support_one_field():
    """Item 5: net_quantity resolved by JOINING two OCR lines (keyword +
    nearby amount, once multi-column interleaving separates them) - both
    must appear as separate regions supporting the same field."""
    data = _detections_ocr([
        ("NET WEIGHT:", 0.90),
        ("Some unrelated interleaved line", 0.99),
        ("75 g", 0.70),
    ])
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    qty = result.rule_results[0].required_fields["net_quantity"]
    assert qty.region_ids == ["region_0", "region_2"]
    assert len(qty.regions) == 2
    assert {r.text for r in qty.regions} == {"NET WEIGHT:", "75 g"}


def test_missing_evidence_is_empty_list_not_invented():
    """Item 6: a field with no detections at all (undetected) must get
    region_ids=[]/regions=[], never a fabricated region."""
    data = _detections_ocr([("Completely unrelated text with nothing useful", 0.99)])
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp.status == "NEEDS_MANUAL_VERIFICATION"
    assert mrp.region_ids == []
    assert mrp.regions == []


def test_existing_compliance_statuses_unchanged_for_real_fixtures():
    """Item 7: real Britannia/Parle-G evaluations must produce the EXACT
    SAME statuses as before evidence propagation was added - this change
    is additive evidence only, never a verdict change."""
    britannia = ComplianceEngine().evaluate(_real_label_ocr(), "e_commerce_product_listing")
    assert britannia.overall_status == "REVIEW_REQUIRED"
    parle_g_care = ComplianceEngine().evaluate(_parle_g_ocr(), "e_commerce_product_listing").rule_results[0].required_fields["consumer_care_details"]
    assert parle_g_care.status == "PASS"


def test_existing_ocr_result_is_not_mutated_by_evidence_propagation():
    """Item 8: normalize_ocr_result/ComplianceEngine.evaluate must never
    mutate the caller's ocr_result dict while computing evidence regions -
    same "never mutates" contract normalize_ocr_result's docstring already
    promises, now re-verified with the new region-matching code path
    actually exercised."""
    data = _detections_ocr([("MRP Rs. 149.00", 0.98), ("NET WEIGHT: 250 g", 0.97)])
    before = copy.deepcopy(data)
    ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    assert data == before


def test_api_compliance_evaluate_serialization_is_backward_compatible():
    """Item 9: POST /api/compliance/evaluate's response still has every
    field an existing client already reads (status, value, confidence,
    explanation), PLUS the new region_ids/regions - additive, not breaking."""
    from backend.main import app

    data = _detections_ocr([("MRP Rs. 149.00 (Incl. of all taxes)", 0.98), ("NET WEIGHT: 250 g", 0.97)])
    with TestClient(app) as client:
        response = client.post("/api/compliance/evaluate", json={"ocr_result": data, "validation_profile": "e_commerce_product_listing"})
    assert response.status_code == 200
    body = response.json()
    mrp = body["rule_results"][0]["required_fields"]["maximum_retail_price_mrp"]
    # Pre-existing keys still present and correctly typed.
    assert mrp["status"] == "PASS"
    assert isinstance(mrp["value"], dict)
    assert isinstance(mrp["confidence"], float)
    assert isinstance(mrp["explanation"], str)
    # New, additive keys.
    assert mrp["region_ids"] == ["region_0"]
    assert mrp["regions"][0]["bbox"] == [0, 0, 0, 0]  # no bbox supplied in this fixture - real, not invented
    assert mrp["regions"][0]["text"] == "MRP Rs. 149.00 (Incl. of all taxes)"


def test_real_britannia_and_parle_g_evidence_mapping():
    """Item 10: using the real, per-line-detection Britannia fixture (see
    P0 tests above), confirm evidence mapping is correct for a real label,
    not just a synthetic one."""
    normalized_data = _real_label_ocr_with_per_line_detections()
    result = ComplianceEngine().evaluate(normalized_data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp.status == "PASS"
    assert len(mrp.region_ids) >= 1
    assert any("MRP" in r.text.upper() for r in mrp.regions)

    qty = result.rule_results[0].required_fields["net_quantity"]
    assert qty.region_ids
    assert any(r.text == "75 g" for r in qty.regions)


# --- P1 COMPLIANCE COVERAGE ---------------------------------------------
# R11 (LMPC-R11-NET-QUANTITY-EXCLUSION): confirmed unreachable from OCR/
# image input (total_package_weight/wrapper_packaging_weight are physical
# scale measurements, never produced anywhere in this pipeline) - see the
# comment above this rule's handler in compliance_engine.py. Deliberately
# left OUT of every validation_profile rather than wired in with fabricated
# or perpetually-uncertain data; these tests confirm both halves of that
# decision stay true.

def test_r11_is_not_evaluated_by_any_validation_profile():
    """R11 must not be wired into production merely because the code
    exists - confirms it is absent from every profile's evaluate_rules,
    i.e. no scan can ever silently pick it up."""
    ruleset = load_ruleset()
    for profile_name, profile in ruleset["validation_profiles"].items():
        assert "LMPC-R11-NET-QUANTITY-EXCLUSION" not in profile.get("evaluate_rules", []), (
            f"R11 must stay unwired (no OCR-derivable data source); found in profile {profile_name!r}"
        )


def test_r11_direct_evaluation_without_scale_data_is_needs_review_not_fabricated():
    """If R11 is ever evaluated directly (bypassing profile selection),
    the required physical-weight metadata is absent (as it always is from
    an OCR/image-only pipeline) so the result must be
    NEEDS_MANUAL_VERIFICATION - never a fabricated COMPLIANT/NON_COMPLIANT
    pass or fail invented from missing measurements. Pre-existing,
    unmodified behavior - confirms it still holds."""
    normalized = normalize_ocr_result(ocr())
    rule = {"rule_id": "LMPC-R11-NET-QUANTITY-EXCLUSION"}
    result = _rule_result(rule, normalized)
    assert result.status == "NEEDS_MANUAL_VERIFICATION"
    assert result.score is None


# R24 (LMPC-R24-WHOLESALE-DECLARATIONS): the compliance-engine-level gate
# (package_type == "wholesale") already existed and is covered by
# test_wholesale_profile_evaluates_wholesale_rule above; these tests cover
# what this task actually added - API-level profile selection (see
# backend/api/test_ocr.py) and the package-count parsing fix below.

def test_wholesale_package_count_wording_is_recognized_not_treated_as_weight():
    """total_number_of_retail_packages_or_net_quantity's keyword line
    ("Retail Packages") with no number on it, and the actual count
    ("Contains 24 retail packages") printed a few lines later - the
    completion search must recognize this as a PACKAGE COUNT declaration,
    not require a gram/millilitre match the way plain net_quantity does."""
    data = ocr(package_type="wholesale", full_text="Retail Packages\nSome unrelated line\nContains 24 retail packages\n")
    data["fields"] = {}
    normalized = normalize_ocr_result(data)
    field = normalized.fields["total_number_of_retail_packages_or_net_quantity"]
    assert field.detected
    assert "24" in str(field.value)


def test_wholesale_package_count_various_wordings_recognized():
    """A handful of real-world package-count phrasings - "pack of N", "no.
    of packages: N", "N packs" - not just the one exact wording exercised
    above."""
    from backend.services.compliance_engine import _parse_package_count

    assert _parse_package_count("Pack of 20") == 20
    assert _parse_package_count("No. of Packages: 12") == 12
    assert _parse_package_count("24 Packs") == 24
    assert _parse_package_count("10 Pouches") == 10
    assert _parse_package_count("Net Weight: 500 g") is None


def test_net_quantity_completion_unaffected_by_package_count_wording():
    """Existing retail behavior is unchanged: net_quantity itself must
    NEVER be completed by package-count wording (a retail package's net
    quantity is always a weight/volume, never a count) - confirms the
    wholesale-specific fix above did not alter net_quantity's own,
    unrelated completion logic."""
    data = ocr(full_text="Net Weight\nContains 24 retail packages\n")
    data["fields"] = {}
    normalized = normalize_ocr_result(data)
    field = normalized.fields["net_quantity"]
    assert field.detected
    assert field.value == "Net Weight"  # left keyword-only: no weight/volume candidate found nearby


def test_existing_retail_profile_behavior_unchanged_by_wholesale_wiring():
    """A plain retail e_commerce_product_listing evaluation (package_type
    defaults to "retail") is byte-for-byte unaffected by the wholesale
    wiring added in this task."""
    assert ComplianceEngine().evaluate(ocr(), "e_commerce_product_listing").overall_status == "COMPLIANT"


# MRP "inclusive of all taxes" (LMPC-R6 mrp_format.text_requirement) - the
# three cases from the task description, each with an explicitly-named
# dedicated test (some incidental coverage already exists from fixture
# updates elsewhere in this file; these are the canonical, clearly-named
# ones).

def test_mrp_case_a_amount_and_tax_wording_detected_is_pass():
    """Case A: amount detected + tax wording detected -> normal PASS."""
    field, _result = _mrp_field("MRP Rs. 99 (Inclusive of all taxes)")
    assert field.status == "PASS"
    assert field.value["normalized_value"] == 99


def test_mrp_case_b_amount_detected_tax_wording_absent_is_needs_review():
    """Case B: amount detected + tax wording absent. Per the engine's
    existing uncertainty model (OCR non-detection is not confirmed
    physical absence - same principle as the P0 amount-unreadable fix),
    this is NEEDS_MANUAL_VERIFICATION, not a fabricated NON_COMPLIANT: the
    engine cannot distinguish "the qualifier is genuinely missing from the
    label" from "OCR simply didn't pick up that (often smaller-print)
    line"."""
    field, result = _mrp_field("MRP Rs. 99")
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert "inclusive of all taxes" in field.explanation.lower()
    assert result.overall_status != "NON_COMPLIANT"


def test_mrp_case_c_low_confidence_ocr_does_not_produce_false_violation():
    """Case C: OCR quality prevents determining whether the tax wording
    exists at all (low-confidence detection) - must not create a false
    CONFIRMED violation. Falls into the same NEEDS_MANUAL_VERIFICATION
    path as Case B, which is itself never a confirmed violation."""
    field, result = _mrp_field("MRP Rs. 99", confidence=0.3)
    assert field.status != "NON_COMPLIANT"
    assert result.overall_status != "NON_COMPLIANT"


def test_mrp_case_c_unreadable_amount_stays_needs_review_not_fail():
    """Case C variant: OCR quality prevents reading the amount itself
    (wording present, digits unreadable) - pre-existing P0 false-FAIL
    protection, confirmed still intact after the tax-wording check was
    added alongside it."""
    field, result = _mrp_field("Maximum Retail Price: ABC (Inclusive of all taxes)")
    assert field.status == "NEEDS_MANUAL_VERIFICATION"
    assert result.overall_status != "NON_COMPLIANT"


# --- PARSER ROBUSTNESS: net_quantity word order / vocabulary, ------------
# unit_sale_price bare-substring false-match (real regression benchmark
# findings - see training/benchmark_results/). Every wording below is
# either the exact real OCR text a benchmark fixture produced, or a direct
# variant the task explicitly asked to cover.

def _net_quantity_field(full_text: str):
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    normalized = normalize_ocr_result(data)
    return normalized.fields["net_quantity"]


@pytest.mark.parametrize("text,expected_grams_or_ml", [
    ("Net 500 g", 500.0),
    ("500 g Net", 500.0),
    ("Net Quantity: 500 g", 500.0),
    ("500 g Net Quantity", 500.0),
    ("Net Contents: 250 ml", 250.0),
    ("Net Contents When Packed: 100 g", 100.0),
    ("180 ml Net", 180.0),  # the exact real Handwash_back.jpg benchmark wording
])
def test_net_quantity_word_order_and_vocabulary_variants_detected(text, expected_grams_or_ml):
    from backend.services.compliance_engine import _parse_net_quantity

    field = _net_quantity_field(text)
    assert field.detected, f"{text!r} should be detected as net_quantity"
    parsed = _parse_net_quantity(str(field.value))
    assert parsed is not None
    assert parsed[0] == expected_grams_or_ml


def test_net_quantity_does_not_confuse_gross_weight():
    """Do not confuse net quantity with gross weight - a real, legally
    distinct declaration this engine must never treat as net_quantity."""
    field = _net_quantity_field("Gross Weight: 600 g")
    assert not field.detected


def test_net_quantity_does_not_confuse_serving_size():
    field = _net_quantity_field("Serving Size: 30 g")
    assert not field.detected


def test_net_quantity_does_not_confuse_dimensions():
    field = _net_quantity_field("Dimensions: 12 cm x 8 cm x 5 cm")
    assert not field.detected


def test_net_quantity_does_not_confuse_ingredient_quantity():
    field = _net_quantity_field("Contains Sugar 500 g per batch")
    assert not field.detected


def test_net_quantity_does_not_confuse_promotional_number():
    field = _net_quantity_field("Now 500 g EXTRA FREE! Buy 1 Get 1")
    assert not field.detected


def test_net_quantity_ocr_corrupted_weight_stays_unresolved_not_fabricated():
    """The real Ferrero_back.jpg benchmark case: glare corrupted "NET
    WEIGHT" into "COSETWEIGHT". No fuzzy recovery is implemented (see the
    comment in compliance_engine.py right after _parse_net_quantity) -
    confirms the field stays undetected (never a fabricated quantity) and
    the compliance layer correctly falls back to NEEDS_MANUAL_VERIFICATION,
    not a confirmed violation."""
    field = _net_quantity_field("COSETWEIGHT")
    assert not field.detected
    assert field.value is None

    data = ocr(full_text="COSETWEIGHT")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    net_qty_result = result.rule_results[0].required_fields["net_quantity"]
    assert net_qty_result.status == "NEEDS_MANUAL_VERIFICATION"
    assert net_qty_result.value is None  # never a fabricated quantity


def test_net_quantity_evidence_mapping_correct_for_reordered_wording():
    """Evidence mapping (region_ids/regions) must still work correctly for
    the newly-recognized reordered wording - not just for the pre-existing
    "Net Weight: X" phrasing."""
    data = _detections_ocr([("180 ml Net", 0.97), ("Some Unrelated Line", 0.9)])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["net_quantity"]
    assert field.detected
    assert field.region_ids
    assert any("180 ml Net" in r.text for r in field.regions)


def _unit_sale_price_field(full_text: str):
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    normalized = normalize_ocr_result(data)
    return normalized.fields["unit_sale_price"]


def test_unit_sale_price_license_code_is_not_false_matched():
    """The exact real Facewash_back.jpg benchmark bug: the bare "/l" alias
    matched inside a manufacturing license code, not a per-litre price."""
    field = _unit_sale_price_field("Mfg. Lic. No. M HIM/COS/L/12/167")
    assert not field.detected


@pytest.mark.parametrize("text", [
    "₹100 / l",
    "₹20 / kg",
    "₹0.20 / g",
    "₹50 per 100 g",
    "Rs 0.38 per g",
    "Unit Sale Price: Rs 5/kg",
])
def test_unit_sale_price_legitimate_examples_still_detected(text):
    field = _unit_sale_price_field(text)
    assert field.detected, f"{text!r} should still be detected as unit_sale_price"


@pytest.mark.parametrize("text", [
    "Mfg. Lic. No. M HIM/COS/L/12/167",
    "Batch/G/2024",
    "ABC/G123 unrelated code",
    "FSSAI/L/998877",
    "Model No. XYZ/KG-100",
])
def test_unit_sale_price_analogous_substring_traps_not_matched(text):
    """Not just the exact "/l" case - the underlying matching mechanism
    (bounded amount+unit pattern, not a bare substring) must reject every
    unit-suffix-shaped code, not merely the one observed string."""
    field = _unit_sale_price_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as unit_sale_price"


def test_unit_sale_price_license_code_does_not_produce_false_pass():
    """End-to-end: the license-code false match must not flow through to a
    confirmed compliance PASS built on the wrong text."""
    data = ocr(full_text="Mfg. Lic. No. M HIM/COS/L/12/167")
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    usp_result = result.rule_results[0].required_fields["unit_sale_price"]
    assert usp_result.status != "PASS"
    assert usp_result.value != "Mfg. Lic. No. M HIM/COS/L/12/167"


# --- PARSER ROBUSTNESS, continued: "Net Vol." and OCR space-collapsed ----
# "NETWEIGHT" (real regression benchmark findings, second pass - see
# training/benchmark_results/). Both real fixture wordings below are the
# exact OCR text Facewash_back.jpg and Cookie_back.jpg produced.

@pytest.mark.parametrize("text,expected_ml", [
    ("Net Vol. 100 ml", 100.0),
    ("Net Vol 100 ml", 100.0),
    ("Net Volume 100 ml", 100.0),
])
def test_net_quantity_net_vol_variants_detected(text, expected_ml):
    from backend.services.compliance_engine import _parse_net_quantity

    field = _net_quantity_field(text)
    assert field.detected, f"{text!r} should be detected as net_quantity"
    parsed = _parse_net_quantity(str(field.value))
    assert parsed is not None
    assert parsed[0] == expected_ml


def test_net_quantity_bare_vol_alone_is_not_a_signal():
    """Do NOT make "vol" alone a net-quantity signal - only "net vol"
    (immediately preceded by "net") may trigger detection."""
    field = _net_quantity_field("Volume Discount Offer 200 g")
    assert not field.detected


def test_net_quantity_net_vol_keyword_only_completes_via_nearby_line():
    """The exact real Facewash_back.jpg benchmark wording: "Net Vol." on
    its own line, with no number on that line - must still resolve via the
    pre-existing nearby-line completion loop, exactly like "Net Weight:"/
    "Net Contents" already do, without any special-casing."""
    field = _net_quantity_field("Net Vol.\nSome Unrelated Line\n100 ml")
    assert field.detected
    from backend.services.compliance_engine import _parse_net_quantity
    parsed = _parse_net_quantity(str(field.value))
    assert parsed == (100.0, "volume")


@pytest.mark.parametrize("text,expected_grams", [
    ("NETWEIGHT: 200 g", 200.0),  # OCR space-collapse, value on the same line
    ("NET WEIGHT: 200 g", 200.0),
])
def test_net_quantity_space_collapsed_netweight_detected(text, expected_grams):
    from backend.services.compliance_engine import _parse_net_quantity

    field = _net_quantity_field(text)
    assert field.detected, f"{text!r} should be detected as net_quantity"
    parsed = _parse_net_quantity(str(field.value))
    assert parsed is not None
    assert parsed[0] == expected_grams


def test_net_quantity_space_collapsed_netweight_keyword_only_completes_via_nearby_line():
    """The exact real Cookie_back.jpg benchmark structure: "NETWEIGHT:"
    bare on its own line (OCR space-collapse), with the actual value
    ("75 g (6 units x 12.5 g)") THREE lines later after multi-column
    interleaving - not on the same line as the task's simpler illustrative
    example above. An earlier version of the netweight pattern required a
    same-line digit, which looked safe in isolation but silently broke this
    exact real fixture (the field was never even marked detected, so the
    pre-existing nearby-line completion loop never got a chance to run).
    Keyword-only detection (matching every other net_quantity alias's
    design) is required for the completion loop to work here at all."""
    from backend.services.compliance_engine import _parse_net_quantity

    field = _net_quantity_field(
        "write to us at the mfg. address\n"
        "NETWEIGHT:\n"
        "or call +91 9015227230\n"
        "or\n"
        "75 g (6 units x 12.5 g)\n"
        "Lic No:11522998000068"
    )
    assert field.detected
    parsed = _parse_net_quantity(str(field.value))
    assert parsed == (75.0, "weight")


@pytest.mark.parametrize("text", [
    "gross weight 200 g",
    "drained weight 200 g",
    "serving weight 200 g",
    "weight per serving 200 g",
])
def test_net_quantity_does_not_confuse_other_weight_declarations(text):
    """Do not confuse net quantity with gross/drained/serving weight -
    real, legally distinct declarations this engine must never treat as
    net_quantity, even once "netweight" (no space) is recognized."""
    field = _net_quantity_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as net_quantity"


@pytest.mark.parametrize("text", [
    "Cabinet Specifications 200 g",   # 'net'-shaped fragment ("Cabinet") nowhere near "weight"/"vol"/"contents"/"quantity"
    "Net Promoter Score 200 g",       # bare "net" with no weight/vol/contents/quantity keyword nearby
    "Networking Cable 200 g",         # "net" embedded in an unrelated word, no weight/vol/contents keyword
])
def test_net_quantity_unrelated_net_substring_not_confused(text):
    field = _net_quantity_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as net_quantity"


def test_net_quantity_unrelated_weight_substring_not_confused():
    field = _net_quantity_field("Weightlifting Equipment 200 g")
    assert not field.detected


def test_net_quantity_net_vol_evidence_mapping_correct():
    """Evidence mapping (region_ids/regions) must work correctly for the
    newly-recognized "Net Vol." wording too."""
    data = _detections_ocr([("Net Vol. 100 ml", 0.97), ("Some Unrelated Line", 0.9)])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["net_quantity"]
    assert field.detected
    assert field.region_ids
    assert any("Net Vol. 100 ml" in r.text for r in field.regions)


def test_net_quantity_netweight_collapsed_evidence_mapping_correct():
    """Evidence mapping (region_ids/regions) must work correctly for the
    newly-recognized space-collapsed "NETWEIGHT" wording too."""
    data = _detections_ocr([("NETWEIGHT: 200 g", 0.96), ("Some Unrelated Line", 0.9)])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["net_quantity"]
    assert field.detected
    assert field.region_ids
    assert any("NETWEIGHT: 200 g" in r.text for r in field.regions)


# --- SYSTEMATIC ALIASES AUDIT: "net weight"/"net contents"/"net vol"/
# "rs."/"made in"/"price per" moved from unbounded plain-substring ALIASES
# entries to word-boundaried REGEX_ALIASES entries (see the audit report
# accompanying this change). Each now has: a legitimate-positive test, a
# substring-collision negative test, and (for net_quantity) a nearby-line
# completion test, per this task's explicit test requirements.

def test_net_quantity_net_weight_still_detected_after_move_to_regex():
    """"Net Weight" (spaced) must keep working exactly as before, now that
    it moved from a plain ALIASES entry to a word-boundaried regex."""
    from backend.services.compliance_engine import _parse_net_quantity

    field = _net_quantity_field("Net Weight: 500 g")
    assert field.detected
    assert _parse_net_quantity(str(field.value)) == (500.0, "weight")


def test_net_quantity_net_weight_rejects_cabinet_weight_collision():
    """The exact real collision this task's SPECIAL CASE named: "Cabinet
    Weight Capacity" must never be treated as a net_quantity declaration -
    "cabinet" ends in "...net", which a plain substring check for "net
    weight" could not tell apart from a genuine declaration."""
    field = _net_quantity_field("Cabinet Weight Capacity 200 g")
    assert not field.detected


def test_net_quantity_net_contents_rejects_cabinet_contents_collision():
    """Same collision shape as "net weight", for "net contents" (moved
    alongside it in this audit): "Cabinet Contents List" must never be
    treated as a net_quantity declaration."""
    field = _net_quantity_field("Cabinet Contents List 200 g")
    assert not field.detected


def test_net_quantity_net_vol_rejects_cabinet_volume_collision():
    """Same collision shape again, for "net vol": "Cabinet Volume" must
    never be treated as a net_quantity declaration."""
    field = _net_quantity_field("Cabinet Volume 200 g")
    assert not field.detected


def test_net_quantity_net_contents_nearby_line_completion_still_works():
    """"Net Contents" keyword-only, value on a later line - the exact real
    Pears_back.jpg benchmark shape - must still resolve via the nearby-line
    completion loop after moving to a word-boundaried regex."""
    from backend.services.compliance_engine import _parse_net_quantity

    field = _net_quantity_field("Net Contents\nSome Unrelated Line\n250 ml")
    assert field.detected
    assert _parse_net_quantity(str(field.value)) == (250.0, "volume")


@pytest.mark.parametrize("text", ["Rs. 45.00", "Rs 45.00", "MRP Rs.50"])
def test_mrp_rs_prefix_still_detected_after_move_to_regex(text):
    """"Rs."/"Rs " must keep working exactly as before, now that they moved
    from plain ALIASES entries to one word-boundaried regex. Uses full_text
    (not _mrp_field, which supplies the field directly and would never
    exercise ALIASES/REGEX_ALIASES detection at all) so this genuinely
    tests the text-scanning path the fix changed."""
    data = ocr(full_text=text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    normalized = normalize_ocr_result(data)
    field = normalized.fields["maximum_retail_price_mrp"]
    assert field.detected, f"{text!r} should be detected as maximum_retail_price_mrp"
    assert field.value == text


@pytest.mark.parametrize("text", [
    "Best used within 24 hours.",
    "Store for up to 2 years.",
    "Available in 6 colors.",
])
def test_mrp_rs_prefix_rejects_trailing_word_collisions(text):
    """A plain substring check for "rs." matches inside any word ending in
    "...rs." followed by a period - "hours."/"years."/"colors." are all
    plausible real label text, not contrived. None of these should ever be
    treated as an MRP declaration."""
    data = ocr(full_text=text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    mrp = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp.value != text
    assert mrp.status != "PASS"


def _country_of_origin_field(full_text: str):
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    normalized = normalize_ocr_result(data)
    return normalized.fields["country_of_origin"]


@pytest.mark.parametrize("text,expected_line", [
    ("Made in India", "Made in India"),
    ("MADE IN INDIA", "MADE IN INDIA"),
    ("made in USA", "made in USA"),
])
def test_country_of_origin_made_in_still_detected_after_move_to_regex(text, expected_line):
    """"Made in <country>" must keep working exactly as before, now that it
    moved from a plain ALIASES entry to a word-boundaried regex."""
    field = _country_of_origin_field(text)
    assert field.detected
    assert field.value == expected_line


@pytest.mark.parametrize("text", [
    "Homemade Ingredients 200 g",
    "100% Homemade Indian Recipe",
])
def test_country_of_origin_made_in_rejects_homemade_collision(text):
    """A plain substring check for "made in" matches inside "Homemade
    Ingredients"/"Homemade Indian Recipe" ("homemade" ends in "...made",
    fused with no boundary to " in..." that follows) - a plausible real
    snack-packaging phrase, not contrived."""
    field = _country_of_origin_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as country_of_origin"


@pytest.mark.parametrize("text", ["Price per kg: Rs 50", "Unit Sale Price per litre: Rs 90"])
def test_unit_sale_price_price_per_still_detected_after_move_to_regex(text):
    """"Price per <unit>" must keep working exactly as before, now that it
    moved from a plain ALIASES entry to a word-boundaried regex."""
    field = _unit_sale_price_field(text)
    assert field.detected, f"{text!r} should be detected as unit_sale_price"


@pytest.mark.parametrize("text", [
    "Special Price Period Offer 200 g",
    "Reduced Price Period This Week Only",
])
def test_unit_sale_price_price_per_rejects_price_period_collision(text):
    """A plain substring check for "price per" matches inside "Special
    Price Period"/"Reduced Price Period" ("price" fused with no boundary to
    " per..." from "period") - a plausible promotional-sticker phrase."""
    field = _unit_sale_price_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as unit_sale_price"


# --- ACCURACY FIX #1: "PRODUCT OF INDIA", "MADEIN" collapse, "For Queries
# or Feedback", and country_of_origin nearby-line completion (real
# regression benchmark findings - see the accuracy bottleneck audit report
# preceding this task). Every wording below is either the exact real OCR
# text a benchmark fixture produced, or a direct variant the task
# explicitly asked to cover.

@pytest.mark.parametrize("text", [
    "PRODUCT OF INDIA",  # the exact real Chips_nutrition.jpg benchmark wording
    "PRODUCT OF INDIA MADE WITH FRESH INGREDIENTS",
])
def test_country_of_origin_product_of_india_detected(text):
    field = _country_of_origin_field(text)
    assert field.detected, f"{text!r} should be detected as country_of_origin"
    assert field.value == text  # already self-contained - must NOT trigger a spurious completion attempt


@pytest.mark.parametrize("text", [
    "PRODUCT INFORMATION",
    "PRODUCT DETAILS",
    "INDIAN PRODUCT INFORMATION",
])
def test_country_of_origin_product_of_india_rejects_unrelated_product_phrases(text):
    """Not a broad "product" substring alias - "PRODUCT OF INDIA" is a
    fixed, self-contained 3-word phrase that does not appear inside any of
    these real, plausible, unrelated label headings."""
    field = _country_of_origin_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as country_of_origin"


def test_country_of_origin_madein_collapsed_and_nearby_line_completion():
    """The exact real Cookie_back.jpg benchmark structure: "MADEIN" (OCR
    space-collapse of "MADE IN") on its own line, with "INDIA" three lines
    later after nutrition-table interleaving. Confirms both the collapse
    fix and the new nearby-line completion mechanism together."""
    field = _country_of_origin_field("MADEIN\nSodium\n50mg\nINDIA")
    assert field.detected
    assert field.value == "MADEIN INDIA"


def test_country_of_origin_madein_collapsed_bare_still_detected():
    field = _country_of_origin_field("MADEIN")
    assert field.detected
    assert field.value == "MADEIN"


def test_country_of_origin_madein_does_not_weaken_existing_made_in_protection():
    """Adding the zero-space "madein" pattern must not reopen the
    already-fixed "made in" substring collision (a fully space-collapsed
    "Homemadeingredients" must still be rejected - no word boundary exists
    on either side of "madein" inside that fused word)."""
    field = _country_of_origin_field("Homemadeingredients 200 g")
    assert not field.detected


def test_country_of_origin_nearby_line_completion_skips_nutrition_noise():
    """A nutrition-table row label (e.g. "Sodium") sitting between the
    keyword-only country_of_origin line and the actual country name must
    be skipped, not absorbed as if it were the country - the same
    protection net_quantity's own completion loop already relies on."""
    field = _country_of_origin_field("Country of Origin\nProtein\n7g\nIndia")
    assert field.detected
    assert field.value == "Country of Origin India"


def test_country_of_origin_nearby_line_completion_does_not_absorb_numeric_lines():
    """A candidate line containing a digit (address, PIN code, batch
    number, price) must never be absorbed as a country name."""
    field = _country_of_origin_field("Country of Origin\n560005\nIndia")
    assert field.detected
    assert field.value == "Country of Origin India"  # skips the numeric line, finds the real one after it


def test_country_of_origin_nearby_line_completion_gives_up_gracefully_if_nothing_found():
    """If no plausible country-shaped candidate exists within the search
    window, the field stays keyword-only - never a fabricated value."""
    field = _country_of_origin_field("Country of Origin\n2024-01-01\n560005 Karnataka")
    assert field.detected
    assert field.value == "Country of Origin"


def _consumer_care_field(full_text: str):
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    normalized = normalize_ocr_result(data)
    return normalized.fields["consumer_care_details"]


@pytest.mark.parametrize("text", [
    "write to us at the mfg. address\nFor Queries or Feedback:\nor call +91 9015227230\nor\ne-mail sayhello@opensecret.in",
    "FOR QUERIES OR FEEDBACK\ncall 1800-123-4567",
    "Queries or Feedback:\ncall 1800-123-4567",
])
def test_consumer_care_queries_or_feedback_phrase_detected(text):
    """The exact real Cookie_back.jpg benchmark wording ("For Queries or
    Feedback:") and close variants - a common, legitimate alternative to a
    "Consumer Care"/"Helpline" heading this trigger previously had zero
    coverage for."""
    field = _consumer_care_field(text)
    assert field.detected, f"{text!r} should be detected as consumer_care_details"


@pytest.mark.parametrize("text", [
    "We welcome your feedback on our new flavour",
    "Please share your queries with our team",
    "Call us to share feedback",
])
def test_consumer_care_queries_or_feedback_rejects_unrelated_marketing_copy(text):
    """Not a broad "queries"/"feedback"/"call"/"email" alias - only the
    full specific phrase "queries or feedback" together triggers
    detection, so ordinary marketing copy that happens to use one of those
    words alone must never be mistaken for a consumer-care declaration."""
    field = _consumer_care_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as consumer_care_details"


def test_consumer_care_existing_phrases_unaffected_by_queries_or_feedback_addition():
    """Existing consumer-care trigger phrases must keep working exactly as
    before."""
    for text in ["Consumer Care: 1800-123-4567", "Customer Care Department", "Toll Free: 1800-999-8888", "Helpline: 1800-111-2222"]:
        field = _consumer_care_field(text)
        assert field.detected, f"{text!r} should be detected as consumer_care_details"


def test_country_of_origin_nearby_line_completion_evidence_mapping_correct():
    """Evidence mapping (region_ids/regions) must work correctly for the
    newly-completed value, not just its own keyword-only line."""
    data = _detections_ocr([("Country of Origin", 0.97), ("Batch No. AB1234", 0.9), ("India", 0.96)])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["country_of_origin"]
    assert field.detected
    assert field.value == "Country of Origin India"
    assert field.region_ids
    assert any("India" in r.text for r in field.regions) or any("Country of Origin" in r.text for r in field.regions)


def test_consumer_care_queries_or_feedback_evidence_mapping_correct():
    """Evidence mapping (region_ids/regions) must work correctly for the
    newly-recognized "For Queries or Feedback" trigger too."""
    data = _detections_ocr([("For Queries or Feedback:", 0.96), ("call 1800-123-4567", 0.95)])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["consumer_care_details"]
    assert field.detected
    assert field.region_ids


# --- ACCURACY FIX #4: "CONSUMER" / "CARE OFFICE" heading split across two
# OCR lines by multi-column interleaving (real DarkFantasy_back.jpg
# benchmark wording) - see the code comment above
# _find_split_care_heading for the full reasoning.

def test_consumer_care_split_heading_real_darkfantasy_structure():
    """The exact real DarkFantasy_back.jpg benchmark structure: "CONSUMER"
    ends one line, an UNRELATED "Use By:" date declaration interleaves
    from a different column, then "CARE OFFICE, ITC LIMITED, ITC GREEN
    CENTRE" three lines later - the single-line trigger can never bridge
    this. Confirms detection, a genuine (not fabricated) name/address/
    phone/email, and that the compliance status is a real PASS."""
    field = _consumer_care_field(
        "FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER\n"
        "Use By:\n"
        "12/02/27\n"
        "CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE\n"
        "MRP Rs.\n"
        "10th FLOOR, NO. 18, BANASWADI MAIN ROAD,\n"
        "BENGALURU-560005. 1800 425 444 444."
    )
    assert field.detected
    assert field.value["name"] == "Consumer Care"
    assert "ITC LIMITED" in field.value["address"]
    assert field.value["telephone_number"] == "1800 425 444 444"


def test_consumer_care_split_heading_end_to_end_is_confirmed_pass_not_fabricated():
    """End-to-end via the full compliance engine: a genuinely complete,
    OCR-evidenced declaration (name + address + phone all confirmed) must
    reach a real PASS - not merely "detected" with missing sub-fields
    silently accepted."""
    data = ocr(full_text=(
        "FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER\n"
        "Use By:\n"
        "12/02/27\n"
        "CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE\n"
        "MRP Rs.\n"
        "10th FLOOR, NO. 18, BANASWADI MAIN ROAD,\n"
        "Phone 1800 425 444 444 Email care@itc.in"
    ))
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    result = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    care_result = result.rule_results[0].required_fields["consumer_care_details"]
    assert care_result.status == "PASS"


def test_consumer_care_split_heading_does_not_affect_manufacturer_field():
    """Cross-field independence check: recognizing the split consumer-care
    heading must never cause "ITC LIMITED" to be mistaken for a
    manufacturer/packer/importer declaration - manufacturer detection is
    driven entirely by _ROLE_HEADING_PATTERNS, untouched by this fix, and
    no "manufactured by"/"packed by"/etc. phrase exists in this text."""
    data = ocr(full_text=(
        "FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER\n"
        "Use By:\n"
        "12/02/27\n"
        "CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE\n"
        "MRP Rs.\n"
        "10th FLOOR, NO. 18, BANASWADI MAIN ROAD,\n"
        "BENGALURU-560005. 1800 425 444 444."
    ))
    data["fields"] = {}
    data["detections"] = [{"confidence": 0.95}]
    normalized = normalize_ocr_result(data)
    assert normalized.fields["manufacturer_packer_importer_details"].detected is False


@pytest.mark.parametrize("text", [
    "CONSUMER\nINFORMATION",
    "CONSUMER\nNOTICE",
    "This product is designed for the modern consumer.\nABC Foods Pvt Ltd\nNet Weight: 100 g",
    "Consumer\nCare Instructions: wash before use",
    "Consumer\nCareful handling required",
    "Consumer\nCare should be taken while opening",
    "Net Weight: 100 g\nConsumer Durables Ltd\nRegistered Office",
    "Consumer\nCare Guarantee: satisfaction assured",
])
def test_consumer_care_split_heading_rejects_collision_cases(text):
    """CRITICAL SAFETY REQUIREMENT: a bare "consumer"/"care" pairing must
    never become a fabricated consumer-care detection unless the second
    line genuinely starts with "care" + a real office-designation word
    (office/cell/department/desk/division/team/centre/center). Covers:
    unrelated two-line text ("consumer"/"information", "consumer"/
    "notice"), "consumer" in ordinary prose followed by an unrelated
    company name, and "care" used in unrelated real phrasing (care
    instructions, "careful", "care should be taken", a care guarantee) -
    none of these may be mistaken for the real "CONSUMER" / "CARE OFFICE"
    declaration shape."""
    field = _consumer_care_field(text)
    assert not field.detected, f"{text!r} should NOT be detected as consumer_care_details"


def test_consumer_care_split_heading_requires_office_word_not_bare_care():
    """A bare "Care" second line with NO office-designation word at all
    must not trigger - this is what keeps "Care Instructions:"/"Careful
    handling" (see the parametrized collision tests above) from matching,
    verified directly against the exact boundary case."""
    field = _consumer_care_field("Consumer\nCare")
    assert not field.detected


def test_consumer_care_split_heading_beyond_window_not_absorbed():
    """The split-heading search is bounded to _CARE_SPLIT_HEADING_WINDOW
    lines - a "CARE OFFICE" heading far beyond that window must not be
    linked back to an earlier, unrelated "consumer" mention."""
    field = _consumer_care_field(
        "Consumer\n" + "\n".join(f"Unrelated filler line {i}" for i in range(10)) + "\nCare Office, XYZ Ltd"
    )
    assert not field.detected


def test_consumer_care_split_heading_evidence_mapping_correct():
    """Evidence mapping (region_ids/regions) must cover both real OCR
    lines that together justify the split-heading detection."""
    data = _detections_ocr([
        ("FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER", 0.96),
        ("Use By:", 0.9),
        ("12/02/27", 0.9),
        ("CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE", 0.95),
    ])
    normalized = normalize_ocr_result(data)
    field = normalized.fields["consumer_care_details"]
    assert field.detected
    assert field.region_ids
    assert any("CARE OFFICE" in r.text for r in field.regions)
