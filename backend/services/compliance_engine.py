from __future__ import annotations

import json
import re
import uuid
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from backend.models.compliance import ComplianceResult, ComplianceSummary, Evidence, FieldResult, NormalizedField, NormalizedOCRResult, RegionEvidence, RuleResult

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
    #
    # "made in" REMOVED here too (ALIASES audit finding): a plain substring
    # check for "made in" matches inside "Homemade Ingredients"/"Homemade
    # Indian Recipe" ("homemade" ends in "...made", fused with no boundary
    # to " in..." that follows) - both are plausible real snack-packaging
    # phrases, not contrived. Moved to a word-boundaried REGEX_ALIASES entry
    # below, same treatment as bare "origin" above.
    #
    # "product of india" added (real observed fixture wording,
    # Chips_nutrition.jpg's benchmark OCR text: "PRODUCT OF INDIA") - a
    # fixed, self-contained 3-word legal phrase, not a generalized "product
    # of <any country>" pattern (deliberately not added - no evidence any
    # other country's version of this exact phrase occurs in this project's
    # fixtures, and genericizing it risks matching "Product of concern"/
    # "Product of interest" style unrelated phrasing this project has never
    # observed either). Safe as a plain substring exactly like "country of
    # origin" above: "PRODUCT INFORMATION"/"PRODUCT DETAILS"/"INDIAN PRODUCT
    # INFORMATION" do not contain "product of india" anywhere.
    "country_of_origin": ["country of origin", "product of india"],
    "common_generic_name_of_commodity": ["product name", "commodity", "generic name"],
    # Systematic ALIASES audit (see the audit report accompanying this
    # change): "net weight", "net contents", and "net vol" were REMOVED from
    # this plain list and replaced with word-boundaried REGEX_ALIASES
    # entries below. All three share the same demonstrated collision shape
    # - any word ENDING in "net" (e.g. "Cabinet", a hypothetical
    # "Magnet"/"Internet"/"Bonnet") fused with no boundary check to the next
    # word ("Weight"/"Volume"/"Contents") creates a false substring match a
    # plain `alias in lowered` check cannot see, exactly like the already-
    # fixed unit_sale_price "/l"-in-license-code bug. "net quantity"/"net
    # qty"/"net wt" are KEPT here even though they share the same
    # theoretical shape ("Cabinet Quantity"/"Cabinet Qty"/"Cabinet Wt") -
    # deliberately not touched: no real fixture or plausible packaged-
    # commodity wording has ever produced that collision (unlike weight/vol/
    # contents, which map directly to real fixture wording - Cookie_back.jpg,
    # Facewash_back.jpg, Pears_back.jpg respectively), and "net quantity" in
    # particular is this project's single most heavily-tested alias: fixing
    # a theoretical, undemonstrated risk here would trade real regression
    # risk for no verified benefit.
    "net_quantity": ["net quantity", "net qty", "net wt"],
    "month_and_year_of_manufacture_or_packing": ["date of manufacture", "manufactured", "mfg", "packed on", "packing date"],
    # "rs." / "rs " were REMOVED here (ALIASES audit finding: a plain
    # substring check for "rs." matches inside any word ending in "...rs."
    # followed by a period - "hours.", "years.", "colors.", "flavors." are
    # all common, plausible label text, e.g. "Best used within 24 hours."
    # would substring-match "rs." at "hou-RS."). Moved to a word-boundaried
    # REGEX_ALIASES entry below, which "hours."/"years." etc. cannot match
    # (no word boundary exists between "hou"/"yea" and "rs" - both sides are
    # word characters). "maximum retail price"/"mrp"/"m.r.p"/"₹" are kept
    # here - all four are either long specific phrases or symbols with no
    # realistic containing-word collision.
    "maximum_retail_price_mrp": ["maximum retail price", "mrp", "m.r.p", "₹"],
    # Bare "/kg"/"/g"/"/litre"/"/l" were REMOVED here (real observed
    # benchmark bug, Facewash_back.jpg: "/l" matched inside
    # "Mfg. Lic. No. M HIM/COS/L/12/167" - an unrelated manufacturing
    # license code, not a per-litre price). A plain substring check has no
    # way to require the digit+unit context that actually makes something a
    # unit-price declaration; that context requirement now lives entirely in
    # _UNIT_PRICE_AMOUNT_PATTERN via REGEX_ALIASES below, which every
    # genuine "₹X/unit" or "Rs X per unit" declaration already satisfies.
    # "price per" was ALSO removed here (ALIASES audit finding: matches
    # inside "Special Price Period"/"Reduced Price Period" - "price" fused
    # with no boundary to " per..." from "period" - a plausible promotional-
    # sticker phrase). Moved to a word-boundaried REGEX_ALIASES entry below.
    # "unit sale price" is kept as a plain alias - a long, specific
    # 3-word phrase with no realistic false-substring risk.
    "unit_sale_price": ["unit sale price"],
    "consumer_care_details": ["consumer care", "customer care", "helpline", "toll free"],
    "identity_of_commodity": ["product name", "commodity", "generic name"],
    "total_number_of_retail_packages_or_net_quantity": ["net quantity", "number of packages", "retail packages"],
}

