from __future__ import annotations

import json
import re
import uuid
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from backend.models.compliance import ComplianceResult, ComplianceSummary, Evidence, FieldResult, NormalizedField, NormalizedOCRResult, RuleResult

RULESET_PATH = Path(__file__).resolve().parents[1] / "rules" / "legal_metrology_rules_2011.json"
ENGINE_VERSION = "1.0.0"
CONFIDENCE_THRESHOLD = 0.80
ALIASES = {
    # "rice"/"sugar"/"flour" were removed here (previously matched
    # ingredient-list mentions, e.g. "Sugar, Edible Vegetable Oil (Palm),",
    # instead of the actual product name - see the positional fallback
    # below and training/CLASS_TAXONOMY.md-adjacent Phase 6 investigation).
    #
    # manufacturer_packer_importer_details / name_and_address_of_
    # manufacturer_or_packer are NOT listed here (or in REGEX_ALIASES
    # below) - see the dedicated "Manufacturer / Packer / Importer /
    # Marketer / Distributor" section further down, which owns both fields
    # completely (detection, multi-role separation, entity-value
    # resolution) as a single cohesive unit rather than being split across
    # this generic substring loop, a regex fallback, and a later patch
    # block, as it previously was.
    #
    # Bare "origin" deliberately excluded here - as a plain substring it
    # matches inside unrelated words (e.g. "Original Gluco Biscuits"). A
    # word-boundaried version lives in REGEX_ALIASES below instead.
    "country_of_origin": ["country of origin", "made in"],
    "common_generic_name_of_commodity": ["product name", "commodity", "generic name"],
    "net_quantity": ["net quantity", "net qty", "net weight", "net wt"],
    "month_and_year_of_manufacture_or_packing": ["date of manufacture", "manufactured", "mfg", "packed on", "packing date"],
    "maximum_retail_price_mrp": ["maximum retail price", "mrp", "m.r.p", "rs.", "rs ", "₹"],
    "unit_sale_price": ["unit sale price", "price per", "/kg", "/ g", "/g", "/litre", "/l"],
    "consumer_care_details": ["consumer care", "customer care", "helpline", "toll free"],
    "identity_of_commodity": ["product name", "commodity", "generic name"],
    "total_number_of_retail_packages_or_net_quantity": ["net quantity", "number of packages", "retail packages"],
}

# Fallback regex aliases, checked only when a field's plain ALIASES
# substring check above found nothing.
REGEX_ALIASES: dict[str, list[re.Pattern]] = {
    # Word-boundaried "origin" - the ALIASES substring check above only
    # covers "country of origin"/"made in"; a bare "origin" can't live there
    # because a plain substring check matches inside unrelated words (e.g.
    # "Original Gluco Biscuits" contains "origin" as a substring of
    # "Original"). \b ensures "origin" must be its own word - it does not
    # match inside "Original" (immediately followed by "al", not a word
    # boundary) - while still accepting standalone "Origin: India" style
    # declarations per Legal Metrology labeling conventions.
    "country_of_origin": [
        re.compile(r"\borigin\b", re.I),
    ],
    # The plain ALIASES entry above only covers SLASH-based per-unit
    # notation ("/kg", "/g", "/litre", "/l") and the literal phrase
    # "price per" - it does not recognize the equally common SPACE-
    # separated wording ("₹0.38 per g", "Rs 0.38 per g"), so a genuine
    # declaration in that format was never even detected. Requires a digit
    # immediately before "per <unit>" (not just a bare "per g"/"per l"
    # substring) so this doesn't false-trigger on unrelated text that
    # happens to contain those letters (e.g. "paper garment", "paper lid").
    "unit_sale_price": [
        re.compile(r"[0-9](?:\.[0-9]+)?\s*per\s+(?:100\s*g|100\s*ml|kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b", re.I),
    ],
}

# Shared window (in OCR lines) used when a value is expected NEAR a matched
# keyword line rather than strictly ON it - PaddleOCR's line order
# interleaves real multi-column label layouts (price column beside address
# column, date beside batch number, etc.), so "the very next line" is
# frequently the wrong adjacent column, not the value that keyword actually
# labels. See Phase 6 investigation for the concrete real-image evidence.
NEARBY_LINE_WINDOW = 6
# Deliberately NOT widened alongside CONSUMER_CARE_CONTACT_WINDOW below:
# this bounds the readable "address" text block specifically, and a wider
# value here starts pulling in unrelated later content (dates, other
# declarations) as noise into the displayed address. The actual fix for
# "phone/email are further away than this" is CONSUMER_CARE_CONTACT_WINDOW,
# which searches for the phone/email PATTERNS independently of this
# shorter descriptive-text window - see _care_contact.
CONSUMER_CARE_LOOKAHEAD = 3
CONSUMER_CARE_CONTACT_WINDOW = 12

# A bare, undecorated number that plausibly reads as a price on its own
# line (e.g. "20.00") once separated from its "MRP"/currency context by
# column interleaving - deliberately requires a decimal component so a
# bare 4-digit number (a year, a PIN code) doesn't false-positive; real MRP
# amounts are conventionally printed with paise (".00", ".50", etc.).
_BARE_AMOUNT_PATTERN = re.compile(r"^[0-9]{1,4}[.,][0-9]{1,2}$")

# A month-name-or-numeric date, used to disambiguate a genuine
# manufacture/packing DATE line from an unrelated line that merely contains
# the bare word "manufactured" (e.g. an entity/address header).
_DATE_PATTERN = re.compile(
    r"\b(?:[0-3]?[0-9][/\-.][0-1]?[0-9][/\-.][0-9]{2,4}"
    r"|[0-1]?[0-9][/\-.][0-9]{4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+[0-9]{2,4})\b",
    re.I,
)
# Keywords considered when searching for the manufacture/packing DATE line
# specifically (separate from ALIASES' broader, unvalidated match) - "pkd"
# is a common Indian packaged-goods abbreviation for "packed [on]", not
# text specific to any one product.
_DATE_KEYWORD_PATTERN = re.compile(
    r"date\s+of\s+manufacture|manufactur\w*|\bmfg\b|packed?\s+on|packing\s+date|\bpkd\b", re.I,
)

# Maps a YOLO26 declaration-region class (backend/ocr/yolo_service.py's
# multi_region mode, see training/CLASS_TAXONOMY.md) to the rule-field
# name(s) it satisfies. A class maps to more than one field where the
# retail (LMPC-R6) and wholesale (LMPC-R24) rules use different field names
# for what is visually the same declaration - matching how ALIASES above
# already conflates them (see training/CLASS_TAXONOMY.md's class 0/3/4
# rationale for why these were merged into one YOLO class in the first
# place). "mrp" and "consumer_care" are intentionally absent here - they
# need extra parsing beyond a plain text value (an amount/wording check, a
# name/address/phone/email split respectively) and are handled specially in
# `_apply_regions` instead of through this generic mapping.
YOLO_CLASS_TO_FIELDS: dict[str, list[str]] = {
    "product_identity": ["common_generic_name_of_commodity", "identity_of_commodity"],
    "net_quantity": ["net_quantity", "total_number_of_retail_packages_or_net_quantity"],
    "entity_details": ["manufacturer_packer_importer_details", "name_and_address_of_manufacturer_or_packer"],
    "country_of_origin": ["country_of_origin"],
    "mfg_date_batch": ["month_and_year_of_manufacture_or_packing"],
    "unit_sale_price": ["unit_sale_price"],
}

_BATCH_PATTERN = re.compile(r"batch\s*(?:no\.?|number)?\s*[:\-]?\s*([A-Za-z0-9\-/]{2,20})", re.I)
_EXPIRY_PATTERN = re.compile(
    r"(?:use\s+by|best\s+before|expir\w*)\s*[:\-]?\s*"
    r"([0-9]{1,2}[/\-][0-9]{1,2}[/\-][0-9]{2,4}|[0-9]+\s*(?:months?|years?)\s*from[^\n]*)",
    re.I,
)


