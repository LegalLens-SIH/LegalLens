"""T005: Build the Phase 2 benchmark dataset for the Advanced Legal Knowledge
Assistant, from the VERIFIED legal corpus (same corpus 001-legal-rag uses,
read-only here). Every "expected" answer is pulled from the corpus file at run
time - never hardcoded independently of it.

At least as large and adversarially rigorous as 001-legal-rag's own 36-query
Phase 8 benchmark (specs/001-legal-rag/tasks.md, "Phase 8 Evaluation Outcome"),
and extended with two categories that benchmark did not need (this feature's
spec requires them): amendment/history queries and multi-provision "explain
what changed" queries.

Query styles (spec.md's required query shapes + research.md's adversarial
classes):
  - exact_citation:    "What does Rule 6(11) require...?" - names the clause directly.
  - topic_phrased:      natural language, no clause number, no rule_id given.
  - amendment_history:  asks specifically about what changed / which version applies.
  - adversarial_confusable, adversarial_generic, adversarial_multi,
    adversarial_no_result: same four classes as 001-legal-rag's Phase 8, applied
    fresh to this feature's own free-form-question framing (not copy-pasted).

Pure Python, no heavy dependency - run with any Python 3.11 interpreter.
"""
import json
from pathlib import Path

CORPUS_PATH = Path(r"E:\LegalLense-main\LegalLense-main\Portal\backend\legal_corpus\legal_metrology_packaged_commodities_2011.json")
OUT_PATH = Path(__file__).parent / "benchmark_dataset.json"

corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
by_field = {(p["linked_rule_id"], p["linked_field"]): p for p in corpus["provisions"]}
by_id = {p["provision_id"]: p for p in corpus["provisions"]}


def prov(rule_id, field):
    p = by_field[(rule_id, field)]
    return {"provision_id": p["provision_id"], "rule_id": rule_id, "field": field, "clause": p["rule_sub_rule_clause"]}


R6 = "LMPC-R6-MANDATORY-DECLARATIONS"
R24 = "LMPC-R24-WHOLESALE-DECLARATIONS"

ALL_R6_FIELDS = [
    "manufacturer_packer_importer_details", "country_of_origin", "common_generic_name_of_commodity",
    "net_quantity", "month_and_year_of_manufacture_or_packing", "maximum_retail_price_mrp",
    "unit_sale_price", "consumer_care_details",
]
ALL_R24_FIELDS = [
    "name_and_address_of_manufacturer_or_packer", "identity_of_commodity",
    "total_number_of_retail_packages_or_net_quantity",
]

queries = []

# --- exact_citation: one per corpus provision (11), naming the clause directly.
EXACT_CITATION = [
    (R6, "manufacturer_packer_importer_details", "What does Rule 6(1)(a) require about manufacturer or packer details?"),
    (R6, "country_of_origin", "What does Rule 6(1)(aa) require regarding country of origin?"),
    (R6, "common_generic_name_of_commodity", "What does Rule 6(1)(b) require about the common or generic name?"),
    (R6, "net_quantity", "What does Rule 6(1)(c) require about net quantity?"),
    (R6, "month_and_year_of_manufacture_or_packing", "What does Rule 6(1)(d) require about the month and year of manufacture?"),
    (R6, "maximum_retail_price_mrp", "What does Rule 6(1)(e) require about the maximum retail price?"),
    (R6, "unit_sale_price", "What does Rule 6(11) require regarding unit sale price?"),
    (R6, "consumer_care_details", "What does Rule 6(2) require about consumer care details?"),
    (R24, "name_and_address_of_manufacturer_or_packer", "What does Rule 24(a) require about manufacturer name and address?"),
    (R24, "identity_of_commodity", "What does Rule 24(b) require about identity of the commodity?"),
    (R24, "total_number_of_retail_packages_or_net_quantity", "What does Rule 24(c) require about the number of retail packages?"),
]
for rule_id, field, text in EXACT_CITATION:
    p = prov(rule_id, field)
    queries.append({"id": f"exact-{field}", "style": "exact_citation", "query_text": text, "expected": [p]})

# --- topic_phrased: natural language, no clause number, no rule_id.
TOPIC_PHRASED = [
    (R6, "manufacturer_packer_importer_details", "Which provision covers who made or packed a retail product?"),
    (R6, "country_of_origin", "Which provision covers country of origin for an imported product?"),
    (R6, "common_generic_name_of_commodity", "Which provision covers stating what a product actually is, in plain terms?"),
    (R6, "net_quantity", "Which provision covers how much product is inside a retail package?"),
    (R6, "month_and_year_of_manufacture_or_packing", "Which provision covers when a retail product was made or packed?"),
    (R6, "maximum_retail_price_mrp", "Show me the legal provisions related to MRP."),
    (R6, "unit_sale_price", "Which provision requires showing a price per kilogram or per litre?"),
    (R6, "consumer_care_details", "Which provision covers a customer complaint phone number or email?"),
    (R24, "name_and_address_of_manufacturer_or_packer", "Which provision covers manufacturer identity on a bulk/wholesale package?"),
    (R24, "identity_of_commodity", "Which provision requires identifying the commodity on a wholesale package?"),
    (R24, "total_number_of_retail_packages_or_net_quantity", "Which provision covers how many retail units are inside a wholesale carton?"),
]
for rule_id, field, text in TOPIC_PHRASED:
    p = prov(rule_id, field)
    queries.append({"id": f"topic-{field}", "style": "topic_phrased", "query_text": text, "expected": [p]})

