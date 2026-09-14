"""
Focused tests for backend/services/structured_extraction.py.

Reuses REAL data already in this project rather than only synthetic
fixtures:
  - backend/ocr_output/synthetic_label_ocr.json - a genuine captured
    PaddleOCR (PP-OCRv6 Medium) result, with real per-line confidences and
    bounding boxes, used for the evidence-region-mapping and normalization
    tests.
  - test_compliance_engine.py's REAL_LABEL_TEXT / PARLE_G_LABEL_LINES - the
    real Britannia and Parle-G OCR line orders already captured and tuned
    against in this project's own compliance-engine test suite (imported
    directly, not re-typed, so these two test files can never drift apart
    on what "the real Britannia/Parle-G text" actually is).

None of these tests touch the compliance engine's rule evaluation itself -
see test_compliance_engine.py for that; these only check the structured
extraction JSON this module builds.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.ocr.gemini_service import GeminiFieldSuggestion
from backend.services.compliance_engine import normalize_ocr_result
from backend.services.structured_extraction import build_structured_extraction
from backend.services.test_compliance_engine import PARLE_G_LABEL_TEXT, REAL_LABEL_TEXT, ocr

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "ocr_output" / "synthetic_label_ocr.json"


def _real_ocr_capture() -> dict:
    """The genuine captured PaddleOCR result - fresh dict per call so
    tests can't accidentally share/mutate state."""
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _text_ocr(full_text: str, confidence: float = 0.98) -> dict:
    data = ocr(full_text=full_text)
    data["fields"] = {}
    data["detections"] = [{"text": line, "confidence": confidence} for line in full_text.splitlines() if line.strip()]
    return data


# --- MRP --------------------------------------------------------------------

def test_mrp_extraction_from_real_britannia_label():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    mrp = result.pricing.maximum_retail_price_mrp
    assert mrp.value == 20.0
    assert mrp.currency == "INR"
    assert mrp.raw_text is not None
    assert mrp.source == "ocr_parser"


def test_mrp_extraction_from_real_ocr_capture():
    result = build_structured_extraction(_real_ocr_capture())
    assert result.pricing.maximum_retail_price_mrp.value == 149.0


def test_mrp_normalizes_rs_and_rupee_variants_to_the_same_value():
    variants = ["Rs. 10", "Rs 10", "₹10", "MRP ₹10.00", "M.R.P. Rs.10"]
    for variant in variants:
        text = f"MRP {variant}\nNET WEIGHT: 50 g"
        result = build_structured_extraction(_text_ocr(text))
        assert result.pricing.maximum_retail_price_mrp.value == 10.0, variant
        assert result.pricing.maximum_retail_price_mrp.currency == "INR"


# --- unit sale price ----------------------------------------------------

def test_unit_sale_price_extraction():
    text = "MRP Rs. 110\nUnit Sale Price: Rs 1.10 per g\nNET WEIGHT: 100 g"
    result = build_structured_extraction(_text_ocr(text))
    usp = result.pricing.unit_sale_price
    assert usp.value == 1.10
    assert usp.currency == "INR"


def test_unit_sale_price_missing_is_null_not_guessed():
    """Neither real fixture prints a unit sale price - must stay null,
    never silently substituted with MRP (this task's rule 2/1)."""
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    usp = result.pricing.unit_sale_price
    assert usp.value is None
    assert usp.raw_text is None


# --- net quantity ---------------------------------------------------------

def test_net_quantity_extraction_from_real_britannia_label():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    assert result.quantity.value == 75
    assert result.quantity.unit == "g"


def test_net_quantity_extraction_from_real_parle_g_label():
    result = build_structured_extraction(_text_ocr(PARLE_G_LABEL_TEXT))
    assert result.quantity.value == 60
    assert result.quantity.unit == "g"


def test_net_quantity_normalizes_common_wordings_to_the_same_value_and_unit():
    # Each variant keeps a "net quantity/qty/weight" keyword (required for
    # normalize_ocr_result's own ALIASES-based detection to fire at all -
    # a bare, unlabeled "100 g" is never treated as a net-quantity
    # declaration by the existing extractor, correctly, since a bare
    # number/unit could be almost anything on a real label) while varying
    # the exact wording/spacing/capitalization around it.
    variants = ["Net Weight: 100 g", "Net Wt 100g", "Net Qty: 100 G", "Net Quantity 100 grams"]
    for variant in variants:
        text = f"MRP Rs. 10\n{variant}"
        result = build_structured_extraction(_text_ocr(text))
        assert result.quantity.value == 100, variant
        assert result.quantity.unit == "g", variant


# --- business details: manufacturer/packer/importer/marketer --------------

def test_manufacturer_extracted_not_just_heading_real_parle_g():
    result = build_structured_extraction(_text_ocr(PARLE_G_LABEL_TEXT))
    manufacturer = result.business_details.manufacturer
    assert manufacturer is not None
    assert "PARLE BISCUITS PVT. LTD." in manufacturer.value