def load_ruleset(path: Path = RULESET_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _text_and_confidence(ocr_result: dict[str, Any]) -> tuple[str, float]:
    text = str(ocr_result.get("full_text", ""))
    detections = ocr_result.get("detections") or []
    confidence = sum(float(item.get("confidence", 0)) for item in detections) / len(detections) if detections else 0
    return text, confidence


def _mrp_amounts(value: str) -> list[str]:
    currency_amounts = re.findall(r"(?:₹|Rs\.?|INR)\s*([0-9]+(?:[.,][0-9]{1,2})?)", value, re.I)
    if currency_amounts:
        return currency_amounts

    label_amount = re.search(
        r"(?:maximum\s+retail\s+price|m\.?r\.?p\.?)\s*[:\-]?\s*([0-9]+(?:[.,][0-9]{1,2})?)",
        value,
        re.I,
    )
    return [label_amount.group(1)] if label_amount else []


# Parses a net-quantity string (e.g. "Net Weight: 75 g", "1 kg", "500 ml")
# into (amount normalized to grams-or-millilitres, basis). Used to determine
# whether the Legal Metrology (Packaged Commodities) Amendment Rules, 2022
# proviso applies to a package's unit-sale-price requirement (see
# _unit_sale_price_result below) - deliberately a pure unit-conversion
# parser, not tied to any specific product's wording.
_QUANTITY_PATTERN = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*"
    r"(kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b",
    re.I,
)
_WEIGHT_UNITS = {"kg": 1000.0, "kgs": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0, "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0}
_VOLUME_UNITS = {"l": 1000.0, "litre": 1000.0, "litres": 1000.0, "liter": 1000.0, "liters": 1000.0, "ml": 1.0, "mls": 1.0, "millilitre": 1.0, "millilitres": 1.0, "milliliter": 1.0, "milliliters": 1.0}


def _parse_net_quantity(value: str) -> tuple[float, str] | None:
    """Return (amount, basis) with amount normalized to grams ("weight") or
    millilitres ("volume"), or None if no recognizable quantity+unit is
    found - callers must treat that as "cannot determine", never as zero."""
    match = _QUANTITY_PATTERN.search(value or "")
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit in _WEIGHT_UNITS:
        return amount * _WEIGHT_UNITS[unit], "weight"
    if unit in _VOLUME_UNITS:
        return amount * _VOLUME_UNITS[unit], "volume"
    return None


# A nutrition-table ROW LABEL (e.g. "Protein" immediately followed by its
# own value line "6.6 g") - used to reject a quantity-shaped candidate line
# sitting near the net-quantity keyword from being mistaken for the actual
# declared net quantity. Real nutrition tables print several such per-100g/
# per-serving gram values (protein, carbohydrate, fat, sugars, ...), any of
# which can end up within the nearby-line search window once a multi-column
# layout interleaves them with the genuine "Net Weight" declaration - a
# generic, product-independent row-label vocabulary, not tied to any one
# product's specific nutrition numbers.
_NUTRITION_ROW_LABEL_PATTERN = re.compile(
    r"\bprotein\b|\bcarbohydrate|\bsugars?\b|\bsodium\b|\bcholesterol\b|"
    r"\bvitamin\b|\bcalcium\b|\biron\b|\bfibre\b|\bfiber\b|saturated\s+fat|trans\s+fat|\bfat\b",
    re.I,
)


def _in_nutrition_table_context(lines: list[str], index: int, radius: int = 1) -> bool:
    """True if a nutrition-table row label sits immediately adjacent to
    `lines[index]` - the shape real labels use (label on one line, its gram
    value on the very next), so a value found here is almost certainly a
    nutrition fact, not the package's declared net quantity."""
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return any(_NUTRITION_ROW_LABEL_PATTERN.search(lines[i]) for i in range(start, end) if i != index)


# Matches an explicit per-unit price declaration - an amount followed by
# "/<unit>" or "per <unit>" (e.g. "Rs 90/kg", "33.00/100g", "Rs. 5 per
# litre"). Deliberately requires the per-unit suffix, unlike _mrp_amounts,
# since a bare amount alone can't distinguish a unit sale price from any
# other number on the label.
_UNIT_PRICE_AMOUNT_PATTERN = re.compile(
    r"(?:₹|Rs\.?|INR)?\s*([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:/|per\s+)\s*"
    r"(?:100\s*g|100\s*ml|kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b",
    re.I,
)

# --- Manufacturer / Packer / Importer / Marketer / Distributor -----------
#
# Legal basis: Legal Metrology (Packaged Commodities) Rules, 2011, Rule 6 -
# every retail package must declare the manufacturer's name/address, and
# (where different) the packer's and/or importer's. This ruleset's own
# required_fields already model this as ONE combined field per rule
# (manufacturer_packer_importer_details for R6, name_and_address_of_
# manufacturer_or_packer for R24) - matching how real labels usually print
# it (often the same company in more than one role, or one panel listing
# every applicable role together) - not a per-role redesign of the
# compliance schema, per this task's explicit "keep compatibility" scope.
#
# "Marketed by"/"Distributed by" are NOT independently required by this
# ruleset (not listed in required_fields, and this project does not invent
# legal requirements beyond the ruleset) - but real labels very commonly
# print them alongside, or occasionally in place of, the manufacturer/
# packer/importer text (e.g. "Manufactured by X, Marketed by Y"). They are
# recognized here as entity-declaration WORDING for extraction purposes -
# capturing that text is strictly better than missing it - while the role
# is tracked separately (see metadata["entity_roles_detected"] below) so a
# marketer/distributor mention is never silently presented as if it were
# confirmed manufacturer/packer/importer evidence without that distinction
# being recoverable. If a future amendment or ruleset update independently
# requires a marketer/distributor declaration, this is where that would
# plug in - not guessed here.
#
# A: recognized declaration WORDING = a role stem (what the declaration is
#    ABOUT) immediately followed by "by"/"for" (0-2 connector tokens
#    allowed in between, for phrasing like "& Marketed By").
# B: conservative, EXPLICIT OCR-variant tolerance per stem - not a general
#    fuzzy matcher (which risks false positives elsewhere) - each is a
#    specific, documented character confusion:
#      manufactur -> manufactijr  (U <-> IJ)
#      packed     -> packeo       (D <-> O)
#      imported   -> importeo     (D <-> O)
#      by         -> 8y           (B <-> 8)
#    plus the short forms genuinely used on Indian labels (MFD/MFG/MFR,
#    PKD, IMP).
# C: the actual entity/company/address text is deliberately NOT part of
#    these patterns - it is resolved separately from nearby OCR lines by
#    _resolve_entity_continuation, never assumed to be on the heading line.
_BY_OR_FOR = r"(?:by|8y|for)"
_BY_ONLY = r"(?:by|8y)"
_CONNECTOR = r"(?:\s+\S+){0,2}"

_ROLE_HEADING_PATTERNS: dict[str, re.Pattern] = {
    "manufacturer": re.compile(rf"\bmanufact(?:u|ij)r\w*{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bmf[dgr]{_CONNECTOR}\s+{_BY_OR_FOR}\b", re.I),
    # "pkd" is also the common short form for a PACKING DATE stamp ("PKD.
    # 15/05/2024") - that usage never has a trailing by/for, so it never
    # matches this pattern; only "PKD BY <entity>" does.
    "packer": re.compile(rf"\bpack(?:ed|eo|er)?{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bpkd{_CONNECTOR}\s+{_BY_OR_FOR}\b", re.I),
    "importer": re.compile(rf"\bimport(?:ed|eo|er)?{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bimp{_CONNECTOR}\s+{_BY_OR_FOR}\b", re.I),
    "marketer": re.compile(rf"\bmarket(?:ed|er)?{_CONNECTOR}\s+{_BY_ONLY}\b", re.I),
    "distributor": re.compile(rf"\bdistribut(?:ed|or)?{_CONNECTOR}\s+{_BY_ONLY}\b", re.I),
}

# True only when a line is ENTIRELY one declaration heading and nothing
# else (mirrors _ROLE_HEADING_PATTERNS' stems/connectors, anchored ^...$
# with an optional trailing colon/dash) - i.e. the entity's actual name/
# address is not on this line at all. Triggers the nearby-line search in
# _resolve_entity_continuation, so the heading itself never becomes the
# extracted value on its own.
_HEADING_ONLY_PATTERN = re.compile(
    rf"^(?:manufact(?:u|ij)r\w*|mf[dgr]){_CONNECTOR}\s+{_BY_OR_FOR}\s*[:\-]?\s*$|"
    rf"^(?:pack(?:ed|eo|er)?|pkd){_CONNECTOR}\s+{_BY_OR_FOR}\s*[:\-]?\s*$|"
    rf"^(?:import(?:ed|eo|er)?|imp){_CONNECTOR}\s+{_BY_OR_FOR}\s*[:\-]?\s*$|"
    rf"^market(?:ed|er)?{_CONNECTOR}\s+{_BY_ONLY}\s*[:\-]?\s*$|"
    rf"^distribut(?:ed|or)?{_CONNECTOR}\s+{_BY_ONLY}\s*[:\-]?\s*$",
    re.I,
)

# Recognizes lines that are OTHER Legal Metrology declarations/headings
# (any of them, including a DIFFERENT manufacturer-side role - e.g. this is
# what keeps "Manufactured by A" / "Packed by B" from having B's line
# absorbed into A's continuation search). Shared by the product-identity
# positional fallback (a candidate line matching this is never accepted as
# the commodity name) and the entity-continuation search below (never
# accepted as "the rest of the entity name/address" either) - both need the
# exact same "is this actually a different declaration" check. Extended
# with the full boundary list this task specifies (net quantity, unit sale
# price, batch/lot, mfg/packing date, expiry/use by/best before, country of
# origin, barcode/GTIN, storage/recycling/warning text) beyond what was
# needed before - a real label can interleave any of these between an
# entity heading and its own continuation.
_DECLARATION_HEADING_PATTERN = re.compile(
    r"nutrition(?:al)?\s+information|ingredients?\s*:|"
    r"net\s+(?:quantity|qty|weight|wt)\b|"
    r"unit\s+sale\s+price|price\s+per\s+unit|" +
    rf"manufact(?:u|ij)r\w*{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bmf[dgr]{_CONNECTOR}\s+{_BY_OR_FOR}\b|" +
    rf"pack(?:ed|eo|er)?{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bpkd{_CONNECTOR}\s+{_BY_OR_FOR}\b|" +
    rf"import(?:ed|eo|er)?{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bimp{_CONNECTOR}\s+{_BY_OR_FOR}\b|" +
    rf"market(?:ed|er)?{_CONNECTOR}\s+{_BY_ONLY}\b|distribut(?:ed|or)?{_CONNECTOR}\s+{_BY_ONLY}\b|" +
    r"batch\s*(?:no\.?|number)?\s*[:\-]|\blot\s*(?:no\.?|number)?\s*[:\-]|"
    r"date\s+of\s+manufactur\w*|packing\s+date|packed?\s+on|\bmfg\b|\bpkd\b|"
    r"(?:use\s+by|best\s+before|expir\w*)\b|"
    r"(?:consumer|customer)\s+care|customer\s+service|helpline|toll\s*free|"
    r"country\s+of\s+origin|\borigin\b|"
    r"maximum\s+retail\s+price|\bm\.?r\.?p\.?\b|"
    r"(?:₹|Rs\.?|INR)\s*[0-9]|"
    r"\bgtin\b|\bbarcode\b|"
    r"storage\s+condition|store\s+in\s+a|keep\s+(?:in|away|refrigerat)|"
    r"keep\s+your\s+\w+\s+clean|dispos",
    re.I,
)
# A bare number, with or without a trailing "/-" (a price/quantity
# fragment, e.g. Parle-G's "10/-" price badge or a bare "8"), never a
# product description on its own.
_BARE_NUMBER_OR_PRICE_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?\s*/?-?$")

# A modest, generic vocabulary of Legal-Metrology-relevant packaged-
# commodity CATEGORY nouns (not any specific brand) - used only to PREFER a
# candidate line that plausibly names what the product actually is (e.g.
# "Potato Chips", "Original Gluco Biscuits") over an earlier candidate that
# merely isn't a rejected heading/price (e.g. a bare brand name). See
# _is_short_description_shaped below for why this doesn't resurrect the
# original ingredient-list mismatch bug (matching "oil" inside "Refined
# Wheat Flour...Vegetable Oil (Palm),") this project already fixed once.
#
# Deliberately EXCLUDES words that commonly double as FLAVOR descriptors on
# real packaging (e.g. "cream" in "Cream & Onion", "masala" in "Magic
# Masala", "sauce"/"jam"/"honey"/"spice" in flavor names) - including them
# caused this exact heuristic to misfire on a real snack-flavor line during
# testing. Kept to nouns that name the commodity CATEGORY itself, which are
# far less likely to appear as a flavor modifier.
_COMMODITY_NOUN_PATTERN = re.compile(
    r"\b(?:chips?|biscuits?|cookies?|wafers?|namkeen|snacks?|noodles?|pasta|"
    r"chocolates?|candi?(?:es|y)|toffees?|sweets?|cakes?|bread|rusks?|"
    r"oils?|ghee|butter|cheese|milk|curd|yogh?urt|paneer|"
    r"rice|atta|flour|sugar|salt|tea|coffee|juice|beverages?|"
    r"soaps?|shampoos?|detergents?|toothpaste|lotions?)\b",
    re.I,
)


def _is_identity_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _DECLARATION_HEADING_PATTERN.search(stripped):
        return False
    if _BARE_NUMBER_OR_PRICE_PATTERN.match(stripped):
        return False
    return True


def _is_short_description_shaped(line: str) -> bool:
    """A genuine commodity-description line ("Potato Chips", "Original
    Gluco Biscuits") is short and comma-free; ingredient-list CONTENT
    ("Sugar, Edible Vegetable Oil (Palm),") is longer, comma-separated, and
    often carries parenthetical annotations - even though both may happen
    to contain the same generic noun (e.g. "oil"). This shape check is what
    keeps _COMMODITY_NOUN_PATTERN from resurrecting the original
    ingredient-list mismatch bug."""
    stripped = line.strip()
    return "," not in stripped and len(stripped) <= 40


_CARE_TRIGGER_PATTERN = re.compile(
    r"(?:consumer|customer)\s+care|customer\s+service|helpline|toll\s*free|contact\s+us", re.I,
)

# Matches a recognizable "name of the office/person to be contacted" per
# LMPC-R6's consumer-care requirement - e.g. "Consumer Care Cell", "Customer
# Care Department", "Helpline", "Toll Free". This is genuinely printed on
# most real labels (the OCR text already contains it), but the OLD code
# never captured it into the structured `name` field at all - it was
# hardcoded to None everywhere consumer_care_details was built. Since the
# ruleset's completeness check requires "name" to be present, a permanently
# blank name made every real scan register as incomplete/PARTIAL regardless
# of how much of the declaration was actually printed. Deliberately scoped
# to the CONTACT OFFICE's designation, not the manufacturer's own company
# name (that's a separate, already-existing field) - so this does not
# conflate two legally distinct declarations.
_CARE_NAME_PATTERN = re.compile(
    r"(?:consumer|customer)\s+care(?:\s+(?:cell|department|desk|division|team|centre|center))?"
    r"|customer\s+service(?:\s+(?:cell|department|desk|centre|center))?"
    r"|toll[\s-]?free(?:\s+(?:number|helpline))?"
    r"|helpline",
    re.I,
)


def _find_care_start(lines: list[str]) -> int | None:
    return next((index for index, line in enumerate(lines) if _CARE_TRIGGER_PATTERN.search(line)), None)


def _care_name(trigger_line: str) -> str | None:
    match = _CARE_NAME_PATTERN.search(trigger_line)
    if not match:
        return None
    return " ".join(word.capitalize() for word in match.group(0).split())


# A single bare alphanumeric token with no comma - a batch/license/barcode
# fragment (e.g. "A05124C1") or a stray category/brand word interleaved
# from a different column (e.g. "BISCUITS") - matches this shape. Real
# Indian addresses are conventionally printed comma-separated ("Company
# Ltd., Street, Area, City - PIN, State"), so a genuine address line is
# very rarely a single bare token with no punctuation at all. This is a
# SHAPE-based heuristic (not a hardcoded word list), so it generalizes
# across products instead of naming specific noise strings.
_BARE_FRAGMENT_PATTERN = re.compile(r"^[A-Za-z0-9]{2,20}$")


def _looks_like_unrelated_fragment(line: str) -> bool:
    return bool(_BARE_FRAGMENT_PATTERN.match(line.strip()))


# Cap on how many CONSECUTIVE lines _resolve_entity_continuation will join
# into one entity's value - real multi-line company+address blocks are
# rarely longer than this in practice, and it bounds the search from
# absorbing unrelated content indefinitely even when a trailing comma keeps
# inviting "one more line" (see that function's docstring).
_ENTITY_CONTINUATION_LINE_CAP = 4


def _resolve_entity_continuation(lines: list[str], start: int) -> str:
    """Collect the entity's actual name/address following a heading-only
    line at `start` (see _HEADING_ONLY_PATTERN) - may span several OCR
    lines (a real, required scenario: company name, then street, then
    city/state/pincode), starting once genuine content is found.

    Two distinct behaviors, matching two distinct real situations:
    - BEFORE any genuine content has been found: a boundary/fragment line
      is SKIPPED, not a stop signal - real labels interleave OTHER
      declarations' headings (e.g. "NUTRITION INFORMATION", "INGREDIENTS:")
      between an entity heading and its own continuation once multi-column
      layout is accounted for (confirmed on a real Parle-G label: both
      headings sit between "MANUFACTURED FOR" and the genuine
      "PARLE BISCUITS PVT. LTD." that follows them).
    - AFTER genuine content has started: a boundary/fragment line STOPS the
      collection - we have left this entity's block. Within genuine
      content, a line is only extended to the next one if it ends with a
      trailing comma (the conventional Indian address-printing pattern -
      "Company Ltd., Street, Area,", confirmed on every real address block
      traced in this project so far) - a line NOT ending in a comma is
      accepted as the natural end of the address, not a signal to keep
      searching further.
    """
    collected: list[str] = []
    for line in lines[start + 1: start + 1 + NEARBY_LINE_WINDOW]:
        stripped = line.strip()
        is_noise = (
            len(stripped) <= 3
            or _looks_like_unrelated_fragment(stripped)
            or _BARE_NUMBER_OR_PRICE_PATTERN.match(stripped)
            or _DECLARATION_HEADING_PATTERN.search(stripped)
        )
        if is_noise:
            if collected:
                break
            continue
        collected.append(stripped)
        if len(collected) >= _ENTITY_CONTINUATION_LINE_CAP or not stripped.endswith(","):
            break
    return " ".join(collected)


def _entity_declarations(lines: list[str]) -> list[tuple[str, str]]:
    """Find the FIRST declaration line for EACH distinct role (manufacturer,
    packer, importer, marketer, distributor) - not just the first role
    found overall - so multiple roles declared on the same package (a
    normal, legally unremarkable pattern - e.g. "Manufactured by A" /
    "Packed by B" / "Imported by C") are each captured as independent
    evidence instead of collapsing into one, possibly-wrong, merged entity.
    For each role found, resolves its actual entity/company/address text
    when the heading line alone doesn't already carry it (see
    _resolve_entity_continuation) - the heading itself is never the final
    value on its own when real content can be found nearby.

    Returns [(role, value), ...] ordered by where each role's heading
    appears in the OCR text (not a fixed role priority order).

    A single line can match more than one role pattern independently - e.g.
    "MANUFACTURED & MARKETED BY:" contains "MARKETED BY" as a literal
    substring, so it matches BOTH the manufacturer pattern (as its intended
    declaration) and the marketer pattern (coincidentally, since "marketed"
    is just a connector word there, not a standalone declaration). Once a
    line index has been claimed by an earlier-checked role (dict iteration
    order: manufacturer, packer, importer, marketer, distributor), it is
    skipped for every later role - the line is ONE declaration, not two.
    """
    found: list[tuple[int, str, str]] = []
    claimed_indices: set[int] = set()
    for role, pattern in _ROLE_HEADING_PATTERNS.items():
        index = next((i for i, line in enumerate(lines) if i not in claimed_indices and pattern.search(line)), None)
        if index is None:
            continue
        claimed_indices.add(index)
        heading_line = lines[index].strip()
        if _HEADING_ONLY_PATTERN.match(heading_line):
            continuation = _resolve_entity_continuation(lines, index)
            value = f"{heading_line} {continuation}".strip() if continuation else heading_line
        else:
            value = heading_line
        found.append((index, role, value))
    found.sort(key=lambda item: item[0])
    return [(role, value) for _index, role, value in found]


def _care_block(lines: list[str], start: int) -> str:
    """The descriptive address/name text block starting at `start` - kept
    short (CONSUMER_CARE_LOOKAHEAD lines) since it's meant to be a readable
    address, not a scan of the whole page. Phone/email are searched
    separately and further afield by `_care_contact` below, since contact
    details are frequently printed several lines past this block once
    PaddleOCR's multi-column line order is accounted for.

    The boundary pattern below is deliberately a list of GENERIC declaration
    categories common across most packaged-commodity labels (storage/usage
    instructions, nutrition info, ingredients, batch/expiry, disposal
    instructions, ...), not anything specific to one product - real labels
    frequently interleave one of these right after the consumer-care trigger
    line (e.g. "STORAGE CONDITIONS: ..." printed in an adjacent column), and
    without recognizing it as a boundary, that unrelated text gets absorbed
    into the address block. Stopping here (a narrower, smarter boundary) is
    the fix - NOT widening CONSUMER_CARE_LOOKAHEAD, which would just absorb
    more unrelated text over a longer distance instead of less.

    A boundary keyword match STOPS the block entirely (we've moved into a
    different declaration's territory). A bare unrelated fragment (see
    _looks_like_unrelated_fragment) is instead SKIPPED, not a stop signal -
    real address text can resume on the very next line once column
    interleaving is accounted for (e.g. a batch number sitting between two
    genuine address lines), confirmed on a real label where "A05124C1" (a
    batch number) and "BISCUITS" (a stray category word) both sat between
    the care trigger and its genuine continuing address line."""
    block = [lines[start]]
    declaration = re.compile(
        r"(?:mrp|maximum\s+retail|net\s+(?:quantity|qty|weight)|country\s+of\s+origin|"
        r"manufactur|packed?\s+on|mfg|best\s+before|made\s+in|"
        r"storage\s+condition|store\s+in\s+a|keep\s+(?:in|away|refrigerat)|"
        r"use\s+by|expir|batch\s*(?:no\.?|number)?\s*[:\-]|"
        r"nutrition(?:al)?\s+information|ingredients?\s*:|"
        r"keep\s+your\s+\w+\s+clean|dispos)",
        re.I,
    )
    for line in lines[start + 1:start + 1 + CONSUMER_CARE_LOOKAHEAD]:
        if declaration.search(line):
            break
        if _looks_like_unrelated_fragment(line):
            continue
        block.append(line)
    return "\n".join(block)


def _care_contact(lines: list[str], start: int) -> tuple[str | None, str | None]:
    """Search CONSUMER_CARE_CONTACT_WINDOW lines from the "consumer care"
    trigger for a phone number and/or email address PATTERN, independent of
    `_care_block`'s shorter descriptive-text window - contact details are
    sometimes printed well past the address text once multi-column
    interleaving is accounted for (see Phase 6 investigation: 8+ lines away
    on the real test image, past the previous fixed 3-line lookahead)."""
    phone = None
    email = None
    search_end = min(len(lines), start + CONSUMER_CARE_CONTACT_WINDOW)
    for line in lines[start:search_end]:
        if phone is None:
            found = re.search(r"(?:\+91[\s-]?)?[0-9][0-9\s-]{6,14}[0-9]", line)
            if found:
                phone = found.group(0)
        if email is None:
            found = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", line)
            if found:
                email = found.group(0)
        if phone and email:
            break
    return phone, email


def _region_confidence(region: dict[str, Any]) -> float:
    """Average OCR confidence of a region's own text-line detections - the
    same style of average `_text_and_confidence` uses for the whole page,
    just scoped to one region."""
    detections = region.get("detections") or []
    if not detections:
        return 0.0
    return sum(float(item.get("confidence", 0)) for item in detections) / len(detections)


def _apply_consumer_care_region(fields: dict[str, NormalizedField], region: dict[str, Any]) -> None:
    """Populate consumer_care_details from an isolated consumer_care region's
    own OCR text. Unlike `_care_block` (which has to guess where a
    whole-page text block ends by scanning forward for the next
    declaration's keyword), a region's crop boundary already IS the block
    boundary - no forward-scan heuristic needed here."""
    text = str(region.get("full_text", "")).strip()
    confidence = _region_confidence(region)
    if not text:
        fields["consumer_care_details"] = NormalizedField(value=None, detected=False, confidence=confidence)
        return
    phone = re.search(r"(?:\+91[\s-]?)?[0-9][0-9\s-]{6,14}[0-9]", text)
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    fields["consumer_care_details"] = NormalizedField(
        value={
            "name": None,
            "address": text,
            "telephone_number": phone.group(0) if phone else None,
            "email_address": email.group(0) if email else None,
        },
        detected=True,
        confidence=confidence,
    )


def _apply_mrp_region(fields: dict[str, NormalizedField], region: dict[str, Any]) -> None:
    """Populate maximum_retail_price_mrp from an isolated mrp region's own
    OCR text. `_mrp_result` (in _rule_result below) does the actual
    amount/wording validation against whatever text ends up in this field's
    `value` - unchanged either way, so populating it from a region instead
    of a whole-page regex match requires no changes there."""
    text = str(region.get("full_text", "")).strip()
    fields["maximum_retail_price_mrp"] = NormalizedField(
        value=text or None, detected=bool(text), confidence=_region_confidence(region),
    )


def _apply_mfg_date_batch_region(fields: dict[str, NormalizedField], metadata: dict[str, Any], region: dict[str, Any]) -> None:
    """Populate month_and_year_of_manufacture_or_packing (a real required
    field) from an mfg_date_batch region, and - per the documented decision
    in training/CLASS_TAXONOMY.md class 6 - sub-parse the SAME region text
    for a batch number and an expiry/best-before date into `metadata`
    (optional, not a required field anywhere in the current ruleset, but
    extracted rather than discarded since it was already read by OCR from
    the same region)."""
    text = str(region.get("full_text", "")).strip()
    confidence = _region_confidence(region)
    fields["month_and_year_of_manufacture_or_packing"] = NormalizedField(
        value=text or None, detected=bool(text), confidence=confidence,
    )
    if not text:
        return
    batch_match = _BATCH_PATTERN.search(text)
    if batch_match:
        metadata["batch_lot_number"] = batch_match.group(1)
    expiry_match = _EXPIRY_PATTERN.search(text)
    if expiry_match:
        metadata["expiry_or_best_before_date"] = expiry_match.group(1).strip()


def _apply_regions(
    fields: dict[str, NormalizedField], metadata: dict[str, Any], regions: list[dict[str, Any]],
) -> None:
    """Overlay region-based (per-declaration-class) OCR extraction onto the
    whole-page alias-matched `fields` dict, in place. Only called when
    `ocr_result["regions"]` is non-empty (multi_region YOLO detection mode,
    see backend/ocr/yolo_service.py) - a no-op otherwise, leaving the
    existing whole-page alias-matching as the only source of truth exactly
    as before multi-region support existed.

    Region data is treated as MORE authoritative than whole-page alias
    matching when both exist for the same field: a region is a specific,
    localized detection, whereas whole-page matching is a keyword-in-a-
    text-blob heuristic. If multiple regions share the same YOLO class
    (e.g. two separate `mrp` boxes - a real, expected case per
    training/LABELING_GUIDE.md section F), the HIGHEST-detection-confidence
    region is used as authoritative for that class's field(s); resolving
    genuine multi-instance ambiguity (e.g. "2 MRP values found, needs
    manual verification") is future rules-engine work, not built here.
    """
    if not regions:
        return

    best_by_class: dict[str, dict[str, Any]] = {}
    for region in regions:
        class_name = region.get("class_name")
        if not class_name:
            continue
        current_best = best_by_class.get(class_name)
        if current_best is None or region.get("detection_confidence", 0) > current_best.get("detection_confidence", 0):
            best_by_class[class_name] = region

    for class_name, region in best_by_class.items():
        if class_name == "mrp":
            _apply_mrp_region(fields, region)
        elif class_name == "consumer_care":
            _apply_consumer_care_region(fields, region)
        elif class_name == "mfg_date_batch":
            _apply_mfg_date_batch_region(fields, metadata, region)
        elif class_name in YOLO_CLASS_TO_FIELDS:
            text = str(region.get("full_text", "")).strip()
            confidence = _region_confidence(region)
            for field_name in YOLO_CLASS_TO_FIELDS[class_name]:
                fields[field_name] = NormalizedField(value=text or None, detected=bool(text), confidence=confidence)
        # An unrecognized class_name (e.g. a COCO class from the pretrained
        # model, if multi_region were ever misconfigured against it) is
        # silently ignored here - not this function's job to validate which
        # model produced `regions`, only to apply what it understands.


def normalize_ocr_result(ocr_result: dict[str, Any], document_id: str | None = None) -> NormalizedOCRResult:
    """Create a derived view of OCR data; the supplied OCR dictionary is never mutated."""
    raw_text, average_confidence = _text_and_confidence(ocr_result)
    lowered = raw_text.lower()
    product_type = str(ocr_result.get("product_type") or ocr_result.get("category") or "packaged_commodity").lower()
    package_type = str(ocr_result.get("package_type") or "retail").lower()
    fields: dict[str, NormalizedField] = {}
    for field_name, aliases in ALIASES.items():
        match = next((alias for alias in aliases if alias in lowered), None)
        matched_line = None
        if match:
            matched_line = next((line.strip() for line in raw_text.splitlines() if match in line.lower()), raw_text)
        elif field_name in REGEX_ALIASES:
            # Fallback for real phrasing a plain substring can't match (e.g.
            # "MANUFACTURED & MARKETED BY:" contains neither "manufacturer"
            # nor the literal phrase "manufactured by") - see REGEX_ALIASES.
            for pattern in REGEX_ALIASES[field_name]:
                found_line = next((line.strip() for line in raw_text.splitlines() if pattern.search(line)), None)
                if found_line:
                    matched_line = found_line
                    match = True  # sentinel: "detected", value already resolved above
                    break
        fields[field_name] = NormalizedField(value=matched_line, detected=match is not None, confidence=average_confidence)

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    metadata = {key: ocr_result.get(key) for key in ("instrument_category", "last_verification_date", "physical_seal_verified", "certificate_of_verification", "total_package_weight", "wrapper_packaging_weight") if ocr_result.get(key) is not None}

    # common_generic_name_of_commodity / identity_of_commodity: an explicit
    # "product name"/"commodity"/"generic name" label match above is
    # preferred, but real packages almost never print that exact label -
    # most of the time neither field is detected via ALIASES at all.
    # Rather than blindly taking the first OCR line (which can just as
    # easily be a section heading like "NUTRITIONAL INFORMATION" or a price
    # badge fragment like "10/-" - both real, observed regressions, not
    # hypothetical), scan the first few lines for one that (a) isn't itself
    # another declaration heading/price (_is_identity_candidate) and (b)
    # ideally names a recognizable commodity category
    # (_COMMODITY_NOUN_PATTERN), preferring (b) wherever it's found rather
    # than settling for the first eligible line positionally.
    #
    # If no line satisfies even (a) - or, per the explicit requirement here,
    # if we can't find a genuine (b) candidate as a description - leave the
    # field undetected rather than guessing: a bare brand name (e.g.
    # "PARLE") is not a "reliable" generic-name extraction just because it
    # isn't a rejected heading, and this field's confidence multiplier
    # already pushes any accepted guess below the manual-verification
    # threshold anyway (see CONFIDENCE_THRESHOLD) - an explicit "not
    # detected" is more honest than a still-displayed low-confidence guess.
    identity_candidates = [line for line in lines[:10] if _is_identity_candidate(line)]
    identity_value = next(
        (c for c in identity_candidates if _is_short_description_shaped(c) and _COMMODITY_NOUN_PATTERN.search(c)),
        None,
    )
    if identity_value:
        for identity_field in ("common_generic_name_of_commodity", "identity_of_commodity"):
            if not fields[identity_field].detected:
                fields[identity_field] = NormalizedField(
                    value=identity_value, detected=True, confidence=average_confidence * 0.75,
                )

    # net_quantity / total_number_of_retail_packages_or_net_quantity: the
    # plain ALIASES match above frequently lands on a keyword line with NO
    # number on it at all (e.g. "NET WEIGHT:" alone, the actual "75 g"
    # printed on a separate, nearby line once a multi-column layout is
    # accounted for) - the same interleaving problem already fixed for MRP
    # above. This matters beyond display: _unit_sale_price_result below
    # needs an actual parseable quantity to determine whether the retail-
    # price-equals-unit-price proviso applies, so a keyword-only value with
    # no number is not just a cosmetic gap. Only completes an ALREADY-
    # detected field (never invents a detection where none occurred).
    #
    # A candidate is skipped (not accepted, but the search continues to
    # later lines) when it sits in nutrition-table context - real nutrition
    # tables print several per-100g/per-serving gram values (protein, fat,
    # carbohydrate, sugars, ...) that also match the bare quantity pattern,
    # and one of them can land inside this search window once interleaved
    # with the genuine "Net Weight" declaration (e.g. a real label's
    # "Protein / 6.6 g" pair sitting between the keyword and the actual
    # "50 g" net weight).
    for qty_field_name in ("net_quantity", "total_number_of_retail_packages_or_net_quantity"):
        qty_field = fields[qty_field_name]
        if not qty_field.detected or not qty_field.value or _parse_net_quantity(str(qty_field.value)):
            continue
        qty_index = next((index for index, line in enumerate(lines) if line == qty_field.value), None)
        if qty_index is None:
            continue
        for offset, candidate in enumerate(lines[qty_index + 1: qty_index + 1 + NEARBY_LINE_WINDOW], start=qty_index + 1):
            if not _parse_net_quantity(candidate) or _in_nutrition_table_context(lines, offset):
                continue
            fields[qty_field_name] = NormalizedField(
                value=f"{qty_field.value} {candidate}", detected=True, confidence=qty_field.confidence,
            )
            break

    # manufacturer_packer_importer_details / name_and_address_of_manufacturer_or_packer:
    # both fields are populated ENTIRELY by _entity_declarations - see that
    # function's docstring and the "Manufacturer / Packer / Importer /
    # Marketer / Distributor" section above for the full design (multi-role
    # separation, heading-only continuation resolution, OCR-variant
    # tolerance). Every role found gets its own resolved value; multiple
    # roles are joined with " | " so none of them silently overwrites or
    # merges with another (see _manufacturer_result below for how this
    # combined value is validated).
    entity_declarations = _entity_declarations(lines)
    if entity_declarations:
        combined_entity_value = " | ".join(value for _role, value in entity_declarations)
        for entity_field_name in ("manufacturer_packer_importer_details", "name_and_address_of_manufacturer_or_packer"):
            fields[entity_field_name] = NormalizedField(value=combined_entity_value, detected=True, confidence=average_confidence)
        metadata["entity_roles_detected"] = [role for role, _value in entity_declarations]
    else:
        for entity_field_name in ("manufacturer_packer_importer_details", "name_and_address_of_manufacturer_or_packer"):
            fields[entity_field_name] = NormalizedField()

    mrp_index = next((index for index, line in enumerate(lines) if re.search(r"maximum\s+retail\s+price|m\.?r\.?p\.?", line, re.I)), None)
    mrp_line = None
    if mrp_index is not None:
        mrp_line = lines[mrp_index]
        if not _mrp_amounts(mrp_line):
            # The MRP label's own line often has no amount when a
            # multi-column layout interleaves it with an unrelated column
            # (e.g. an address block) - search a wider forward window for
            # the first line that actually looks like a price (either
            # already currency/keyword-qualified, or a bare decimal amount
            # like "20.00") rather than blindly appending whatever line
            # happens to be immediately next.
            for candidate in lines[mrp_index + 1: mrp_index + 1 + NEARBY_LINE_WINDOW]:
                if _mrp_amounts(candidate) or _BARE_AMOUNT_PATTERN.match(candidate):
                    mrp_line = f"{mrp_line} {candidate}"
                    break
    if mrp_line:
        fields["maximum_retail_price_mrp"] = NormalizedField(value=mrp_line, detected=True, confidence=average_confidence)
    elif len(re.findall(r"(?:₹|Rs\.?|INR)\s*[0-9]+(?:[.,][0-9]{1,2})?", raw_text, re.I)) != 1 or re.search(r"unit\s+sale|price\s+per|/kg|/g|/l", raw_text, re.I):
        fields["maximum_retail_price_mrp"] = NormalizedField()

    # month_and_year_of_manufacture_or_packing: dedicated handling, mirrors
    # the MRP block above. The bare-word ALIASES match ("manufactured",
    # "mfg", ...) can land on a non-date line entirely - e.g. an
    # entity/address header containing "manufactured" - which is exactly
    # what happened on the real test image. Only accept a keyword-matching
    # line as the date value if an actual date-like pattern
    # (DD/MM/YYYY, MM/YYYY, or "Mon YYYY") appears on that line or within
    # NEARBY_LINE_WINDOW lines either side; try each keyword-matching line
    # in order and skip ones with no nearby date rather than accepting the
    # first (possibly wrong) keyword hit. If no candidate has a nearby date
    # at all, leave the field undetected rather than reporting a
    # plainly-wrong value.
    date_value = None
    for index, line in enumerate(lines):
        if not _DATE_KEYWORD_PATTERN.search(line):
            continue
        if _DATE_PATTERN.search(line):
            date_value = line
            break
        window_start = max(0, index - NEARBY_LINE_WINDOW)
        window_end = min(len(lines), index + NEARBY_LINE_WINDOW + 1)
        nearby_date_line = next(
            (lines[i] for i in range(window_start, window_end) if _DATE_PATTERN.search(lines[i])), None,
        )
        if nearby_date_line:
            date_value = nearby_date_line
            break
    fields["month_and_year_of_manufacture_or_packing"] = (
        NormalizedField(value=date_value, detected=True, confidence=average_confidence)
        if date_value else NormalizedField()
    )

    care_start = _find_care_start(lines)
    if care_start is not None:
        care_text = _care_block(lines, care_start)
        name = _care_name(lines[care_start])
        care_text = re.sub(r"^(?:consumer|customer)\s+care|customer\s+service|helpline|toll\s*free|contact\s+us\s*[:-]?", "", care_text, flags=re.I).strip(" :-\n")
        phone, email = _care_contact(lines, care_start)
        fields["consumer_care_details"] = NormalizedField(
            value={
                "name": name,
                "address": care_text or None,
                "telephone_number": phone,
                "email_address": email,
            },
            detected=True,
            confidence=average_confidence,
        )

    # Region-based (per-declaration-class) extraction, when present, takes
    # priority over the whole-page alias matching above - see _apply_regions'
    # docstring. A no-op when ocr_result has no "regions" key (single_region
    # YOLO mode, or no YOLO at all), so this changes nothing for any existing
    # caller that doesn't supply per-region OCR data. (`metadata` was
    # initialized earlier, right after `lines`, so entity-role evidence
    # from _entity_declarations above is already in it.)
    _apply_regions(fields, metadata, ocr_result.get("regions") or [])

    supplied_fields = ocr_result.get("fields") or {}
    for name, supplied in supplied_fields.items():
        if isinstance(supplied, dict):
            fields[name] = NormalizedField(value=supplied.get("value", supplied), detected=bool(supplied.get("detected", supplied.get("value") is not None)), confidence=float(supplied.get("confidence", 0)))
        else:
            fields[name] = NormalizedField(value=supplied, detected=supplied is not None, confidence=average_confidence)

    return NormalizedOCRResult(document_id=document_id or str(ocr_result.get("document_id") or f"DOC-{uuid.uuid4().hex[:8].upper()}"), product_type=product_type, package_type=package_type, raw_text=raw_text, raw_ocr_result=dict(ocr_result), fields=fields, metadata=metadata)


def _field(normalized: NormalizedOCRResult, name: str) -> NormalizedField:
    return normalized.fields.get(name, NormalizedField())


# --- Confirmed-pass / confirmed-fail / low-confidence scoring model -------
#
# This project's OCR-based extraction can confidently assert PRESENCE (text
# matching a declaration was located) but can essentially never confidently
# assert ABSENCE (a keyword not matching could mean the declaration isn't on
# the package, or just as easily that OCR missed it - angle, crop, blur, a
# multi-panel layout, glare). Every "not detected by OCR" branch below is
# therefore LOW-CONFIDENCE evidence, not confirmed non-compliance, UNLESS a
# field has a positive mechanism to actually confirm a shortfall (the one
# case in this ruleset: consumer_care_details, where the declaration block
# itself WAS located and specific required sub-fields within it are
# confirmably absent from that located text - see the PARTIAL branch below).
# See training/CLASS_TAXONOMY.md-adjacent Phase 6-9 investigations for the
# concrete real-image evidence this policy is built on.
def _status_bucket(status: str) -> str:
    """Collapse any FieldStatus or ComplianceStatus value into exactly one
    of three scoring buckets: a CONFIRMED pass, a CONFIRMED failure (FAIL
    and PARTIAL both represent evidence-backed shortfalls - PARTIAL just at
    partial completeness rather than total absence), or LOW_CONFIDENCE
    (insufficient/ambiguous evidence - NEVER counted as a pass or a fail).
    Anything else (NOT_APPLICABLE, NOT_DETECTED, ...) is excluded from
    scoring entirely - not this function's concern to classify further."""
    if status in ("PASS", "COMPLIANT"):
        return "compliant"
    if status in ("FAIL", "PARTIAL", "NON_COMPLIANT"):
        return "non_compliant"
    if status in ("NEEDS_MANUAL_VERIFICATION", "REVIEW_REQUIRED"):
        return "low_confidence"
    return "excluded"


def _aggregate_checks(statuses: list[str]) -> tuple[str, float | None, dict[str, int]]:
    """The core three-state scoring/status aggregation: given every
    APPLICABLE check's status (caller must have already excluded anything
    NOT_APPLICABLE - this function has no concept of applicability), return:
      - overall status: any confirmed non-compliant check wins first
        (NON_COMPLIANT), else any low-confidence check (REVIEW_REQUIRED),
        else COMPLIANT - never inferring compliance from an absence of
        confirmed failures alone when uncertainty remains unresolved.
      - numeric score: confirmed_passes / (confirmed_passes +
        confirmed_failures) * 100, excluding low-confidence checks from
        BOTH numerator and denominator - None (never 0, never 100) if there
        are zero confirmed checks to score at all.
      - bucket counts, for score-transparency reporting.
    """
    buckets = [_status_bucket(s) for s in statuses]
    compliant = buckets.count("compliant")
    non_compliant = buckets.count("non_compliant")
    low_confidence = buckets.count("low_confidence")
    scored = compliant + non_compliant
    score = round(compliant / scored * 100, 2) if scored else None
    if non_compliant:
        status = "NON_COMPLIANT"
    elif low_confidence:
        status = "REVIEW_REQUIRED"
    else:
        status = "COMPLIANT"
    return status, score, {"compliant": compliant, "non_compliant": non_compliant, "low_confidence": low_confidence}


def _field_result(field: NormalizedField, label: str) -> FieldResult:
    if not field.detected:
        return FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation=f"{label} was not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.")
    if field.confidence < CONFIDENCE_THRESHOLD:
        return FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=field.value, confidence=field.confidence, explanation=f"{label} was detected with low OCR confidence.")
    return FieldResult(status="PASS", value=field.value, confidence=field.confidence, explanation=f"{label} was detected by OCR.")


