from backend.services.compliance_engine import ComplianceEngine, _aggregate_checks, normalize_ocr_result


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
        "maximum_retail_price_mrp": {"value": "MRP Rs. 450", "detected": True, "confidence": 0.98},
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
    data = ocr(full_text="MRP: 149\nConsumer Care: 1800-123-4567\nEmail: care@example.com")
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
    data = ocr(full_text="MRP: Rs. 149\nConsumer Care: 1800-123-4567\nEmail: care@example.com")
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
    assert normalized.fields["maximum_retail_price_mrp"].value == "MRP Rs. 450"


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
    declaration is printed on Indian labels."""
    for text, expected_line in [
        ("Country of Origin: India\nNET WEIGHT: 60 g", "Country of Origin: India"),
        ("Country of Origin\nIndia\nNET WEIGHT: 60 g", "Country of Origin"),
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
    field, _result = _mrp_field("MRP Rs. 99")
    assert field.status == "PASS"


def test_mrp_detected_invalid_is_confirmed_non_compliant():
    """PRESENT_INVALID -> NON_COMPLIANT (FAIL), NOT downgraded to review:
    the MRP wording is positively identified (strong, confirming evidence
    this line IS the MRP declaration), and it contains no valid amount at
    all - a structural defect in the printed declaration itself."""
    field, result = _mrp_field("Maximum Retail Price: ABC")
    assert field.status == "FAIL"
    assert result.overall_status == "NON_COMPLIANT"


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


def test_unit_sale_price_detected_invalid_is_confirmed_non_compliant():
    """PRESENT_INVALID -> NON_COMPLIANT (FAIL): every unit_sale_price alias
    is already specific to a per-unit-price context, so detection here is
    strong evidence this line IS a unit-price declaration attempt - no
    valid per-unit amount in it is a confirmed defect, not uncertainty."""
    field, result = _unit_price_field("Unit Sale Price: N/A")
    assert field.status == "FAIL"
    assert result.overall_status == "NON_COMPLIANT"


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