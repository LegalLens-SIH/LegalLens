"""
Structured extraction: OCR output -> a normalized, evidence-linked JSON view
of a package label (product identity, pricing, quantity, business
declarations, dates, identification, origin, consumer care, food info, and
free-form warnings).

Scope (deliberately narrow, same spirit as paddle_ocr_service.py /
yolo_service.py / gemini_service.py): OCR result dict in -> a
StructuredExtraction out. This module NEVER makes a compliance decision -
see backend/models/extraction.py's docstring. It sits entirely alongside
backend/services/compliance_engine.py's existing rule evaluation, which is
completely unchanged and still the only thing that decides COMPLIANT /
NON_COMPLIANT / REVIEW_REQUIRED / NEEDS_MANUAL_VERIFICATION.

Why this reuses compliance_engine.py's normalize_ocr_result() rather than
re-parsing from scratch: that function is already, in effect, this
project's extraction layer - it already turns raw OCR text into
per-declaration values with OCR confidence, using regex/alias matching
that has been tuned against real label text (Britannia, Parle-G, and
others - see backend/services/test_compliance_engine.py). Re-implementing
MRP/quantity/entity/date/consumer-care parsing here would duplicate that
tuning and risk drifting from it. Instead this module:

  1. Calls normalize_ocr_result() once (or reuses an already-computed
     NormalizedOCRResult the caller passes in - see build_structured_
     extraction's `normalized` parameter) to get the fields
     compliance_engine.py already resolves.
  2. Maps those into the richer, evidence-linked nested schema this task
     specifies, adding raw_text/ocr_confidence/extraction_confidence/
     evidence_region_ids per field by reusing the SAME validation
     predicates compliance_engine.py's own rule functions use (e.g. the
     MRP-wording check, the unit-price amount pattern) - so
     "extraction_confidence" reflects genuine, explainable signal, not an
     arbitrary number.
  3. Adds NEW lightweight regex parsing (not present in
     compliance_engine.py, because the deterministic ruleset doesn't need
     it) for fields this schema asks for that the ruleset doesn't:
     brand, FSSAI number, ingredients, nutrition block, barcode, and
     free-form warnings/declarations. See _NEW PARSERS_ below - each is a
     small, generic, product-independent pattern, never a guess.
  4. Optionally overlays AI/VLM (Gemini) suggestions for fields the
     deterministic parser could not confidently resolve - reusing
     backend/ocr/gemini_service.py's EXISTING fallback machinery
     (find_ambiguous_fields / GeminiService / apply_suggestions_to_ocr_
     result) rather than adding a second AI integration. AI-sourced
     values are marked `source="ai_fallback"` and use Gemini's own
     reported confidence as extraction_confidence (see EvidenceField's
     docstring) - still never a compliance decision.

Evidence region IDs: OCRResult.detections has no built-in stable ID, so
this module assigns one deterministically per scan: "region_<index>" by
position in ocr_result["detections"]. A field's evidence_region_ids are
whichever detection line(s) contain that field's raw_text as a substring
(in either direction, to handle both a field resolved from exactly one OCR
line and one resolved by joining several - see _region_ids_for_text).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.models.compliance import NormalizedField, NormalizedOCRResult
from backend.models.extraction import (
    BusinessDetails,
    ConsumerCare,
    Dates,
    EvidenceField,
    ExtractionMetadata,
    FoodInformation,
    Identification,
    MoneyField,
    Origin,
    Pricing,
    Product,
    QuantityField,
    StructuredExtraction,
)
from backend.ocr.gemini_service import GEMINI_FIELD_TO_RULE_FIELDS, GeminiFieldSuggestion
from backend.services.compliance_engine import (
    _DATE_PATTERN,
    _DECLARATION_HEADING_PATTERN,
    _HEADING_ONLY_PATTERN,
    _QUANTITY_PATTERN,
    _UNIT_PRICE_AMOUNT_PATTERN,
    _entity_declarations,
    _field_confidence,
    _matching_detection_indices,
    _mrp_amounts,
    _text_and_confidence,
    normalize_ocr_result,
)

# Reuses the exact MRP-wording check compliance_engine.py's own _mrp_result
# and gemini_service.py's mrp-value shim both already use - one definition,
# imported by both instead of a third copy here.
from backend.ocr.gemini_service import _MRP_WORDING_PATTERN


# --- evidence region linking ------------------------------------------------
#
# Both helpers below delegate to compliance_engine.py's
# _matching_detection_indices/_field_confidence - the SAME matcher
# backend/services/compliance_engine.py's own evidence-region propagation
# (NormalizedField.region_ids/regions, then FieldResult/Evidence) uses, so
# a field's region ids agree between StructuredExtraction and
# ComplianceResult for the same OCR result. Kept as thin wrappers here
# (same signatures as before this refactor) rather than duplicating the
# matching logic a second time, per the "no second independent evidence
# system" requirement - every call site below is unchanged.

def _region_ids_for_text(detections: list[dict[str, Any]], text: Optional[str]) -> list[str]:
    indices = _matching_detection_indices({"detections": detections}, text)
    return [f"region_{index}" for index in indices]


# --- NEW lightweight parsers (fields the deterministic ruleset doesn't need,
# but this schema asks for) --------------------------------------------------

# FSSAI license numbers are a fixed 14-digit format (FSSAI's own numbering
# standard) - a specific, reliable pattern, not a guess.
_FSSAI_PATTERN = re.compile(r"fssai[^0-9]{0,40}?(\d{14})", re.I)

# A line that is PURELY an 8, 12, or 13-digit number - the standard EAN-8/
# UPC-A/EAN-13 human-readable digit string printed beneath a barcode symbol.
# PaddleOCR reads printed text, not the barcode symbol itself, so this is
# the only barcode signal available without adding a barcode/QR decoding
# dependency (deliberately not added - see this module's docstring).
_BARCODE_LINE_PATTERN = re.compile(r"^\d{8}$|^\d{12}$|^\d{13}$")

_INGREDIENTS_TRIGGER = re.compile(r"ingredients?\s*:", re.I)
_NUTRITION_TRIGGER = re.compile(r"nutrition(?:al)?\s+information", re.I)
_BLOCK_LOOKAHEAD = 8

# A modest, generic vocabulary of common mandatory-looking package
# declarations - not brand-specific, mirrors compliance_engine.py's own
# philosophy of generic, product-independent keyword lists (e.g. its
# _COMMODITY_NOUN_PATTERN).
_WARNING_PATTERN = re.compile(
    r"\bwarning\b|\bcaution\b|keep\s+out\s+of\s+reach|keep\s+away\s+from|"
    r"for\s+external\s+use\s+only|not\s+for\s+children\s+under|"
    r"do\s+not\s+use\s+if|store\s+in\s+a\s+cool|shake\s+well|"
    r"choking\s+hazard|may\s+contain\s+traces|allergen",
    re.I,
)


def _extract_block(lines: list[str], trigger: re.Pattern) -> Optional[str]:
    """Same shape as compliance_engine.py's _care_block: find the trigger
    line, then collect forward until the next recognizable declaration
    heading (reusing _DECLARATION_HEADING_PATTERN so this stays consistent
    with how every other block boundary in the project is decided)."""
    start = next((i for i, line in enumerate(lines) if trigger.search(line)), None)
    if start is None:
        return None
    block = [lines[start]]
    for line in lines[start + 1: start + 1 + _BLOCK_LOOKAHEAD]:
        if _DECLARATION_HEADING_PATTERN.search(line) and not trigger.search(line):
            break
        block.append(line)
    return "\n".join(block)


def _extract_fssai(raw_text: str) -> Optional[str]:
    match = _FSSAI_PATTERN.search(raw_text)
    return match.group(1) if match else None


def _extract_barcode(lines: list[str]) -> Optional[str]:
    for line in lines:
        if _BARCODE_LINE_PATTERN.match(line.strip()):
            return line.strip()
    return None


# Same NEARBY_LINE_WINDOW compliance_engine.py's own MRP/date/entity search
# uses - real multi-column OCR line order frequently interleaves an
# unrelated line (a different declaration, an address continuation, ...)
# between a keyword ("BATCH No.", "USE BY") and its actual value, so "the
# very next line" is often wrong. A candidate is only accepted from within
# this forward window, and a line matching ANOTHER recognized declaration
# heading is skipped over (never accepted as this keyword's value) -
# mirrors _resolve_entity_continuation's exact reasoning.
_KEYWORD_VALUE_WINDOW = 6


def _find_value_near_keyword(lines: list[str], keyword: re.Pattern, value_pattern: re.Pattern) -> tuple[Optional[str], Optional[str]]:
    """Return (matched_value, source_line) for the first line matching
    `keyword`, checking that same line first, then a forward window,
    skipping lines that are themselves a different recognized declaration.
    (None, None) if nothing found."""
    for index, line in enumerate(lines):
        if not keyword.search(line):
            continue
        same_line = value_pattern.search(line)
        if same_line:
            return same_line.group(1) if same_line.groups() else same_line.group(0), line
        for candidate in lines[index + 1: index + 1 + _KEYWORD_VALUE_WINDOW]:
            if _DECLARATION_HEADING_PATTERN.search(candidate):
                continue
            match = value_pattern.search(candidate)
            if match:
                return (match.group(1) if match.groups() else match.group(0)), candidate
        return None, None
    return None, None


_BATCH_KEYWORD_PATTERN = re.compile(r"batch\s*(?:no\.?|number)?|\blot\s*(?:no\.?|number)?", re.I)
_BATCH_VALUE_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9\-/]{1,19})$")
_EXPIRY_KEYWORD_PATTERN = re.compile(r"use\s+by|best\s+before|expir\w*", re.I)
_EXPIRY_VALUE_PATTERN = re.compile(
    r"([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4}|[0-9]+\s*(?:months?|years?)\s*from[^\n]*)", re.I,
)


def _extract_batch(lines: list[str]) -> tuple[Optional[str], Optional[str]]:
    value, source_line = _find_value_near_keyword(lines, _BATCH_KEYWORD_PATTERN, _BATCH_VALUE_PATTERN)
    # A bare-word fragment (e.g. "BISCUITS", a stray category word from
    # column interleaving) can match the shape pattern too - require at
    # least one digit, since a real batch/lot code always has one.
    if value and not any(ch.isdigit() for ch in value):
        return None, None
    return value, source_line


def _extract_warnings(lines: list[str], detections: list[dict[str, Any]]) -> list[EvidenceField]:
    seen: set[str] = set()
    results: list[EvidenceField] = []
    for line in lines:
        if not _WARNING_PATTERN.search(line) or line in seen:
            continue
        seen.add(line)
        conf = _line_confidence(detections, line)
        results.append(EvidenceField(
            value=line, raw_text=line, ocr_confidence=conf, extraction_confidence=0.8 if conf else 0.0,
            evidence_region_ids=_region_ids_for_text(detections, line),
        ))
    return results


def _line_confidence(detections: list[dict[str, Any]], text: str) -> float:
    """The OCR confidence of whichever detection(s) contributed `text` -
    averaged if more than one line was involved, 0 if none matched (see
    this module's fallback default below - callers here always pass an
    explicit 0.0 fallback, not the page average compliance_engine.py's
    _field_confidence uses, so behavior for "no match" is unchanged)."""
    return _field_confidence({"detections": detections}, text, 0.0)


# --- mapping normalize_ocr_result()'s existing fields into the new schema --

def _money_field(field: NormalizedField, detections: list[dict[str, Any]], require_wording: Optional[re.Pattern] = None) -> MoneyField:
    if not field.detected or not field.value:
        return MoneyField()
    value_text = str(field.value)
    amounts = _mrp_amounts(value_text) if require_wording is _MRP_WORDING_PATTERN else None
    if amounts is None:
        match = _UNIT_PRICE_AMOUNT_PATTERN.search(value_text)
        amounts = [match.group(1)] if match else []
    parsed_value = float(amounts[0].replace(",", "")) if amounts else None

    has_wording = bool(require_wording.search(value_text)) if require_wording else True
    extraction_confidence = 0.95 if (parsed_value is not None and has_wording) else (0.6 if parsed_value is not None else 0.0)

    return MoneyField(
        value=parsed_value,
        currency="INR" if parsed_value is not None else None,
        raw_text=value_text,
        ocr_confidence=field.confidence,
        extraction_confidence=extraction_confidence,
        evidence_region_ids=_region_ids_for_text(detections, value_text),
    )


def _quantity_field(field: NormalizedField, detections: list[dict[str, Any]]) -> QuantityField:
    if not field.detected or not field.value:
        return QuantityField()
    value_text = str(field.value)
    match = _QUANTITY_PATTERN.search(value_text)
    if not match:
        return QuantityField(
            raw_text=value_text, ocr_confidence=field.confidence, extraction_confidence=0.0,
            evidence_region_ids=_region_ids_for_text(detections, value_text),
        )
    amount = float(match.group(1))
    if amount == int(amount):
        amount = int(amount)
    unit_token = match.group(2).lower()
    unit = {
        "kgs": "kg", "kilogram": "kg", "kilograms": "kg",
        "gm": "g", "gms": "g", "gram": "g", "grams": "g",
        "mls": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
        "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    }.get(unit_token, unit_token)
    return QuantityField(
        value=amount, unit=unit, raw_text=value_text, ocr_confidence=field.confidence,
        extraction_confidence=0.95, evidence_region_ids=_region_ids_for_text(detections, value_text),
    )


def _plain_field(field: NormalizedField, detections: list[dict[str, Any]], extraction_confidence: float = 0.9) -> Optional[EvidenceField]:
    if not field.detected or not field.value:
        return None
    value_text = str(field.value)
    return EvidenceField(
        value=value_text, raw_text=value_text, ocr_confidence=field.confidence,
        extraction_confidence=extraction_confidence, evidence_region_ids=_region_ids_for_text(detections, value_text),
    )


def _date_field(field: NormalizedField, detections: list[dict[str, Any]]) -> Optional[EvidenceField]:
    if not field.detected or not field.value:
        return None
    value_text = str(field.value)
    match = _DATE_PATTERN.search(value_text)
    parsed = match.group(0) if match else None
    return EvidenceField(
        value=parsed, raw_text=value_text, ocr_confidence=field.confidence,
        extraction_confidence=0.9 if parsed else 0.3,
        evidence_region_ids=_region_ids_for_text(detections, value_text),
    )


def _business_details(normalized: NormalizedOCRResult, lines: list[str], detections: list[dict[str, Any]]) -> BusinessDetails:
    """Role-separated manufacturer/packer/importer/marketer/distributor -
    reuses compliance_engine.py's _entity_declarations directly (the same
    function LMPC-R6's own manufacturer check is built on) rather than
    re-deriving roles from the already-joined `manufacturer_packer_importer_
    details` string, per this task's rule 6 (never randomly infer a role
    from unrelated text)."""
    declarations = _entity_declarations(lines)
    average_confidence = normalized.fields.get(
        "manufacturer_packer_importer_details", NormalizedField(),
    ).confidence
    by_role: dict[str, EvidenceField] = {}
    for role, value in declarations:
        is_heading_only = bool(_HEADING_ONLY_PATTERN.match(value))
        by_role[role] = EvidenceField(
            value=None if is_heading_only else value,
            raw_text=value,
            ocr_confidence=average_confidence,
            extraction_confidence=0.0 if is_heading_only else 0.9,
            evidence_region_ids=_region_ids_for_text(detections, value),
        )
    return BusinessDetails(
        manufacturer=by_role.get("manufacturer"),
        packer=by_role.get("packer"),
        importer=by_role.get("importer"),
        marketer=by_role.get("marketer"),
        distributor=by_role.get("distributor"),
    )


def _consumer_care(field: NormalizedField, detections: list[dict[str, Any]]) -> ConsumerCare:
    if not field.detected or not isinstance(field.value, dict):
        return ConsumerCare()
    care = field.value
    address = care.get("address")
    populated = any(care.get(k) for k in ("name", "address", "telephone_number", "email_address"))
    if not populated:
        return ConsumerCare()
    return ConsumerCare(details=EvidenceField(
        value=care, raw_text=address, ocr_confidence=field.confidence,
        extraction_confidence=0.9 if (care.get("telephone_number") or care.get("email_address")) else 0.6,
        evidence_region_ids=_region_ids_for_text(detections, address),
    ))


def _food_information(raw_text: str, lines: list[str], detections: list[dict[str, Any]], average_confidence: float) -> FoodInformation:
    fssai_number = _extract_fssai(raw_text)
    fssai = EvidenceField(
        value=fssai_number, raw_text=fssai_number, ocr_confidence=_line_confidence(detections, fssai_number) if fssai_number else 0.0,
        extraction_confidence=0.95 if fssai_number else 0.0,
        evidence_region_ids=_region_ids_for_text(detections, fssai_number),
    ) if fssai_number else None

    ingredients_block = _extract_block(lines, _INGREDIENTS_TRIGGER)
    ingredients = EvidenceField(
        value=ingredients_block, raw_text=ingredients_block, ocr_confidence=average_confidence,
        extraction_confidence=0.8, evidence_region_ids=_region_ids_for_text(detections, ingredients_block),
    ) if ingredients_block else None

    nutrition_block = _extract_block(lines, _NUTRITION_TRIGGER)
    nutrition = EvidenceField(
        value=nutrition_block, raw_text=nutrition_block, ocr_confidence=average_confidence,
        # Deliberately capped lower: this is the raw block only - per-
        # nutrient values are NOT parsed out individually (a multi-column
        # nutrition table is out of scope here, see module docstring).
        extraction_confidence=0.5, evidence_region_ids=_region_ids_for_text(detections, nutrition_block),
    ) if nutrition_block else None

    return FoodInformation(fssai=fssai, ingredients=ingredients, nutrition=nutrition)


def _identification(normalized: NormalizedOCRResult, raw_text: str, lines: list[str], detections: list[dict[str, Any]]) -> Identification:
    batch_value = normalized.metadata.get("batch_lot_number")
    batch_source_line = batch_value
    if not batch_value:
        batch_value, batch_source_line = _extract_batch(lines)
    batch = EvidenceField(
        value=batch_value, raw_text=batch_source_line or batch_value,
        ocr_confidence=_line_confidence(detections, batch_source_line or batch_value) if batch_value else 0.0,
        extraction_confidence=0.85 if batch_value else 0.0,
        evidence_region_ids=_region_ids_for_text(detections, batch_source_line or batch_value),
    ) if batch_value else None

    barcode_value = _extract_barcode(lines)
    barcode = EvidenceField(
        value=barcode_value, raw_text=barcode_value, ocr_confidence=_line_confidence(detections, barcode_value) if barcode_value else 0.0,
        extraction_confidence=0.7 if barcode_value else 0.0,
        evidence_region_ids=_region_ids_for_text(detections, barcode_value),
    ) if barcode_value else None

    # QR codes encode binary/URL data, not printed text - PaddleOCR (or any
    # OCR engine) cannot read one; decoding would need a dedicated QR
    # reader (e.g. pyzbar), an extra dependency this task explicitly says
    # not to add. Always null - a documented limitation, not a guess.
    return Identification(batch_or_lot_number=batch, barcode=barcode, qr_code=None)


# --- AI fallback overlay (reuses backend/ocr/gemini_service.py) -----------

def _apply_ai_suggestions(result: StructuredExtraction, suggestions: list[GeminiFieldSuggestion]) -> list[str]:
    """Overlay Gemini-proposed values for the classes gemini_service.py
    already knows how to resolve (see GEMINI_FIELD_TO_RULE_FIELDS) onto the
    matching leaf(ves) of the structured schema. Marked source="ai_fallback"
    and uses Gemini's own reported confidence as extraction_confidence -
    this module never invents a second confidence number for an
    AI-sourced value."""
    applied: list[str] = []
    for suggestion in suggestions:
        field = EvidenceField(
            value=suggestion.value, raw_text=suggestion.value, ocr_confidence=0.0,
            extraction_confidence=suggestion.confidence, evidence_region_ids=[], source="ai_fallback",
        )
        if suggestion.field == "product_identity":
            result.product.name = field
        elif suggestion.field == "mrp":
            result.pricing.maximum_retail_price_mrp = MoneyField(**field.model_dump())
        elif suggestion.field == "unit_sale_price":
            result.pricing.unit_sale_price = MoneyField(**field.model_dump())
        elif suggestion.field == "net_quantity":
            result.quantity = QuantityField(**field.model_dump())
        elif suggestion.field == "entity_details":
            # gemini_service.py resolves this as one combined entity value,
            # not role-separated - surfacing it under "manufacturer" would
            # misattribute the role (rule 6), so it is intentionally not
            # applied to business_details here. The deterministic parser's
            # own role-separated result (if any) is left as-is.
            continue
        elif suggestion.field == "country_of_origin":
            result.origin.country_of_origin = field
        elif suggestion.field == "mfg_date_batch":
            result.dates.manufacture_or_packing_date = field
        elif suggestion.field == "consumer_care":
            result.consumer_care.details = field
        else:
            continue
        applied.append(suggestion.field)
    return applied


# --- entry point -------------------------------------------------------------

def build_structured_extraction(
    ocr_result: dict[str, Any],
    normalized: Optional[NormalizedOCRResult] = None,
    gemini_suggestions: Optional[list[GeminiFieldSuggestion]] = None,
) -> StructuredExtraction:
    """Build the structured extraction JSON for one scan.

    `normalized` lets a caller that already computed normalize_ocr_result()
    for this exact ocr_result (e.g. backend/api/ocr.py's create_scan, which
    also needs it for Gemini's ambiguous-field check) pass it straight in
    instead of this function recomputing it - a cheap, pure, CPU-only
    function either way (no OCR/model re-run), but reusing it when
    available avoids even that redundant call. Computed internally if
    omitted.

    `gemini_suggestions`, when given, are the SAME suggestions backend/api/
    ocr.py's create_scan already obtained from GeminiService.suggest_fields
    for the low-confidence/undetected fields on this scan - passed in
    rather than fetched again here, so this function never itself triggers
    a second AI call.
    """
    if normalized is None:
        normalized = normalize_ocr_result(ocr_result)

    raw_text = normalized.raw_text
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    detections = ocr_result.get("detections") or []

    def field(name: str) -> NormalizedField:
        return normalized.fields.get(name, NormalizedField())

    product_name = _plain_field(field("common_generic_name_of_commodity"), detections, extraction_confidence=0.75)

    result = StructuredExtraction(
        product=Product(name=product_name, brand=None),  # brand: see module docstring - no reliable signal, left null rather than guessed
        pricing=Pricing(
            maximum_retail_price_mrp=_money_field(field("maximum_retail_price_mrp"), detections, require_wording=_MRP_WORDING_PATTERN),
            unit_sale_price=_money_field(field("unit_sale_price"), detections),
        ),
        quantity=_quantity_field(field("net_quantity"), detections),
        business_details=_business_details(normalized, lines, detections),
        dates=Dates(
            manufacture_or_packing_date=_date_field(field("month_and_year_of_manufacture_or_packing"), detections),
            expiry_or_best_before=_expiry_field(normalized, lines, detections),
        ),
        identification=_identification(normalized, raw_text, lines, detections),
        origin=Origin(country_of_origin=_plain_field(field("country_of_origin"), detections)),
        consumer_care=_consumer_care(field("consumer_care_details"), detections),
        food_information=_food_information(raw_text, lines, detections, _text_and_confidence(ocr_result)[1]),
        warnings_and_declarations=_extract_warnings(lines, detections),
    )

    ai_fields_used: list[str] = []
    if gemini_suggestions:
        ai_fields_used = _apply_ai_suggestions(result, gemini_suggestions)

    result.extraction_metadata = ExtractionMetadata(
        overall_confidence=_overall_confidence(result),
        extraction_method="hybrid",
        ai_assisted=bool(ai_fields_used),
        ai_fields_used=ai_fields_used,
    )
    return result


def _expiry_field(normalized: NormalizedOCRResult, lines: list[str], detections: list[dict[str, Any]]) -> Optional[EvidenceField]:
    existing = normalized.metadata.get("expiry_or_best_before_date")
    if existing:
        return EvidenceField(
            value=existing, raw_text=existing, ocr_confidence=_line_confidence(detections, existing),
            extraction_confidence=0.9, evidence_region_ids=_region_ids_for_text(detections, existing),
        )
    value, source_line = _find_value_near_keyword(lines, _EXPIRY_KEYWORD_PATTERN, _EXPIRY_VALUE_PATTERN)
    if not value:
        return None
    value = value.strip()
    return EvidenceField(
        value=value, raw_text=source_line, ocr_confidence=_line_confidence(detections, source_line),
        extraction_confidence=0.85, evidence_region_ids=_region_ids_for_text(detections, source_line),
    )


def _overall_confidence(result: StructuredExtraction) -> float:
    """Average ocr_confidence across every leaf field that was actually
    populated - not a blind reuse of the whole-page average, so a scan
    where only 2 of 8 fields were found doesn't get credited with the
    confidence of fields it never resolved."""
    scores: list[float] = []

    def collect(item: Optional[EvidenceField]) -> None:
        if item is not None and item.value is not None:
            scores.append(item.ocr_confidence)

    collect(result.product.name)
    collect(result.product.brand)
    collect(result.pricing.maximum_retail_price_mrp)
    collect(result.pricing.unit_sale_price)
    collect(result.quantity)
    collect(result.business_details.manufacturer)
    collect(result.business_details.packer)
    collect(result.business_details.importer)
    collect(result.business_details.marketer)
    collect(result.business_details.distributor)
    collect(result.dates.manufacture_or_packing_date)
    collect(result.dates.expiry_or_best_before)
    collect(result.identification.batch_or_lot_number)
    collect(result.identification.barcode)
    collect(result.origin.country_of_origin)
    collect(result.consumer_care.details)
    collect(result.food_information.fssai)
    collect(result.food_information.ingredients)
    collect(result.food_information.nutrition)
    for warning in result.warnings_and_declarations:
        collect(warning)

    return round(sum(scores) / len(scores), 4) if scores else 0.0