# Matches an explicit per-unit price declaration - an amount followed by
# "/<unit>" or "per <unit>" (e.g. "Rs 90/kg", "33.00/100g", "Rs. 5 per
# litre"). Deliberately requires BOTH a digit amount AND the per-unit
# suffix immediately together, unlike _mrp_amounts (a bare amount alone
# can't distinguish a unit sale price from any other number on the label)
# and unlike a bare "/kg"/"/l" substring (see ALIASES["unit_sale_price"]'s
# comment above for the real false-match this replaced). Used both as
# REGEX_ALIASES's detection pattern below AND as _unit_sale_price_result's
# own amount-extraction pattern - one bounded pattern, not two that could
# drift apart.
_UNIT_PRICE_AMOUNT_PATTERN = re.compile(
    r"(?:₹|Rs\.?|INR)?\s*([0-9]+(?:[.,][0-9]{1,2})?)\s*(?:/|per\s+)\s*"
    r"(?:100\s*g|100\s*ml|kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b",
    re.I,
)

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
        # \bmade\s+in\b - formerly "made in" (a literal-space plain ALIASES
        # entry). The audit found it substring-matches inside "Homemade
        # Ingredients"/"Homemade Indian Recipe" ("homemade" ends in
        # "...made", fused with no boundary to " in..." that follows) - a
        # plausible real snack-packaging phrase, not contrived. \b before
        # "made" rejects this the same way \borigin\b above already rejects
        # "Original" - no word boundary exists between "home" and "made"
        # inside the fused word "homemade".
        re.compile(r"\bmade\s+in\b", re.I),
        # \bmadein\b - real observed fixture wording (Cookie_back.jpg's
        # benchmark OCR text: "MADEIN" - PaddleOCR collapsed the space
        # between "Made" and "In", the same phenomenon already fixed for
        # net_quantity's "NETWEIGHT"). Kept as a SEPARATE pattern from
        # \bmade\s+in\b immediately above, rather than widening that one's
        # \s+ to \s*, so the existing, already-reviewed "made in" protection
        # is never touched - this purely ADDS coverage for the zero-space
        # form. \b on both sides rejects a fused unrelated word the same way
        # \bnet\s?weight\b already rejects "Cabinetweight" (no word boundary
        # exists between two words fused with no space, so this can't match
        # inside some other run-together OCR string that happens to contain
        # "madein" as a substring).
        re.compile(r"\bmadein\b", re.I),
    ],
    # Real observed fixture wording (Handwash_back.jpg's benchmark OCR
    # text: "180 ml Net" - the quantity printed BEFORE the bare "Net"
    # label, not after it as "Net weight"/"net wt"/"net qty"/"net quantity"
    # all assume). \bnet\b requires "net" as its own word (never matches
    # inside "Internet"/"cabinet"), and it only fires when a genuine
    # quantity+unit match - the SAME unit vocabulary _QUANTITY_PATTERN uses,
    # not a new unit system - sits within 15 non-digit characters of it, in
    # either order. This is NOT a bare "net" match: a line with "net"
    # nowhere near a real weight/volume figure never matches, so it cannot
    # mistake gross weight, dimensions, serving size, ingredient quantities,
    # or a promotional number for net quantity (none of those carry the
    # word "net" immediately next to them). "Net Weight: 500 g"/"Net
    # Quantity: 500 g"/"500 g Net Quantity" etc. never reach this fallback -
    # they already match the plain ALIASES phrases above.
    "net_quantity": [
        re.compile(
            r"\bnet\b[^0-9]{0,15}[0-9]+(?:\.[0-9]+)?\s*(?:kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b"
            r"|[0-9]+(?:\.[0-9]+)?\s*(?:kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b[^0-9]{0,15}\bnet\b",
            re.I,
        ),
        # \bnet\s?weight\b - covers BOTH "Net Weight" (spaced, the common
        # case) and the real observed OCR space-collapse (Cookie_back.jpg:
        # "NETWEIGHT:") in one bounded pattern; formerly "net weight" (with
        # a literal space) was also a plain ALIASES entry, but the ALIASES
        # audit (see the report accompanying this change) found it
        # substring-matches inside "Cabinet Weight Capacity" ("cabinet" ends
        # in "...net", fused with no boundary to " weight" that follows) -
        # moved here and word-boundaried instead of removed outright, since
        # "Net Weight" is a real, common, legitimate declaration. \bnet\s?
        # weight\b requires the two words immediately adjacent as one token
        # - never matches "gross weight"/"drained weight"/"serving weight"/
        # "weight per serving" (none of those collapse to "netweight"), and
        # the leading \b also rejects an OCR-collapsed UNRELATED phrase like
        # "Cabinet Weight" -> "Cabinetweight" (no word boundary exists
        # between "Cabi" and "net" inside that fused word, so \bnet never
        # matches there even though the literal substring "netweight" is
        # present).
        #
        # Deliberately KEYWORD-ONLY here - no same-line digit requirement.
        # An earlier version of this pattern required a quantity to follow
        # within 15 characters on the SAME line, which looked safer on
        # paper but broke the real Cookie_back.jpg fixture: its actual OCR
        # output is "NETWEIGHT:" alone on one line, with the value
        # ("75 g (6 units x 12.5 g)") three lines later after multi-column
        # interleaving. Requiring same-line adjacency meant the field was
        # never even marked detected, so the EXISTING nearby-line
        # completion loop (the "for qty_field_name in (...)" loop below -
        # the same mechanism "Net Contents"/"Net Vol." below also rely on)
        # never got a chance to find it. Being keyword-only here, matching
        # the same convention every other net_quantity alias uses, restores
        # that.
        re.compile(r"\bnet\s?weight\b", re.I),
        # \bnet\s+contents\b - formerly "net contents" (a literal-space
        # plain ALIASES entry, added for Pears_back.jpg's real benchmark
        # wording "Net Contents When Packed"). The audit found the same
        # collision shape as "net weight": "Cabinet Contents" ("cabinet"
        # ends in "...net", fused to " contents") would substring-match the
        # plain phrase. Moved here, word-boundaried, same tradeoff as above
        # - keyword-only so the nearby-line completion loop still finds the
        # actual quantity when (as on the real Pears_back.jpg fixture) it
        # sits a few lines away rather than on the same line.
        re.compile(r"\bnet\s+contents\b", re.I),
        # \bnet\s+vol(?:ume)?\b - formerly "net vol" (a literal-space plain
        # ALIASES entry, added for Facewash_back.jpg's real benchmark
        # wording "Net Vol."). Same collision shape again: "Cabinet Volume"
        # ("cabinet" + " volume") would substring-match the plain phrase.
        # Moved here, word-boundaried, keyword-only for the same nearby-line
        # completion reason as above. (?:ume)? makes the "-ume" suffix
        # optional so this covers "Net Vol"/"Net Vol." (abbreviated) and
        # "Net Volume" (spelled out) with one pattern, same coverage the
        # plain "net vol" substring used to provide.
        re.compile(r"\bnet\s+vol(?:ume)?\b", re.I),
    ],
    # The plain ALIASES entry above only covers the safe multi-word phrase
    # "unit sale price" - every SLASH-based per-unit notation ("/kg", "/g",
    # "/litre", "/l", "/ml", "/100g", ...) now lives here instead, bounded
    # to a real digit-amount context via _UNIT_PRICE_AMOUNT_PATTERN (see its
    # own comment above) rather than a bare substring - this is also why
    # "₹0.38 per g"/"Rs 0.38 per g" (space-separated, not slash-separated)
    # is recognized too: one pattern covers both "/" and "per" forms, so
    # there is no separate narrower "per"-only pattern to keep in sync with
    # it.
    "unit_sale_price": [
        _UNIT_PRICE_AMOUNT_PATTERN,
        # \bprice\s+per\b - formerly "price per" (a literal-space plain
        # ALIASES entry). The audit found it substring-matches inside
        # "Special Price Period"/"Reduced Price Period" ("price" fused with
        # no boundary to " per..." from "period") - a plausible promotional-
        # sticker phrase. \b after "per" rejects "period" (no word boundary
        # between "per" and "iod" inside that word), while still matching
        # genuine "price per kg"/"price per unit" (where "per" is a real,
        # separate, space-bounded word).
        re.compile(r"\bprice\s+per\b", re.I),
    ],
    # "rs." / "rs " were REMOVED from the plain ALIASES list above (audit
    # finding: substring-matches inside "hours."/"years."/"colors."/
    # "flavors." - any word ending in "...rs." followed by a period, all
    # plausible label text). \brs(?:\.|\s) requires "rs" to start its own
    # word (no boundary exists between "hou"/"yea" and "rs" inside those
    # fused words), immediately followed by either a period or whitespace -
    # covers "Rs." and "Rs 50" (space, no period), the exact same two
    # spellings the two removed plain aliases covered between them.
    "maximum_retail_price_mrp": [
        re.compile(r"\brs(?:\.|\s)", re.I),
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

# The ruleset's own mrp_format.text_requirement (rules/legal_metrology_
# rules_2011.json, LMPC-R6): "Must state 'Maximum Retail Price' or 'MRP'
# and include all taxes." - the "MRP"/"Maximum Retail Price" half is
# already enforced by _mrp_result's has_label check; this pattern covers
# the "include all taxes" half. Tolerant of the real wording variants
# already present in this project's own fixtures (all three real/realistic
# captures used across this test suite phrase it slightly differently):
# "(INCL. OF ALL TAXES)" (Britannia/Parle-G real OCR text),
# "Inclusive of all taxes" (IMAGE_A_BYTES), "Incl. of all taxes" (the real
# captured synthetic_label_ocr.json fixture).
_MRP_TAX_INCLUSIVE_PATTERN = re.compile(r"incl(?:usive|\.)?\s*(?:of\s*)?all\s*taxes", re.I)

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


# --- OCR-corrupted "NET WEIGHT" (e.g. real benchmark fixture Ferrero_back.
# jpg, glare-damaged: "COSETWEIGHT" instead of "NET WEIGHT") -------------
#
# Deliberately NOT recovered with fuzzy/edit-distance matching. Considered
# and rejected: matching a bare "weight" substring inside a corrupted token
# is not safe here, because "weight" alone cannot distinguish NET weight
# from GROSS weight, TARE weight, or DRAINED weight - all real, legally
# distinct declarations this project must never conflate with net_quantity
# (see this task's explicit "do not confuse net quantity / gross weight"
# requirement). A fuzzy match aggressive enough to reconstruct "net" from
# "coset" would be exactly the kind of aggressive fuzzy matching this task
# explicitly forbids, and would risk turning an unrelated corrupted word
# into a fabricated quantity on some other label. Since neither "net" nor
# any of net_quantity's ALIASES/REGEX_ALIASES survive this specific
# corruption, the field is correctly left undetected here, which
# _field_result already turns into NEEDS_MANUAL_VERIFICATION - not a
# fabricated value, and not silently dropped either (still visible as a
# review item). If a future fixture shows a more targeted, structurally
# safe recovery (e.g. a corruption pattern specific enough to rule out
# gross/tare/drained weight), it belongs here - not a general-purpose
# fuzzy matcher.


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


# Used only to decide whether an already-matched country_of_origin line is
# "keyword-only" (needs nearby-line completion, see normalize_ocr_result
# below) or already carries its own country name. Deliberately excludes
# "product of india" - that trigger phrase already contains the country
# name itself, so stripping it would wrongly read as an empty remainder.
# \s* (not \s+) so this also recognizes the OCR-collapsed "madein" form
# without needing a second copy of this pattern.
_COUNTRY_KEYWORD_STRIP_PATTERN = re.compile(r"country\s+of\s+origin|\borigin\b|\bmade\s*in\b", re.I)

# A candidate line accepted as a country-of-origin completion value:
# SINGLE WORD only (no internal space/punctuation at all), 2-20 letters.
# Deliberately narrower than "any short alphabetic phrase" - an earlier
# version of this pattern allowed spaces/periods/hyphens (to cover
# multi-word names like "United States"), but that also accepted an
# unrelated multi-word line like "Some Unrelated Line" sitting between the
# keyword and the real country name, which is exactly the "arbitrary
# nearby text becomes the country" failure this mechanism must avoid. The
# only real evidence this project has (Cookie_back.jpg: "INDIA") is a
# single word; every other real country name relevant to Indian import/
# export labels observed or plausible here (Italy, USA, China, Nepal,
# Bangladesh, ...) is also one word. This is a SHAPE check standing in for
# a country name whitelist (this project has never hardcoded one and this
# does not start now), deliberately narrow enough that it cannot mistake
# an ordinary sentence fragment for a country - see the completion loop's
# own comment for the full reasoning. A genuine multi-word country name
# sitting on its own nearby line is a known, accepted limitation, not
# silently mishandled: it simply leaves the field at its keyword-only
# value rather than fabricating a wrong one.
_COUNTRY_CANDIDATE_PATTERN = re.compile(r"^[A-Za-z]{2,20}$")


# LMPC-R24 (rules/legal_metrology_rules_2011.json) requires a wholesale
# package to declare the "total number of retail packages" it contains -
# this is a PACKAGE-COUNT declaration ("Contains 24 retail packages",
# "No. of Packages: 12", "Pack of 20"), not a weight/volume measurement, so
# _QUANTITY_PATTERN/_parse_net_quantity (grams/millilitres only) can never
# recognize it. total_number_of_retail_packages_or_net_quantity's own field
# name ("...OR_net_quantity") already acknowledges the ruleset accepts
# EITHER form - the field is reused for retail net-quantity display in the
# e_commerce profile (see ALIASES) and for this wholesale count in the
# LMPC-R24 profile. Deliberately does NOT match a bare "units" suffix,
# which would risk false-matching unrelated "unit sale price" wording.
_PACKAGE_COUNT_PATTERN = re.compile(
    r"pack(?:s)?\s+of\s+([0-9]+)"
    r"|(?:no\.?|number)\s*of\s*(?:retail\s+)?packages?\s*[:\-]?\s*([0-9]+)"
    r"|([0-9]+)\s*(?:x\s*)?(?:retail\s+)?(?:packages?|packs?|pouches?)\b",
    re.I,
)


def _parse_package_count(value: str) -> int | None:
    """Return the declared count of retail packages, or None if `value`
    does not contain recognizable package-count wording. Deliberately
    separate from _parse_net_quantity - a package count is not a
    weight/volume and must never be treated as one (or vice versa)."""
    match = _PACKAGE_COUNT_PATTERN.search(value or "")
    if not match:
        return None
    group = next((g for g in match.groups() if g), None)
    return int(group) if group else None


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
#
# Deliberately NOT recovered here (real benchmark fixture, DarkFantasy_
# back.jpg): a CONSUMER CARE OFFICE address ("FOR FEEDBACK/COMPLAINT
# CONTACT: CONSUMER CARE OFFICE, ITC LIMITED, ITC GREEN CENTRE, ...") that
# happens to name a company. Verified via the fixture's complete OCR text
# that no manufacturer/packer/importer/marketer/distributor role stem
# ("manufactured by", "packed by", "mfg by", ...) appears ANYWHERE in it -
# the ONLY entity information present is this consumer-care contact
# address. Treating "a company name appears near 'consumer care'" as
# manufacturer evidence would be exactly the kind of unbounded inference
# this project's own rules forbid (see this task's explicit "do not infer
# manufacturer merely because a company name appears" requirement) and
# would risk turning ANY consumer-care office mention into a fabricated
# manufacturer declaration on unrelated future labels. This field
# correctly stays NEEDS_MANUAL_VERIFICATION for this fixture - not a
# fabricated PASS, and not a bug: the declaration this rule requires
# genuinely does not appear in the available OCR text.
_BY_OR_FOR = r"(?:by|8y|for)"
_BY_ONLY = r"(?:by|8y)"
_CONNECTOR = r"(?:\s+\S+){0,2}"

_ROLE_HEADING_PATTERNS: dict[str, re.Pattern] = {
    # "\bfor\s+mfg\.?\s+unit\s+see\b" added (real observed fixture wording,
    # Pears_back.jpg's benchmark OCR text: "FOR MFG. UNIT SEE THE FIRST
    # CHARACTER(S) OF THE CODE FOLLOWING [symbol] BELOW. D) HINDUSTAN
    # UNILEVER LTD., ...") - a real, standardized multi-plant labeling
    # convention (large FMCG manufacturers with several factories print
    # this exact instruction, keying each factory to a code letter/symbol
    # printed elsewhere on the batch stamp) - not a contrived phrase. This
    # is the REVERSED word order the other alternatives above don't cover:
    # "for" comes BEFORE the role stem ("for mfg... see"), not after it
    # ("mfg... by/for"), the same class of reordering issue already fixed
    # for net_quantity's "500 g Net"/"180 ml Net". Deliberately narrow -
    # requires the specific "unit see" continuation, not bare "for"+"mfg"
    # in any order (so "FOR MFG DATE SEE BELOW"/"MFG DATE: 01/2024" never
    # match - those are date stamps, not a manufacturer declaration).
    "manufacturer": re.compile(
        rf"\bmanufact(?:u|ij)r\w*{_CONNECTOR}\s+{_BY_OR_FOR}\b|\bmf[dgr]{_CONNECTOR}\s+{_BY_OR_FOR}\b"
        r"|\bfor\s+mfg\.?\s+unit\s+see\b",
        re.I,
    ),
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
    rf"^distribut(?:ed|or)?{_CONNECTOR}\s+{_BY_ONLY}\s*[:\-]?\s*$|"
    # Matches ONLY the exact "for mfg. unit see..." descriptive sentence
    # (see _ROLE_HEADING_PATTERNS["manufacturer"]'s comment) with nothing
    # but an optional short trailing symbol/placeholder character after
    # it (\S{0,3} - the real fixture's OCR read a printed mark as a single
    # stray Unicode character here) - i.e. no real company name is on this
    # line. Deliberately anchored at the END too (unlike the detection
    # trigger above, which only needs to locate the line): once a
    # continuation IS found and appended (see _resolve_entity_continuation),
    # the joined value is LONGER than this exact sentence, so this
    # anchored pattern correctly stops matching and _manufacturer_result
    # treats the value as confirmed (PASS-eligible) rather than perpetually
    # heading-only.
    r"^for\s+mfg\.?\s+unit\s+see\s+the\s+first\s+character\(s?\)\s+of\s+the\s+code\s+following\b\s*\S{0,3}\s*$",
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
    r"(?:consumer|customer)\s+care|customer\s+service|helpline|toll\s*free|contact\s+us"
    # Real observed fixture wording (Cookie_back.jpg's benchmark OCR text:
    # "For Queries or Feedback:" ... phone ... email) - a common, clean,
    # legitimate alternative to a "Consumer Care"/"Helpline" heading this
    # trigger previously had no coverage for at all. Requires the FULL
    # specific phrase "queries or feedback" together (with an optional
    # leading "for") - deliberately NOT a bare "queries"/"feedback"/"call"/
    # "email" alias, any of which would be far too broad and risk matching
    # unrelated marketing copy ("we welcome your feedback on our new
    # flavour") that has nothing to do with a consumer-care declaration.
    r"|(?:for\s+)?queries\s+or\s+feedback",
    re.I,
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

# Accuracy Fix #4: recognizes a "CONSUMER" / "CARE OFFICE" heading SPLIT
# across two OCR lines by multi-column interleaving - real observed
# fixture wording (DarkFantasy_back.jpg's benchmark OCR text: line
# "FOR FEEDBACK/COMPLAINT CONTACT: CONSUMER", then an UNRELATED "Use By:"/
# date declaration interleaved from a different column, then "CARE OFFICE,
# ITC LIMITED, ITC GREEN CENTRE" three lines later) - _CARE_TRIGGER_PATTERN
# above only ever checks ONE line at a time and can never bridge this.
#
# Deliberately narrow, NOT a "search the document for CONSUMER/CARE and
# combine arbitrary lines" mechanism:
#   - the FIRST line must END with "consumer" (\bconsumer\s*$) - a bare
#     mid-sentence mention ("designed for the modern consumer durables")
#     never qualifies, only a line where "consumer" is the trailing,
#     heading-shaped word - exactly how the real fixture's line ends.
#   - the SECOND line must START with "care" immediately followed by a
#     real office-designation word (office/cell/department/desk/division/
#     team/centre/center) - deliberately NOT bare "care" alone, which
#     would also match unrelated real phrasing like "Care Instructions:"/
#     "Care should be taken while opening"/"Careful handling required".
#   - the two must be within _CARE_SPLIT_HEADING_WINDOW lines of each
#     other - a small, dedicated window (not the generic NEARBY_LINE_WINDOW,
#     which is wider and used for unrelated purposes elsewhere), sized to
#     the exact 3-line real gap plus a small margin, not an unbounded scan.
# Both conditions must hold together, so a company name, an address block,
# or an unrelated "CONSUMER"/"CARE" mention alone never triggers this on
# its own - see this fix's negative tests for the specific collisions this
# was checked against.
_CARE_SPLIT_FIRST_LINE_PATTERN = re.compile(r"\bconsumer\s*$", re.I)
_CARE_SPLIT_SECOND_LINE_PATTERN = re.compile(r"^care\s+(?:office|cell|department|desk|division|team|centre|center)\b", re.I)
_CARE_SPLIT_HEADING_WINDOW = 4


def _find_split_care_heading(lines: list[str]) -> tuple[int, int] | None:
    """Return (consumer_line_index, care_line_index) if a split "CONSUMER"
    / "CARE ..." heading is found within _CARE_SPLIT_HEADING_WINDOW lines,
    else None. The CARE line's index is what _care_block/_care_contact
    should anchor on - that is where the real address/contact content
    actually begins (the intervening lines, like the real fixture's
    "Use By:" date, are a different, unrelated declaration column-
    interleaved between the two heading fragments - anchoring on the
    CONSUMER line instead would make _care_block's own boundary detection
    stop on that unrelated declaration before ever reaching the genuine
    address text). The CONSUMER line's index is kept too, purely so the
    caller can include its real text in the evidence trail. Only called as
    a fallback when _CARE_TRIGGER_PATTERN (single-line) finds nothing -
    see normalize_ocr_result below."""
    for index, line in enumerate(lines):
        if not _CARE_SPLIT_FIRST_LINE_PATTERN.search(line.strip()):
            continue
        for offset in range(index + 1, min(len(lines), index + 1 + _CARE_SPLIT_HEADING_WINDOW)):
            if _CARE_SPLIT_SECOND_LINE_PATTERN.match(lines[offset].strip()):
                return index, offset
    return None


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


def _matching_detection_indices(ocr_result: dict[str, Any], matched_text: str | None) -> list[int]:
    """The ONE canonical text->OCR-detection matcher this whole module (and
    backend/services/structured_extraction.py, which reuses this instead of
    keeping its own copy - see that module's _region_ids_for_text/
    _line_confidence) builds on. Returns the indices into
    `ocr_result["detections"]` whose own text is evidence for
    `matched_text` - the SAME "OCR detection -> region_<index>" addressing
    structured_extraction.py's evidence_region_ids already established, so
    a field's region ids here and in StructuredExtraction always agree for
    the same underlying OCR result.

    Matches in either direction (a detection's own text is a substring of
    `matched_text`, or vice versa) so this works both for a field resolved
    from exactly one OCR line (e.g. net quantity) and one resolved by
    joining several (e.g. MRP's keyword line + a nearby amount line, or a
    multi-line entity/consumer-care block) - every OCR line that
    contributed to the field's final value is included, and only those.
    Empty list (never invented) if `matched_text` is empty or nothing matches.
    """
    if not matched_text:
        return []
    needle = matched_text.strip().lower()
    if not needle:
        return []
    detections = ocr_result.get("detections") or []
    indices: list[int] = []
    for index, det in enumerate(detections):
        det_text = str(det.get("text", "")).strip().lower()
        if det_text and (det_text in needle or needle in det_text):
            indices.append(index)
    return indices


def _field_confidence(ocr_result: dict[str, Any], matched_text: str | None, fallback: float) -> float:
    """Confidence from the SPECIFIC OCR detection line(s) that actually
    support `matched_text`, not the whole-page average `fallback` - the
    single_region/whole-page path's counterpart to `_region_confidence`
    above (which already does the equivalent for the currently-inactive
    multi_region/YOLO path). Reuses `ocr_result["detections"]`, which is
    already in memory from this same OCR call - no additional OCR or model
    inference of any kind.

    Conservative, explicitly documented fallback: if no detection line can
    be matched (see _matching_detection_indices), this returns `fallback`
    (the page-wide average) rather than inventing a number with false
    precision - exactly the same "don't guess" principle this engine
    already applies to values, now applied to confidence too.

    Thin convenience wrapper for callers that only need the confidence
    number, not evidence regions - see _field_evidence below for the
    combined, single-pass version normalize_ocr_result actually uses.
    """
    indices = _matching_detection_indices(ocr_result, matched_text)
    if not indices:
        return fallback
    detections = ocr_result.get("detections") or []
    scores = [float(detections[i].get("confidence", 0)) for i in indices]
    return round(sum(scores) / len(scores), 4) if scores else fallback


def _field_evidence(
    ocr_result: dict[str, Any], matched_text: str | None, fallback_confidence: float,
) -> tuple[float, list[str], list["RegionEvidence"]]:
    """Combined confidence + evidence-region lookup in ONE pass over
    `ocr_result["detections"]` (avoids matching twice per field - once for
    confidence, once for regions - which calling _field_confidence and a
    separate region lookup independently would do). Returns
    (confidence, region_ids, regions).

    `region_ids`/`regions` are empty (never invented - see this task's
    explicit "if a compliance field has no reliable evidence region:
    region_ids = [], regions = []") when _matching_detection_indices finds
    nothing, exactly mirroring _field_confidence's fallback-to-page-average
    policy for the confidence half of this same lookup.
    """
    indices = _matching_detection_indices(ocr_result, matched_text)
    if not indices:
        return fallback_confidence, [], []
    detections = ocr_result.get("detections") or []
    scores: list[float] = []
    region_ids: list[str] = []
    regions: list[RegionEvidence] = []
    for index in indices:
        det = detections[index]
        confidence = float(det.get("confidence", 0))
        scores.append(confidence)
        region_id = f"region_{index}"
        region_ids.append(region_id)
        bbox = det.get("bbox") or [0, 0, 0, 0]
        regions.append(RegionEvidence(
            region_id=region_id, bbox=[int(v) for v in bbox],
            text=str(det.get("text", "")), confidence=confidence,
        ))
    field_confidence = round(sum(scores) / len(scores), 4) if scores else fallback_confidence
    return field_confidence, region_ids, regions


def _yolo_region_evidence(class_name: str, region: dict[str, Any]) -> tuple[list[str], list[RegionEvidence]]:
    """Evidence for the (currently inactive - no trained multi-class YOLO
    checkpoint exists yet, see training/README.md) multi_region path: ONE
    YOLO-detected declaration region per class, addressed as "yolo_<class>"
    - deliberately a different ID scheme from single_region's
    "region_<detection index>" (see _matching_detection_indices), since
    this isn't an index into ocr_result["detections"] at all, it's a whole
    YOLO-cropped region. bbox is converted from RegionOCRResult's
    BoundingBox object (x1/y1/x2/y2) to the same flat [x1,y1,x2,y2] list
    used everywhere else, never inventing coordinates - straight from the
    region's own bbox as already produced by YOLOService."""
    bbox = region.get("bbox") or {}
    region_id = f"yolo_{class_name}"
    return [region_id], [RegionEvidence(
        region_id=region_id,
        bbox=[int(bbox.get("x1", 0)), int(bbox.get("y1", 0)), int(bbox.get("x2", 0)), int(bbox.get("y2", 0))],
        text=str(region.get("full_text", "")),
        confidence=_region_confidence(region),
    )]


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
    region_ids, regions = _yolo_region_evidence("consumer_care", region)
    fields["consumer_care_details"] = NormalizedField(
        value={
            "name": None,
            "address": text,
            "telephone_number": phone.group(0) if phone else None,
            "email_address": email.group(0) if email else None,
        },
        detected=True,
        confidence=confidence,
        region_ids=region_ids, regions=regions,
    )


def _apply_mrp_region(fields: dict[str, NormalizedField], region: dict[str, Any]) -> None:
    """Populate maximum_retail_price_mrp from an isolated mrp region's own
    OCR text. `_mrp_result` (in _rule_result below) does the actual
    amount/wording validation against whatever text ends up in this field's
    `value` - unchanged either way, so populating it from a region instead
    of a whole-page regex match requires no changes there."""
    text = str(region.get("full_text", "")).strip()
    region_ids, regions = _yolo_region_evidence("mrp", region) if text else ([], [])
    fields["maximum_retail_price_mrp"] = NormalizedField(
        value=text or None, detected=bool(text), confidence=_region_confidence(region),
        region_ids=region_ids, regions=regions,
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
    region_ids, regions = _yolo_region_evidence("mfg_date_batch", region) if text else ([], [])
    fields["month_and_year_of_manufacture_or_packing"] = NormalizedField(
        value=text or None, detected=bool(text), confidence=confidence,
        region_ids=region_ids, regions=regions,
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
            region_ids, region_evidence = _yolo_region_evidence(class_name, region) if text else ([], [])
            for field_name in YOLO_CLASS_TO_FIELDS[class_name]:
                fields[field_name] = NormalizedField(
                    value=text or None, detected=bool(text), confidence=confidence,
                    region_ids=region_ids, regions=region_evidence,
                )
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
        _confidence, _region_ids, _regions = _field_evidence(ocr_result, matched_line, average_confidence)
        fields[field_name] = NormalizedField(
            value=matched_line, detected=match is not None,
            confidence=_confidence, region_ids=_region_ids, regions=_regions,
        )

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
        _identity_confidence, _identity_region_ids, _identity_regions = _field_evidence(ocr_result, identity_value, average_confidence)
        for identity_field in ("common_generic_name_of_commodity", "identity_of_commodity"):
            if not fields[identity_field].detected:
                fields[identity_field] = NormalizedField(
                    value=identity_value, detected=True,
                    confidence=_identity_confidence * 0.75,
                    region_ids=_identity_region_ids, regions=_identity_regions,
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
    #
    # total_number_of_retail_packages_or_net_quantity additionally accepts a
    # PACKAGE-COUNT candidate (_parse_package_count), not just a weight/
    # volume one - a wholesale package (LMPC-R24) declares "N retail
    # packages", never a gram/millilitre figure, so treating it as ordinary
    # net-quantity wording would silently drop a genuine wholesale
    # declaration. net_quantity itself is untouched: it never accepts a
    # package-count match, since a retail package's net quantity is always
    # a weight/volume, not a count.
    for qty_field_name in ("net_quantity", "total_number_of_retail_packages_or_net_quantity"):
        qty_field = fields[qty_field_name]
        is_wholesale_count_field = qty_field_name == "total_number_of_retail_packages_or_net_quantity"
        already_resolved = _parse_net_quantity(str(qty_field.value)) or (
            is_wholesale_count_field and _parse_package_count(str(qty_field.value))
        )
        if not qty_field.detected or not qty_field.value or already_resolved:
            continue
        qty_index = next((index for index, line in enumerate(lines) if line == qty_field.value), None)
        if qty_index is None:
            continue
        for offset, candidate in enumerate(lines[qty_index + 1: qty_index + 1 + NEARBY_LINE_WINDOW], start=qty_index + 1):
            candidate_matches = _parse_net_quantity(candidate) or (is_wholesale_count_field and _parse_package_count(candidate))
            if not candidate_matches or _in_nutrition_table_context(lines, offset):
                continue
            joined_value = f"{qty_field.value} {candidate}"
            _qty_confidence, _qty_region_ids, _qty_regions = _field_evidence(ocr_result, joined_value, average_confidence)
            fields[qty_field_name] = NormalizedField(
                value=joined_value, detected=True,
                confidence=_qty_confidence, region_ids=_qty_region_ids, regions=_qty_regions,
            )
            break

    # country_of_origin: same interleaving problem as net_quantity above,
    # same fix shape - the plain ALIASES/REGEX_ALIASES match frequently
    # lands on a keyword-only line ("MADEIN", "Country of Origin") with the
    # actual country name printed on a separate, nearby line. Real observed
    # fixture wording (Cookie_back.jpg's benchmark OCR text: "MADEIN" /
    # "INDIA" on two separate lines, several lines apart after nutrition-
    # table interleaving).
    #
    # "already resolved" means the matched line has content BEYOND the bare
    # trigger phrase itself - "Origin: India"/"Made in India" already carry
    # their own country name and must never be touched. "product of india"
    # is deliberately excluded from this strip check (unlike "country of
    # origin"/"origin"/"made in") because the country name is INSIDE that
    # trigger phrase itself - stripping it would wrongly look like an empty,
    # keyword-only remainder and trigger an unwanted, unnecessary completion
    # attempt on an already-complete value.
    #
    # A candidate is accepted only if it is a SINGLE WORD (see
    # _COUNTRY_CANDIDATE_PATTERN's own comment for why this is deliberately
    # narrower than "any short alphabetic phrase" - that broader shape also
    # accepted an ordinary unrelated sentence fragment sitting between the
    # keyword and the real country name). Also rejected if it matches
    # _NUTRITION_ROW_LABEL_PATTERN or sits in nutrition-table context - a
    # bare nutrition-table row label (e.g. "SODIUM"/"PROTEIN") has the exact
    # same single-word shape as a country name, and nutrition tables are a
    # common source of exactly this kind of false candidate - the same
    # protection net_quantity's own completion loop above already relies on
    # for the identical reason. Deliberately NOT a general "search the
    # whole document" mechanism and NOT a hardcoded country name list -
    # bounded to the existing NEARBY_LINE_WINDOW, and the shape check
    # (single word, no digits) is the only thing standing in for a country
    # whitelist, so it can accept a country this project has never seen
    # without inventing one.
    country_field = fields["country_of_origin"]
    if country_field.detected and country_field.value:
        country_remainder = _COUNTRY_KEYWORD_STRIP_PATTERN.sub("", str(country_field.value)).strip(" :-\n")
        if not country_remainder:
            country_index = next((index for index, line in enumerate(lines) if line == country_field.value), None)
            if country_index is not None:
                for offset, candidate in enumerate(lines[country_index + 1: country_index + 1 + NEARBY_LINE_WINDOW], start=country_index + 1):
                    stripped_candidate = candidate.strip()
                    if (
                        not _COUNTRY_CANDIDATE_PATTERN.match(stripped_candidate)
                        or _NUTRITION_ROW_LABEL_PATTERN.search(stripped_candidate)
                        or _in_nutrition_table_context(lines, offset)
                    ):
                        continue
                    joined_value = f"{country_field.value} {stripped_candidate}"
                    _country_confidence, _country_region_ids, _country_regions = _field_evidence(ocr_result, joined_value, average_confidence)
                    fields["country_of_origin"] = NormalizedField(
                        value=joined_value, detected=True,
                        confidence=_country_confidence, region_ids=_country_region_ids, regions=_country_regions,
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
        entity_confidence, entity_region_ids, entity_regions = _field_evidence(ocr_result, combined_entity_value, average_confidence)
        for entity_field_name in ("manufacturer_packer_importer_details", "name_and_address_of_manufacturer_or_packer"):
            fields[entity_field_name] = NormalizedField(
                value=combined_entity_value, detected=True, confidence=entity_confidence,
                region_ids=entity_region_ids, regions=entity_regions,
            )
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
        # The "inclusive of all taxes" qualifier (rules/legal_metrology_
        # rules_2011.json's mrp_format.text_requirement) is frequently
        # printed as its OWN interleaved OCR line, not on the same line as
        # the amount - confirmed on both real fixtures this project already
        # has: Britannia prints "MRP." / "(INCL. OF ALL TAXES)" / "20.00"
        # as three separate lines with an unrelated line between "MRP."
        # and the tax wording; Parle-G's tax wording sits 8 OCR lines after
        # "MRP.10.00", further than NEARBY_LINE_WINDOW would reach. Unlike
        # the amount search above (intentionally windowed - a bare number
        # is ambiguous enough that a wide search risks grabbing an
        # unrelated one, e.g. from a nutrition table), this phrase is
        # specific enough that a false match anywhere else on a real label
        # is not a realistic concern, so the whole page is searched once
        # rather than only a fixed window - still a single cheap pass over
        # already-in-memory OCR lines, not a new field of uncertainty.
        if not _MRP_TAX_INCLUSIVE_PATTERN.search(mrp_line):
            tax_line = next((line for line in lines if _MRP_TAX_INCLUSIVE_PATTERN.search(line)), None)
            if tax_line and tax_line not in mrp_line:
                mrp_line = f"{mrp_line} {tax_line}"
    if mrp_line:
        _mrp_confidence, _mrp_region_ids, _mrp_regions = _field_evidence(ocr_result, mrp_line, average_confidence)
        fields["maximum_retail_price_mrp"] = NormalizedField(
            value=mrp_line, detected=True, confidence=_mrp_confidence,
            region_ids=_mrp_region_ids, regions=_mrp_regions,
        )
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
    if date_value:
        _date_confidence, _date_region_ids, _date_regions = _field_evidence(ocr_result, date_value, average_confidence)
        fields["month_and_year_of_manufacture_or_packing"] = NormalizedField(
            value=date_value, detected=True, confidence=_date_confidence,
            region_ids=_date_region_ids, regions=_date_regions,
        )
    else:
        fields["month_and_year_of_manufacture_or_packing"] = NormalizedField()

    care_start = _find_care_start(lines)
    consumer_line_for_evidence = None
    if care_start is None:
        # Fallback: a "CONSUMER" / "CARE <office-word>..." heading split
        # across two lines by column interleaving (see
        # _find_split_care_heading's own docstring) - only tried when the
        # single-line trigger above found nothing at all.
        split_match = _find_split_care_heading(lines)
        if split_match is not None:
            consumer_index, care_start = split_match
            consumer_line_for_evidence = lines[consumer_index]
    if care_start is not None:
        care_block_raw = _care_block(lines, care_start)
        # A split match's own line ("CARE OFFICE, ITC LIMITED, ...") never
        # starts with "consumer"/"customer" (that word is on the OTHER,
        # earlier line), so _care_name would find nothing on it alone -
        # reusing it unchanged against a synthesized "Consumer " + line
        # string instead correctly yields "Consumer Care" (the specific
        # office-designation word, e.g. "Office", falls outside
        # _CARE_NAME_PATTERN's own suffix list and is simply dropped, which
        # is fine - "Consumer Care" alone is still an accurate, non-invented
        # name for this declaration).
        name = _care_name(("Consumer " + lines[care_start]) if consumer_line_for_evidence is not None else lines[care_start])
        # The last alternative (bare "care <office-word>") only ever
        # matches a split-heading block, whose first line is "CARE OFFICE,
        # ..." with no "consumer"/"customer" prefix of its own (that word
        # is on the earlier, separate line) - strips it the same way the
        # existing alternatives already strip "consumer care"/"toll free"/
        # etc. from a single-line block, so the address value stays a
        # clean address instead of redundantly starting with "Care Office,"
        # on top of the already-separate `name` field.
        care_text = re.sub(r"^(?:consumer|customer)\s+care|customer\s+service|helpline|toll\s*free|contact\s+us\s*[:-]?|^care\s+(?:office|cell|department|desk|division|team|centre|center)", "", care_block_raw, flags=re.I).strip(" :-\n")
        phone, email = _care_contact(lines, care_start)
        # Confidence evidence: the raw (pre-strip) block PLUS phone/email,
        # since _care_contact searches a wider window than _care_block and
        # can find a phone/email on a line the block itself doesn't
        # include - using care_block_raw (not the stripped `care_text`)
        # also avoids losing the trigger line's own text (e.g. "Consumer
        # Care:") as a match anchor for whichever detection line it came from.
        # `consumer_line_for_evidence` (only set for a split match) is
        # included too, so the evidence trail covers BOTH real OCR lines
        # that together justify this detection, not just the second half.
        care_evidence = "\n".join(filter(None, [consumer_line_for_evidence, lines[care_start], care_block_raw, phone, email]))
        _care_confidence, _care_region_ids, _care_regions = _field_evidence(ocr_result, care_evidence, average_confidence)
        fields["consumer_care_details"] = NormalizedField(
            value={
                "name": name,
                "address": care_text or None,
                "telephone_number": phone,
                "email_address": email,
            },
            detected=True,
            confidence=_care_confidence,
            region_ids=_care_region_ids, regions=_care_regions,
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
    # region_ids/regions are copied straight through from `field` in every
    # branch below (never recomputed here - see this task's evidence-
    # propagation requirement: the SAME evidence normalize_ocr_result
    # already resolved via _field_evidence, not a second independent
    # lookup). Empty on an undetected field, exactly as it already is on
    # `field` itself - never invented.
    if not field.detected:
        return FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation=f"{label} was not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.", region_ids=field.region_ids, regions=field.regions)
    if field.confidence < CONFIDENCE_THRESHOLD:
        return FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=field.value, confidence=field.confidence, explanation=f"{label} was detected with low OCR confidence.", region_ids=field.region_ids, regions=field.regions)
    return FieldResult(status="PASS", value=field.value, confidence=field.confidence, explanation=f"{label} was detected by OCR.", region_ids=field.region_ids, regions=field.regions)


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
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation=f"{requirement} was not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, validation="No manufacturer/packer/importer/marketer/distributor declaration detected", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    if field.confidence < CONFIDENCE_THRESHOLD:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=field.value, confidence=field.confidence, explanation=f"{requirement} was detected with low OCR confidence.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=str(field.value), validation="Low-confidence OCR", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    value = str(field.value)
    segments = [segment.strip() for segment in value.split(" | ") if segment.strip()]
    if segments and all(_HEADING_ONLY_PATTERN.match(segment) for segment in segments):
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation=f"A {requirement.lower()} declaration heading was detected, but no entity name/address could be confirmed nearby.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Declaration heading present; entity value not confirmed", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    result = FieldResult(status="PASS", value=value, confidence=field.confidence, explanation=f"{requirement} detected with entity details.", region_ids=field.region_ids, regions=field.regions)
    return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Declaration heading and entity value both confirmed", result="PASS", region_ids=field.region_ids, regions=field.regions)


def _mrp_result(field: NormalizedField, requirement: str) -> tuple[FieldResult, Evidence]:
    if not field.detected:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation="MRP was not detected by OCR; physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, validation="No INR amount or MRP label detected", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    if field.confidence < CONFIDENCE_THRESHOLD:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=field.value, confidence=field.confidence, explanation="MRP OCR confidence is below the verification threshold.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=str(field.value), validation="Low-confidence OCR", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    value = str(field.value)
    amounts = _mrp_amounts(value)
    has_label = bool(re.search(r"(?:maximum\s+retail\s+price|m\.?r\.?p\.?)", value, re.I))
    if not amounts:
        if has_label:
            # The explicit "Maximum Retail Price"/"MRP" WORDING was
            # positively identified on this exact line - strong, confirming
            # evidence this genuinely IS the MRP declaration - but no valid
            # amount could be read from it. Deliberately NOT a confirmed
            # FAIL (changed from an earlier version of this function that
            # treated this as a structural defect in the declaration
            # itself): OCR failing to read the numeral specifically - blur,
            # glare, embossed/foil printing, a partly obscured digit - is
            # exactly as plausible a cause as the package genuinely lacking
            # an amount, and this engine has no way to distinguish those
            # two cases from text evidence alone. Treating "wording present,
            # amount unreadable" as a confirmed legal violation risks a
            # false NON_COMPLIANT on a genuinely compliant package whose
            # price was simply hard to photograph - real cases of exactly
            # this were observed during this project's own product-photo
            # collection (see training/rejected_images/QC_LOG.md's notes on
            # embossed/smudged MRP areas). This now matches the SAME
            # "OCR non-detection is not confirmed physical absence"
            # principle _field_result's docstring already applies
            # everywhere else in this module - MRP was the one
            # inconsistent exception, not a deliberately different rule.
            # A value that IS successfully read and is itself invalid still
            # falls through to a confirmed result elsewhere in this
            # function (see the len(amounts) > 1 and has_label branches
            # below) - this change only affects "no amount could be parsed
            # at all", never a value that was read and rejected.
            result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="MRP wording was detected, but no valid amount could be read from the OCR text. This may be an OCR/image-quality issue (e.g. a blurred, glared, or embossed price) rather than a missing declaration, so it requires manual verification rather than being treated as a confirmed violation.", region_ids=field.region_ids, regions=field.regions)
            return result, Evidence(requirement=requirement, ocr_evidence=value, validation="MRP wording present; amount not reliably read from OCR - not confirmed as a structural defect", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
        # No MRP wording confirmed on this line either - the match came from
        # a weaker alias (e.g. a bare "rs."/"₹" substring) that could just as
        # plausibly be an unrelated mention, not confirmed to be the MRP
        # declaration at all. Genuinely ambiguous -> needs manual review,
        # not a confirmed violation.
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="Text resembling a price was detected, but MRP wording was not confirmed on this line and no valid amount could be parsed - too ambiguous to confirm as a valid or invalid MRP declaration.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="INR amount not parseable; MRP wording not confirmed on this line", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    if len(amounts) > 1:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="OCR detected multiple possible MRP values.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Multiple amounts detected", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    if not has_label:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="An amount was detected, but the required MRP wording was not detected.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid amount; MRP wording absent", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    # P1: rules/legal_metrology_rules_2011.json's own mrp_format.
    # text_requirement explicitly states the MRP declaration "Must state
    # 'Maximum Retail Price' or 'MRP' and include all taxes" - the wording
    # half is already confirmed by `has_label` above; this checks the
    # "include all taxes" half specifically (see _MRP_TAX_INCLUSIVE_PATTERN
    # and normalize_ocr_result's MRP block, which already folds a nearby
    # tax-inclusive line into `value` when one exists on the page).
    #
    # Absence here is NOT a confirmed FAIL, deliberately, for the exact
    # same reason MRP's amount-unreadable case above isn't one: this
    # engine has no independent way to confirm the qualifier was truly
    # never printed, versus OCR simply not having captured that specific
    # line while it did capture the amount - the ruleset requirement text
    # itself is CONFIRMED (it's explicit, structured project source, not
    # inferred), but "declaration text not found by OCR" is exactly the
    # kind of evidence gap this engine's uncertainty model (see
    # _field_result's docstring) already treats as low-confidence
    # everywhere else, not as proof of physical absence. Scoring this as a
    # confirmed violation would risk the same false-NON_COMPLIANT failure
    # mode the amount-unreadable fix above was written to prevent.
    if not _MRP_TAX_INCLUSIVE_PATTERN.search(value):
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="A valid MRP amount and MRP wording were detected, but the required \"inclusive of all taxes\" qualifier was not found in the OCR text. This may be an OCR/image-quality issue (the qualifier is often printed smaller than the amount) rather than a missing declaration, so it requires manual verification rather than being treated as a confirmed violation.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid MRP amount and wording; \"inclusive of all taxes\" qualifier not found in OCR text - not confirmed as a structural defect", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
    result = FieldResult(status="PASS", value={"detected_value": value, "normalized_value": float(amounts[0].replace(",", "")), "currency": "INR"}, confidence=field.confidence, explanation="Valid MRP amount, MRP wording, and \"inclusive of all taxes\" qualifier detected.", region_ids=field.region_ids, regions=field.regions)
    return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid MRP amount, keyword, and tax-inclusive wording", result="PASS", region_ids=field.region_ids, regions=field.regions)


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
    0. An explicit unit-sale-price declaration was detected, but no valid
       per-unit amount could be read from it -> NEEDS_MANUAL_VERIFICATION,
       not a confirmed violation (changed - see _mrp_result's matching
       branch for the full reasoning: OCR failing to read the numeral is
       exactly as plausible as the package genuinely lacking one, and this
       engine cannot distinguish the two from text evidence alone). A
       value that IS successfully read and rejected elsewhere in this
       function is unaffected by this change.
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
            # unit_sale_price's aliases are "unit sale price"/"price per"
            # (plain ALIASES) or _UNIT_PRICE_AMOUNT_PATTERN itself
            # (REGEX_ALIASES, see its comment near ALIASES above) - every
            # path here is already specific to a per-unit-price context (no
            # bare "/kg"/"/l" substring risk any more - that false-match was
            # a real observed bug, since fixed). But no valid per-unit
            # amount being readable is NOT necessarily a structural defect
            # in the declaration itself - it is equally consistent with OCR
            # simply failing to read the digits (blur, glare, embossed
            # printing), which this engine cannot distinguish from a
            # genuine absence. Deliberately NOT a confirmed FAIL, for the
            # same reason _mrp_result's equivalent branch was changed - see
            # its comment for the full reasoning and the real observed
            # cases this guards against.
            result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="Unit sale price wording was detected, but no valid per-unit amount could be read from the OCR text. This may be an OCR/image-quality issue rather than a missing declaration, so it requires manual verification rather than being treated as a confirmed violation.", region_ids=field.region_ids, regions=field.regions)
            return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Unit sale price wording present; amount not reliably read from OCR - not confirmed as a structural defect", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
        if field.confidence < CONFIDENCE_THRESHOLD:
            result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", value=value, confidence=field.confidence, explanation="Unit sale price OCR confidence is below the verification threshold.", region_ids=field.region_ids, regions=field.regions)
            return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Low-confidence OCR", result="NEEDS_MANUAL_VERIFICATION", region_ids=field.region_ids, regions=field.regions)
        result = FieldResult(status="PASS", value={"detected_value": value, "normalized_value": float(amount_match.group(1).replace(",", "")), "currency": "INR"}, confidence=field.confidence, explanation="Valid unit sale price amount detected.", region_ids=field.region_ids, regions=field.regions)
        return result, Evidence(requirement=requirement, ocr_evidence=value, validation="Valid unit sale price amount", result="PASS", region_ids=field.region_ids, regions=field.regions)

    quantity_text = str(net_quantity_field.value) if net_quantity_field.detected and net_quantity_field.value else ""
    parsed = _parse_net_quantity(quantity_text)
    if parsed is None:
        result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation="Unit sale price was not detected by OCR, and net quantity could not be parsed to determine whether the retail-price-equals-unit-price proviso applies.")
        evidence_kwargs = {"ocr_evidence": quantity_text} if quantity_text else {}
        return result, Evidence(requirement=requirement, validation="Net quantity not parseable; proviso applicability unknown", result="NEEDS_MANUAL_VERIFICATION", **evidence_kwargs)

    amount, _basis = parsed
    if abs(amount - _UNIT_PRICE_EQUALS_MRP_QUANTITY) < 0.01:
        # The evidence for this PASS is genuinely the NET QUANTITY
        # declaration (that's what makes the proviso apply), not a
        # unit-price line that was never printed - so region_ids/regions
        # here deliberately come from net_quantity_field, not `field`
        # (unit_sale_price, which is undetected in this branch).
        result = FieldResult(
            status="PASS",
            value="Not separately required: net quantity equals 1 kg/1 litre, so the retail selling price (MRP) already equals the unit sale price under the Legal Metrology (Packaged Commodities) Amendment Rules, 2022 proviso.",
            confidence=net_quantity_field.confidence,
            explanation="Unit sale price requirement satisfied via the retail-price-equals-unit-price proviso.",
            region_ids=net_quantity_field.region_ids, regions=net_quantity_field.regions,
        )
        return result, Evidence(requirement=requirement, ocr_evidence=quantity_text, validation="Net quantity = 1 kg/litre; proviso applies", result="PASS", region_ids=net_quantity_field.region_ids, regions=net_quantity_field.regions)

    result = FieldResult(status="NEEDS_MANUAL_VERIFICATION", explanation="Unit sale price was not detected by OCR, and net quantity does not equal 1 kg/1 litre, so the retail-price-equals-unit-price proviso does not apply. Physical absence is not confirmed, so this requires manual verification rather than being treated as a confirmed violation.", region_ids=net_quantity_field.region_ids, regions=net_quantity_field.regions)
    return result, Evidence(requirement=requirement, ocr_evidence=quantity_text, validation="Proviso does not apply; unit sale price required but not detected by OCR (absence unconfirmed)", result="NEEDS_MANUAL_VERIFICATION", region_ids=net_quantity_field.region_ids, regions=net_quantity_field.regions)


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
                fields[name] = FieldResult(status=status, value=current.value, confidence=current.confidence, missing=missing, explanation=explanation, region_ids=current.region_ids, regions=current.regions)
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
        # This rule is deliberately absent from EVERY entry in
        # validation_profiles (rules/legal_metrology_rules_2011.json) - it
        # is reachable here only if a caller evaluates it directly, and even
        # then it degrades safely (see below). total_package_weight and
        # wrapper_packaging_weight are physical SCALE measurements: the
        # sealed package's weight and the empty wrapper's weight after the
        # contents are removed. Nothing in this project's pipeline can
        # produce them from a photograph - not OCR (a label prints the
        # DECLARED net quantity as text, never the two raw weights this
        # rule's exclusion arithmetic needs), not structured_extraction.py,
        # not gemini_service.py's AI fallback (it fills in ambiguous OCR
        # TEXT, it does not weigh anything). Wiring this rule into a profile
        # without that data would not "run the check" - it would only ever
        # produce a NEEDS_MANUAL_VERIFICATION for every single scan, adding
        # a permanently-uncertain rule result with no way to ever resolve
        # it, which is worse than omitting it entirely. If a future
        # integration DOES obtain real scale measurements (e.g. a manual
        # entry form, a connected weighing instrument), populating
        # normalized.metadata["total_package_weight"]/["wrapper_packaging_
        # weight"] and adding "LMPC-R11-NET-QUANTITY-EXCLUSION" to a
        # profile's evaluate_rules is sufficient - this handler already
        # supports that case correctly (see the None-check below) and
        # requires no further code change.
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