def _manufacturer_result(field: NormalizedField, requirement: str) -> tuple[FieldResult, Evidence]:
    """Validate manufacturer_packer_importer_details / name_and_address_of_
    manufacturer_or_packer - both populated by _entity_declarations, which
    joins every distinct role found with " | ".

    A detected declaration HEADING without an actual entity value must not
    be treated as valid evidence (per this task's explicit requirement) -
    if EVERY joined segment is still heading-only (no continuation was
    found anywhere), the entity's identity was never actually confirmed.
    If AT LEAST ONE role's entity value was confirmed, that is real,
    positive evidence - PASS - even if another role on the same package
    (e.g. a marketer mentioned without a resolvable address) remained
    heading-only; the confirmed segment(s) are not discarded because of an
    unrelated, separately-tracked role's incompleteness.
    """
    if not field.detected:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation=f"{requirement} was not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.")
        return result, Evidence(requirement=requirement, validation="No manufacturer/packer/importer/marketer/distributor declaration detected", result="NEEDS_MANUAL_VERIFICATION")
    if field.confidence < CONFIDENCE_THRESHOLD:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=field.value, confidence=field.confidence, explanation=f"{requirement} was detected with low OCR confidence.")
        return result, Evidence(requirement=requirement, ocr_evidence=str(field.value), validation="Low-confidence OCR", result="NEEDS_MANUAL_VERIFICATION")
    value = str(field.value)
    segments = [segment.strip() for segment in value.split(" | ") if segment.strip()]
    if segments and all(_HEADING_ONLY_PATTERN.match(segment) for segment in segments):
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation=f"A {requirement.lower()} declaration heading was detected, but no entity name/address could be confirmed nearby.")
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Declaration heading present; entity value not confirmed", result="NEEDS_MANUAL_VERIFICATION")
    result = FieldResult(status="PASS", value=value, confidence=field.confidence, explanation=f"{requirement} detected with entity details.")
    return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Declaration heading and entity value both confirmed", result="PASS")