# --- amendment_history: asks specifically about change/version, spec's own example query.
AMENDMENT_HISTORY = [
    ("hist-r6-2022", "What changed in Rule 6 after the 2022 amendment?", [prov(R6, "unit_sale_price")]),
    ("hist-mrp-2017", "How did the MRP declaration requirement change in 2017?", [prov(R6, "maximum_retail_price_mrp")]),
    ("hist-country-origin-amend", "Is the country of origin requirement an original 2011 rule or a later amendment?", [prov(R6, "country_of_origin")]),
    ("hist-unit-sale-price-effective-date", "As of today, is the unit sale price declaration requirement actually in force?", [prov(R6, "unit_sale_price")]),
]
for qid, text, expected in AMENDMENT_HISTORY:
    queries.append({"id": qid, "style": "amendment_history", "query_text": text, "expected": expected})

# --- adversarial_confusable: near-duplicate wording across two rules (same class 001-legal-rag's Phase 8 used).
ADVERSARIAL_CONFUSABLE = [
    ("adv-conf-netqty-retail", "What must be declared about net quantity on a RETAIL package sold directly to a consumer?", [prov(R6, "net_quantity")]),
    ("adv-conf-netqty-wholesale", "What must be declared about net quantity on a WHOLESALE package sold to a retailer, not a consumer?", [prov(R24, "total_number_of_retail_packages_or_net_quantity")]),
    ("adv-conf-mfr-retail", "Whose name and address must appear on a RETAIL package?", [prov(R6, "manufacturer_packer_importer_details")]),
    ("adv-conf-mfr-wholesale", "Whose name and address must appear on a WHOLESALE package?", [prov(R24, "name_and_address_of_manufacturer_or_packer")]),
    ("adv-conf-identity-vs-genericname", "For a wholesale package, what counts as identifying the commodity itself?", [prov(R24, "identity_of_commodity")]),
    ("adv-conf-genericname-vs-identity", "For a retail package, what common or generic name must be shown for the product?", [prov(R6, "common_generic_name_of_commodity")]),
    ("adv-conf-price-vs-unit-price", "Is the requirement to show price per kilogram the same as the requirement to show the maximum retail price?", [prov(R6, "unit_sale_price")]),
]
for qid, text, expected in ADVERSARIAL_CONFUSABLE:
    queries.append({"id": qid, "style": "adversarial_confusable", "query_text": text, "expected": expected})

# --- adversarial_generic: vague wording, plausibly multi-matching within one rule.
ADVERSARIAL_GENERIC = [
    ("adv-generic-price", "What are the price declaration requirements for a package?", [prov(R6, "maximum_retail_price_mrp"), prov(R6, "unit_sale_price")]),
    ("adv-generic-labeling", "What labeling requirements apply to a packaged commodity?", [prov(R6, f) for f in ALL_R6_FIELDS]),
    ("adv-generic-wholesale-labeling", "What must a wholesale package's label show?", [prov(R24, f) for f in ALL_R24_FIELDS]),
]
for qid, text, expected in ADVERSARIAL_GENERIC:
    queries.append({"id": qid, "style": "adversarial_generic", "query_text": text, "expected": expected})

# --- adversarial_multi: two distinct declarations mentioned together.
ADVERSARIAL_MULTI = [
    ("adv-multi-origin-mfr", "What are the requirements for both country of origin and manufacturer details together on a retail package?", [prov(R6, "country_of_origin"), prov(R6, "manufacturer_packer_importer_details")]),
    ("adv-multi-price-date", "What must be shown about the maximum retail price and the manufacturing date together?", [prov(R6, "maximum_retail_price_mrp"), prov(R6, "month_and_year_of_manufacture_or_packing")]),
    ("adv-multi-wholesale-identity-qty", "For a wholesale package, what must be shown about both the commodity's identity and the number of retail packages inside?", [prov(R24, "identity_of_commodity"), prov(R24, "total_number_of_retail_packages_or_net_quantity")]),
]
for qid, text, expected in ADVERSARIAL_MULTI:
    queries.append({"id": qid, "style": "adversarial_multi", "query_text": text, "expected": expected})

# --- adversarial_no_result: genuinely uncovered by the corpus - correct answer is refusal.
ADVERSARIAL_NO_RESULT = [
    ("adv-none-expiry", "What is the expiry date or best-before date declaration requirement for a package?", []),
    ("adv-none-verification-interval", "What are the verification interval requirements for a weighing or measuring instrument?", []),
    ("adv-none-unrelated-companies-act", "What is the penalty for a violation under the Companies Act?", []),
    ("adv-none-standard-package-size", "What are the standard package sizes a commodity must be packed in?", []),
    ("adv-none-consumer-protection-act", "What remedies does a consumer have under the Consumer Protection Act for a mislabeled product?", []),
    ("adv-none-import-duty", "What import duty applies to a packaged commodity entering India?", []),
]
for qid, text, expected in ADVERSARIAL_NO_RESULT:
    queries.append({"id": qid, "style": "adversarial_no_result", "query_text": text, "expected": expected})

OUT_PATH.write_text(json.dumps({
    "corpus_provision_count": len(corpus["provisions"]),
    "source_corpus": str(CORPUS_PATH.name),
    "queries": queries,
}, indent=2), encoding="utf-8")

by_style = {}
for q in queries:
    by_style[q["style"]] = by_style.get(q["style"], 0) + 1

print(f"Wrote {len(queries)} queries to {OUT_PATH}")
print(json.dumps(by_style, indent=2))