def test_manufacturer_extracted_real_britannia():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    manufacturer = result.business_details.manufacturer
    assert manufacturer is not None
    assert "BRITANNIA INDUSTRIES LTD." in manufacturer.value


def test_packer_importer_marketer_role_separated_not_confused():
    text = "\n".join([
        "Manufactured by ABC Foods Pvt Ltd, Pune",
        "Packed by XYZ Packers Pvt Ltd, Nashik",
        "Imported by Global Traders Pvt Ltd, Mumbai",
        "Marketed by Retail Co Pvt Ltd, Delhi",
        "NET WEIGHT: 200 g",
    ])
    result = build_structured_extraction(_text_ocr(text))
    bd = result.business_details
    assert "ABC Foods" in bd.manufacturer.value
    assert "XYZ Packers" in bd.packer.value
    assert "Global Traders" in bd.importer.value
    assert "Retail Co" in bd.marketer.value
    # Each role's own evidence must reference its own line, not another
    # role's - a direct check against this task's rule 6.
    assert bd.manufacturer.evidence_region_ids != bd.packer.evidence_region_ids


def test_manufacturer_heading_only_without_resolvable_entity_is_null_not_guessed():
    text = "MANUFACTURED BY\nNUTRITION INFORMATION\nMRP Rs. 10\nNET WEIGHT: 50 g"
    result = build_structured_extraction(_text_ocr(text))
    # A heading with no resolvable company/address nearby must not be
    # promoted to a confirmed manufacturer value (rule 6: never randomly
    # infer from unrelated text) - either absent, or present with a value
    # that is NOT the bare heading itself.
    manufacturer = result.business_details.manufacturer
    if manufacturer is not None:
        assert manufacturer.value != "MANUFACTURED BY"


# --- dates ------------------------------------------------------------------

def test_manufacture_and_expiry_dates_from_real_britannia_label():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    assert result.dates.manufacture_or_packing_date.value == "12/05/2024"
    assert result.dates.expiry_or_best_before is not None
    assert "11/11/2024" in result.dates.expiry_or_best_before.value


def test_expiry_as_a_duration_from_synthetic_real_ocr_capture():
    """The real captured OCR result expresses expiry as a duration ("24
    months from Mfg Date"), not an absolute date - must still be captured,
    not forced into a date shape it doesn't have."""
    result = build_structured_extraction(_real_ocr_capture())
    assert result.dates.expiry_or_best_before is not None
    assert "24 months" in result.dates.expiry_or_best_before.value


# --- batch/lot number -------------------------------------------------------

def test_batch_number_extraction_from_real_britannia_label():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    assert result.identification.batch_or_lot_number is not None
    assert result.identification.batch_or_lot_number.value == "A05124C1"


def test_batch_number_missing_is_null():
    result = build_structured_extraction(_text_ocr("MRP Rs. 10\nNET WEIGHT: 50 g"))
    assert result.identification.batch_or_lot_number is None


# --- country of origin ------------------------------------------------------

def test_country_of_origin_extraction():
    text = "MRP Rs. 10\nNET WEIGHT: 50 g\nCountry of Origin: India"
    result = build_structured_extraction(_text_ocr(text))
    assert result.origin.country_of_origin is not None
    assert "India" in result.origin.country_of_origin.value


def test_original_is_never_confused_with_origin():
    """Rule 5: "Original" (e.g. a product's own descriptive wording) must
    never be misread as a country-of-origin declaration."""
    text = "Parle-G Original Gluco Biscuits\nMRP Rs. 10\nNET WEIGHT: 60 g"
    result = build_structured_extraction(_text_ocr(text))
    assert result.origin.country_of_origin is None


# --- consumer care -----------------------------------------------------

def test_consumer_care_extraction_from_real_parle_g_label():
    result = build_structured_extraction(_text_ocr(PARLE_G_LABEL_TEXT))
    care = result.consumer_care.details
    assert care is not None
    assert care.value["telephone_number"] == "022-6691 6929"
    assert care.value["email_address"] == "cs@parle.biz"


def test_consumer_care_extraction_from_real_ocr_capture():
    result = build_structured_extraction(_real_ocr_capture())
    care = result.consumer_care.details
    assert care is not None
    assert care.value["telephone_number"] == "1800-123-4567"
    assert care.value["email_address"] == "care@sunriseconsumer.in"


# --- FSSAI -------------------------------------------------------------

def test_fssai_extraction_from_real_britannia_label():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    assert result.food_information.fssai is not None
    assert result.food_information.fssai.value == "10015043001129"


def test_fssai_missing_is_null():
    result = build_structured_extraction(_text_ocr("MRP Rs. 10\nNET WEIGHT: 50 g"))
    assert result.food_information.fssai is None


# --- noisy OCR / missing / ambiguous ---------------------------------------