def _mrp_result(field: NormalizedField, requirement: str) -> tuple[FieldResult, Evidence]:
    if not field.detected:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation="MRP was not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.")
        return result, Evidence(requirement=requirement, validation="No INR amount or MRP label detected", result="NEEDS_MANUAL_VERIFICATION")
    if field.confidence < CONFIDENCE_THRESHOLD:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=field.value, confidence=field.confidence, explanation="MRP OCR confidence is below the verification threshold.")
        return result, Evidence(requirement=requirement, ocr_evidence=str(field.value), validation="Low-confidence OCR", result="NEEDS_MANUAL_VERIFICATION")
    value = str(field.value)
    amounts = _mrp_amounts(value)
    has_label = bool(re.search(r"(?:maximum\s+retail\s+price|m\.?r\.?p\.?)", value, re.I))
    if not amounts:
        if has_label:
            # The explicit "Maximum Retail Price"/"MRP" WORDING was
            # positively identified on this exact line - strong, confirming
            # evidence this genuinely IS the MRP declaration - and it
            # contains no valid amount at all. That is a structural defect
            # in the declaration itself (PRESENT_INVALID), not an
            # OCR-reading-quality question, so a confirmed violation is
            # the correct, evidence-backed call here, not a hedge.
            result = FieldResult(status="FAIL", value=value, explanation="MRP wording was detected, but the declaration does not contain a valid INR amount.")
            return result, Evidence(requirement=requirement, ocr_evidence=value, validation="MRP wording present; no valid amount - confirmed invalid declaration", result="FAIL")
        # No MRP wording confirmed on this line either - the match came from
        # a weaker alias (e.g. a bare "rs."/"₹" substring) that could just as
        # plausibly be an unrelated mention, not confirmed to be the MRP
        # declaration at all. Genuinely ambiguous -> needs manual review,
        # not a confirmed violation.
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="Text resembling a price was detected, but MRP wording was not confirmed on this line and no valid amount could be parsed - too ambiguous to confirm as a valid or invalid MRP declaration.")
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="INR amount not parseable; MRP wording not confirmed on this line", result="NEEDS_MANUAL_VERIFICATION")
    if len(amounts) > 1:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="OCR detected multiple possible MRP values.")
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Multiple amounts detected", result="NEEDS_MANUAL_VERIFICATION")
    if not has_label:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="An amount was detected, but the required MRP wording was not detected.")
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid amount; MRP wording absent", result="NEEDS_MANUAL_VERIFICATION")
    result = FieldResult(status="PASS", value={"detected_value": value, "normalized_value": float(amounts[0].replace(",", "")), "currency": "INR"}, confidence=field.confidence, explanation="Valid MRP amount and MRP wording detected.")
    return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid MRP amount and keyword", result="PASS")


# The net quantity at which a package's retail selling price (MRP) is, by
# simple arithmetic, numerically identical to its unit sale price: a package
# containing EXACTLY 1 kg (solid/semi-solid) or 1 litre (liquid) costs the
# same per-kg/per-litre as its own MRP, since it IS 1 of that unit. Both
# bases normalize to 1000 in _parse_net_quantity's grams-or-millilitres
# scale. This is the concrete, computable form of the Legal Metrology
# (Packaged Commodities) Amendment Rules, 2022 proviso the task describes
# ("packages where the retail selling price is equal to the unit sale
# price") - derived from the rule's own stated unit-basis switch point (per
# gram below 1 kg, per kilogram above 1 kg), not guessed.
_UNIT_PRICE_EQUALS_MRP_QUANTITY = 1000.0


def _unit_sale_price_result(field: NormalizedField, net_quantity_field: NormalizedField, requirement: str) -> tuple[FieldResult, Evidence]:
    """Validate unit_sale_price per LMPC-R6, honoring the 2022 amendment's
    proviso instead of ever substituting MRP for a missing declaration.

    Four distinct outcomes, matching four distinct real situations:
    0. An explicit unit-sale-price declaration was detected, but contains no
       valid per-unit amount -> confirmed invalid (NON_COMPLIANT/FAIL), not
       merely uncertain - the wording is strong, specific evidence this line
       IS a unit-price declaration attempt, so a structural defect in it is
       a confirmed violation, not an OCR-quality hedge.
    1. An explicit unit-sale-price declaration was detected with a valid
       amount -> PASS (mirrors _mrp_result's structure).
    2. None was detected, but net quantity is parseable and equals exactly
       1 kg/1 litre -> the proviso applies; the separate declaration is not
       required, and this is recorded as satisfied WITHOUT fabricating a
       price value (the FieldResult.value is a plain-language exemption
       statement, never a number that was never printed on the package).
    3. None was detected and the proviso does not apply, or net quantity
       can't be determined -> NEEDS_MANUAL_VERIFICATION (OCR not finding a
       declaration is not confirmed physical absence - see _field_result's
       docstring above for why this project treats that as low-confidence,
       not a confirmed violation); MRP is NEVER copied into this field's
       value under any circumstance.
    """
    if field.detected:
        value = str(field.value)
        amount_match = _UNIT_PRICE_AMOUNT_PATTERN.search(value)
        if not amount_match:
            # Unlike MRP's alias list (which includes weak, generic
            # triggers like a bare "rs."/"₹"), every unit_sale_price alias
            # ("unit sale price", "price per", "/kg", "/g", "/litre", "/l")
            # is already specific to a per-unit-price context - detection
            # here is strong, confirming evidence this line IS a unit-price
            # declaration attempt. No valid per-unit amount in it is a
            # confirmed structural defect (PRESENT_INVALID), not merely an
            # OCR-quality question.
            result = FieldResult(status="FAIL", value=value, explanation="Unit sale price wording was detected, but the declaration does not contain a valid per-unit amount.")
            return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Unit sale price wording present; no valid per-unit amount - confirmed invalid declaration", result="FAIL")
        if field.confidence < CONFIDENCE_THRESHOLD:
            result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="Unit sale price OCR confidence is below the verification threshold.")
            return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Low-confidence OCR", result="NEEDS_MANUAL_VERIFICATION")
        result = FieldResult(status="PASS", value={"detected_value": value, "normalized_value": float(amount_match.group(1).replace(",", "")), "currency": "INR"}, confidence=field.confidence, explanation="Valid unit sale price amount detected.")
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid unit sale price amount", result="PASS")

    quantity_text = str(net_quantity_field.value) if net_quantity_field.detected and net_quantity_field.value else ""
    parsed = _parse_net_quantity(quantity_text)
    if parsed is None:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation="Unit sale price was not detected by OCR, and net quantity could not be parsed to determine whether the retail-price-equals-unit-price proviso applies.")
        evidence_kwargs = {"ocr_evidence": quantity_text} if quantity_text else {}
        return result, Evidence(requirement=requirement, validation="Net quantity not parseable; proviso applicability unknown", result="NEEDS_MANUAL_VERIFICATION", **evidence_kwargs)

    amount, _basis = parsed
    if abs(amount - _UNIT_PRICE_EQUALS_MRP_QUANTITY) < 0.01:
        result = FieldResult(
            status="PASS",
            value="Not separately required: net quantity equals 1 kg/1 litre, so the retail selling price (MRP) already equals the unit sale price under the Legal Metrology (Packaged Commodities) Amendment Rules, 2022 proviso.",
            confidence=net_quantity_field.confidence,
            explanation="Unit sale price requirement satisfied via the retail-price-equals-unit-price proviso.",
        )
        return result, Evidence(requirement=requirement, ocr_evidence=quantity_text, validation="Net quantity = 1 kg/litre; proviso applies", result="PASS")

    result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation="Unit sale price was not detected by OCR, and net quantity does not equal 1 kg/1 litre, so the retail-price-equals-unit-price proviso does not apply. Physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.")
    return result, Evidence(requirement=requirement, ocr_evidence=quantity_text, validation="Proviso does not apply; unit sale price required but not detected by OCR (absence unconfirmed)", result="NEEDS_MANUAL_VERIFICATION")