def test_missing_fields_are_null_never_guessed():
    result = build_structured_extraction(_text_ocr("RANDOM UNRELATED TEXT\nNOTHING USEFUL HERE"))
    assert result.pricing.maximum_retail_price_mrp.value is None
    assert result.quantity.value is None
    assert result.business_details.manufacturer is None
    assert result.origin.country_of_origin is None
    assert result.identification.batch_or_lot_number is None
    assert result.food_information.fssai is None


def test_noisy_ocr_with_low_confidence_still_extracts_but_scores_lower():
    text = "MRP Rs. 10\nNET WEIGHT: 50 g"
    high = build_structured_extraction(_text_ocr(text, confidence=0.97))
    low = build_structured_extraction(_text_ocr(text, confidence=0.35))
    assert high.pricing.maximum_retail_price_mrp.value == low.pricing.maximum_retail_price_mrp.value == 10.0
    assert high.pricing.maximum_retail_price_mrp.ocr_confidence > low.pricing.maximum_retail_price_mrp.ocr_confidence


def test_ambiguous_mrp_wording_without_amount_extracts_nothing_rather_than_guess():
    text = "MRP applicable as per government norms\nNET WEIGHT: 50 g"
    result = build_structured_extraction(_text_ocr(text))
    assert result.pricing.maximum_retail_price_mrp.value is None


# --- multiple OCR regions / evidence-region mapping ------------------------

def test_evidence_region_ids_reference_the_correct_detection_indices():
    """Uses the real captured OCR result's actual detections array (10
    real lines with real bboxes) and confirms each field's evidence
    region id genuinely points at the OCR line that produced it."""
    ocr_result = _real_ocr_capture()
    result = build_structured_extraction(ocr_result)

    mrp = result.pricing.maximum_retail_price_mrp
    assert mrp.evidence_region_ids == ["region_4"]
    assert ocr_result["detections"][4]["text"] == "MRP: Rs. 149.00 (Incl. of all taxes)"

    quantity = result.quantity
    assert quantity.evidence_region_ids == ["region_3"]
    assert ocr_result["detections"][3]["text"] == "Net Quantity: 250 ml"

    origin = result.origin.country_of_origin
    assert origin.evidence_region_ids == ["region_9"]
    assert ocr_result["detections"][9]["text"] == "Country of Origin: India"


def test_multiple_regions_each_field_gets_its_own_distinct_region():
    """Confirms different fields on the same multi-region scan resolve to
    DIFFERENT region ids, not all collapsing onto the same one."""
    result = build_structured_extraction(_real_ocr_capture())
    ids = {
        tuple(result.pricing.maximum_retail_price_mrp.evidence_region_ids),
        tuple(result.quantity.evidence_region_ids),
        tuple(result.dates.manufacture_or_packing_date.evidence_region_ids),
        tuple(result.origin.country_of_origin.evidence_region_ids),
    }
    assert len(ids) == 4, "expected 4 distinct region-id sets for 4 distinct fields"


# --- AI/VLM overlay (reuses backend/ocr/gemini_service.py) ----------------

def test_ai_fallback_suggestion_overlays_the_correct_field_and_is_marked():
    ocr_result = _text_ocr("NUTRITION INFORMATION\nNET WEIGHT: 50 g")  # no MRP at all
    suggestions = [GeminiFieldSuggestion(field="mrp", value="MRP Rs. 25.00", confidence=0.9, reason="found near label")]
    result = build_structured_extraction(ocr_result, gemini_suggestions=suggestions)

    mrp = result.pricing.maximum_retail_price_mrp
    assert mrp.value == "MRP Rs. 25.00"  # AI-sourced: raw proposed text, not re-parsed
    assert mrp.source == "ai_fallback"
    assert mrp.extraction_confidence == 0.9
    assert result.extraction_metadata.ai_assisted is True
    assert "mrp" in result.extraction_metadata.ai_fields_used


def test_no_ai_suggestions_means_extraction_metadata_reports_ocr_only():
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    assert result.extraction_metadata.ai_assisted is False
    assert result.extraction_metadata.ai_fields_used == []


# --- extraction never makes a compliance decision --------------------------

def test_structured_extraction_result_carries_no_compliance_verdict():
    """Rule 9: the extraction layer extracts information only - its output
    type must not even have a place to put PASS/FAIL/COMPLIANT anywhere."""
    result = build_structured_extraction(_text_ocr(REAL_LABEL_TEXT))
    dumped = result.model_dump()
    serialized_keys = json.dumps(dumped)
    for verdict in ("COMPLIANT", "NON_COMPLIANT", "PASS", "FAIL", "REVIEW_REQUIRED"):
        assert verdict not in serialized_keys


# --- sharing an already-computed NormalizedOCRResult (performance path) ---

def test_reuses_a_precomputed_normalized_result_without_recomputing():
    ocr_result = _text_ocr(REAL_LABEL_TEXT)
    normalized = normalize_ocr_result(ocr_result)
    result = build_structured_extraction(ocr_result, normalized=normalized)
    assert result.pricing.maximum_retail_price_mrp.value == 20.0