def _rule_result(rule: dict[str, Any], normalized: NormalizedOCRResult) -> RuleResult:
    rule_id = rule["rule_id"]
    fields: dict[str, FieldResult] = {}
    evidence: list[str] = []
    details: list[Evidence] = []
    if rule_id == "LMPC-R6-MANDATORY-DECLARATIONS":
        for name in rule["required_fields"]:
            current = _field(normalized, name)
            if name == "manufacturer_packer_importer_details":
                result, detail = _manufacturer_result(current, "Manufacturer/Packer/Importer Details")
                fields[name], details = result, details + [detail]
            elif name == "maximum_retail_price_mrp":
                result, detail = _mrp_result(current, "Maximum Retail Price")
                fields[name], details = result, details + [detail]
            elif name == "unit_sale_price":
                result, detail = _unit_sale_price_result(current, _field(normalized, "net_quantity"), "Unit Sale Price")
                fields[name], details = result, details + [detail]
            elif name == "consumer_care_details":
                care = current.value if isinstance(current.value, dict) else {}
                missing = [key for key in rule["validations"]["consumer_care_fields"] if not care.get(key)]
                if current.detected and missing:
                    # The declaration block WAS located, and specific
                    # required sub-fields within it are confirmably absent
                    # from that located text - positive evidence of a
                    # shortfall, not mere OCR non-detection. This is the one
                    # field in R6 with a real absence-confirmation
                    # mechanism, so PARTIAL (a confirmed non-compliant
                    # outcome for scoring) remains correct here.
                    status, explanation = "PARTIAL", f"Consumer-care details are incomplete: {', '.join(missing)} not confirmed."
                elif current.detected:
                    status, explanation = "PASS", "Consumer-care details detected."
                else:
                    # No consumer-care trigger phrase was found anywhere in
                    # the OCR text - the same "OCR didn't find it" gap as
                    # every other undetected field, not confirmed absence.
                    status, explanation = "NEEDS_MANUAL_VERIFICATION", "Consumer-care details were not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation."
                fields[name] = FieldResult(status=status, value=current.value, confidence=current.confidence, missing=missing, explanation=explanation)
            else:
                fields[name] = _field_result(current, name.replace("_", " ").title())
            evidence.append(f"{name}: {fields[name].status}")
        status, score, _bucket_counts = _aggregate_checks([item.status for item in fields.values()])
        missing = [name for name, item in fields.items() if item.status in ("FAIL", "PARTIAL")]
        if status == "NON_COMPLIANT":
            explanation = "Mandatory declaration requirements are not fully satisfied."
        elif status == "REVIEW_REQUIRED":
            explanation = "One or more mandatory declarations could not be confidently verified from the available evidence and require manual review."
        else:
            explanation = "All mandatory declarations were detected and validated."
        return RuleResult(rule_id=rule_id, rule_name="Mandatory Declarations", status=status, score=score, required_fields=fields, explanation=explanation, evidence=evidence, evidence_details=details, recommended_action="Verify the physical package and add or correct missing declarations if absent.",)
    if rule_id == "LMPC-R11-NET-QUANTITY-EXCLUSION":
        net = normalized.metadata.get("net_quantity") or normalized.fields.get("net_quantity", NormalizedField()).value
        total = normalized.metadata.get("total_package_weight")
        wrapper = normalized.metadata.get("wrapper_packaging_weight")
        if total is None or wrapper is None or net is None:
            return RuleResult(rule_id=rule_id, rule_name="Net Quantity Exclusion", status="NEEDS_MANUAL_VERIFICATION", explanation="Required weight evidence was not detected by OCR.", evidence=["Net, total, or wrapper weight evidence unavailable"], recommended_action="Verify the package weights and statistical tolerances manually.")
        expected = float(total) - float(wrapper)
        passed = abs(float(net) - expected) < 0.0001
        return RuleResult(rule_id=rule_id, rule_name="Net Quantity Exclusion", status="COMPLIANT" if passed else "NON_COMPLIANT", score=100 if passed else 0, explanation="Net quantity equals package weight less wrapper weight." if passed else "Net quantity does not equal package weight less wrapper weight.", evidence=[f"Expected net quantity: {expected}"], recommended_action="Check the declared net quantity and applicable statistical tolerances.")
    if rule_id == "LMPC-R24-WHOLESALE-DECLARATIONS":
        names = rule["required_fields"]
        for name in names:
            if name == "name_and_address_of_manufacturer_or_packer":
                fields[name], _detail = _manufacturer_result(_field(normalized, name), "Name and Address of Manufacturer or Packer")
            else:
                fields[name] = _field_result(_field(normalized, name), name.replace("_", " ").title())
        status, score, _bucket_counts = _aggregate_checks([item.status for item in fields.values()])
        if status == "NON_COMPLIANT":
            explanation = "Wholesale declaration requirements are not fully satisfied."
        elif status == "REVIEW_REQUIRED":
            explanation = "One or more wholesale declarations could not be confidently verified from the available evidence and require manual review."
        else:
            explanation = "Wholesale declarations detected."
        return RuleResult(rule_id=rule_id, rule_name="Wholesale Declarations", status=status, score=score, required_fields=fields, explanation=explanation, evidence=[f"{name}: {fields[name].status}" for name in names], recommended_action="Verify wholesale package declarations on the physical package.")
    if rule_id == "LMGEN-R12-VERIFICATION-INTERVALS":
        raw_date = normalized.metadata.get("last_verification_date")
        if not raw_date:
            return RuleResult(rule_id=rule_id, rule_name="Verification Intervals", status="NEEDS_MANUAL_VERIFICATION", explanation="Last verification date was not detected by OCR.", recommended_action="Verify the instrument certificate and date manually.")
        category = str(normalized.metadata.get("instrument_category", "")).lower()
        months = 24 if any(word in category for word in ("capacity", "length", "weight")) else 12
        verified = datetime.fromisoformat(str(raw_date)).date()
        expiry_month = verified.month - 1 + months
        expiry = date(verified.year + expiry_month // 12, expiry_month % 12 + 1, min(verified.day, monthrange(verified.year + expiry_month // 12, expiry_month % 12 + 1)[1]))
        passed = date.today() <= expiry
        return RuleResult(rule_id=rule_id, rule_name="Verification Intervals", status="COMPLIANT" if passed else "NON_COMPLIANT", score=100 if passed else 0, explanation=f"Verification expires on {expiry.isoformat()}." if passed else f"Verification expired on {expiry.isoformat()}.", evidence=[f"Last verification: {verified.isoformat()}", f"Period: {months} months"], recommended_action="Renew the instrument verification.")
    if rule_id == "LMGEN-R14-STAMPING-SEALING":
        if not normalized.metadata.get("physical_seal_verified") or not normalized.metadata.get("certificate_of_verification"):
            return RuleResult(rule_id=rule_id, rule_name="Stamping and Sealing", status="NEEDS_MANUAL_VERIFICATION", explanation="OCR does not provide sufficient evidence that the physical seal and verification certificate are present.", evidence=["Physical verification evidence unavailable from OCR"], recommended_action="Inspect the physical seal, verification mark, and certificate.")
        return RuleResult(rule_id=rule_id, rule_name="Stamping and Sealing", status="COMPLIANT", score=100, explanation="Physical verification evidence was supplied for review.", evidence=["Seal and certificate evidence supplied"], recommended_action="Retain the verification evidence with the audit record.")
    return RuleResult(rule_id=rule_id, rule_name=rule_id, status="NOT_APPLICABLE", explanation="No evaluator is registered for this rule.")


class ComplianceEngine:
    def __init__(self, ruleset_path: Path = RULESET_PATH):
        self.ruleset_path = ruleset_path

    def evaluate(self, ocr_result: dict[str, Any], validation_profile: str) -> ComplianceResult:
        ruleset = load_ruleset(self.ruleset_path)
        normalized = normalize_ocr_result(ocr_result)
        profile = ruleset.get("validation_profiles", {}).get(validation_profile)
        if profile is None:
            raise ValueError(f"Unknown validation profile: {validation_profile}")
        if normalized.product_type in {"unknown", "unrelated", "other"}:
            results = [RuleResult(rule_id="PROFILE", rule_name="Validation Profile", status="NOT_APPLICABLE", explanation="The detected object is not a supported Legal Metrology product.")]
        else:
            all_rules = {rule["rule_id"]: rule for framework in ruleset["frameworks"] for rule in framework["rules"]}
            results = []
            for rule_id in profile.get("evaluate_rules", []):
                if rule_id == "LMPC-R24-WHOLESALE-DECLARATIONS" and normalized.package_type != "wholesale":
                    results.append(RuleResult(rule_id=rule_id, rule_name="Wholesale Declarations", status="NOT_APPLICABLE", explanation="The package type is retail, so the wholesale rule does not apply."))
                else:
                    results.append(_rule_result(all_rules[rule_id], normalized))
        # Rule-level counts - unchanged meaning (RULE statuses, not
        # individual checks), preserved for backward compatibility with
        # anything already reading summary.passed/failed/partial/
        # manual_verification/not_applicable.
        summary = ComplianceSummary(total_rules=len(results), total_checks=sum(len(item.required_fields) or 1 for item in results))
        for item in results:
            if item.status == "COMPLIANT": summary.passed += 1
            elif item.status == "NON_COMPLIANT": summary.failed += 1
            elif item.status == "PARTIALLY_COMPLIANT": summary.partial += 1
            elif item.status in ("NEEDS_MANUAL_VERIFICATION", "REVIEW_REQUIRED"): summary.manual_verification += 1
            elif item.status == "NOT_APPLICABLE": summary.not_applicable += 1

        # Overall status/score: aggregate every APPLICABLE check at the
        # finest granularity available - a rule with its own required_fields
        # contributes each field's status individually (so one field's
        # uncertainty can't be hidden by nine other fields passing); a rule
        # with no required_fields (the instrument-verification rules)
        # contributes its own rule-level status as a single check.
        # NOT_APPLICABLE rules are excluded entirely (never counted as a
        # pass, a fail, or a review) - Part 6's applicability gate.
        overall_statuses: list[str] = []
        for rule in results:
            if rule.status == "NOT_APPLICABLE":
                continue
            if rule.required_fields:
                overall_statuses.extend(item.status for item in rule.required_fields.values())
            else:
                overall_statuses.append(rule.status)

        if overall_statuses:
            overall, score, bucket_counts = _aggregate_checks(overall_statuses)
        else:
            # Every evaluated rule was NOT_APPLICABLE (or there were none) -
            # nothing to score, and NOT COMPLIANT by default either.
            overall, score, bucket_counts = "NOT_APPLICABLE", None, {"compliant": 0, "non_compliant": 0, "low_confidence": 0}

        summary.compliant = bucket_counts["compliant"]
        summary.non_compliant = bucket_counts["non_compliant"]
        summary.low_confidence = bucket_counts["low_confidence"]

        missing = [f"{rule.rule_name}: {name}" for rule in results for name, field in rule.required_fields.items() if field.status in ("FAIL", "PARTIAL")]
        warnings = ["OCR confidence is evidence quality, not legal compliance."]
        if bucket_counts["low_confidence"]:
            warnings.append(f"{bucket_counts['low_confidence']} check(s) could not be confidently verified from the available evidence and require manual review; they are excluded from the numerical score (neither counted as passed nor failed).")
        return ComplianceResult(document_id=normalized.document_id, ruleset_id=ruleset["ruleset_id"], ruleset_version=ruleset["version"], validation_profile=validation_profile, overall_status=overall, compliance_score=score, summary=summary, rule_results=results, missing_fields=missing, warnings=warnings, recommendations=[rule.recommended_action for rule in results if rule.recommended_action], audit={"evaluated_at": datetime.now(timezone.utc).isoformat(), "engine_version": ENGINE_VERSION, "ocr_result_version": ocr_result.get("version", "unknown")})