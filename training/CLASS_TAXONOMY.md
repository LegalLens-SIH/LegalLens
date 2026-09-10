# LegalLense custom YOLO26 — class taxonomy decision document

Status: **FINAL — approved.** The 8 classes below are the final taxonomy.
`training/LABELING_GUIDE.md`, `training/COLLECTION_PLAN.md`,
`training/dataset.yaml`, and `training/labels/*/classes.txt` are already
built against this exact list and are consistent with it (re-verified
2026-09-07). Do not add, remove, split, or rename a class without updating
all of those files together — a `classes.txt` drift from `dataset.yaml`'s
`names:` order is a silent, serious corruption (see `DATASET_STRUCTURE.md`).

## Methodology

Classes below are derived directly from two places in the actual codebase,
not from a generic template:

1. `backend/rules/legal_metrology_rules_2011.json` — the real ruleset the
   compliance engine evaluates. Its `required_fields` lists are the only
   legally-grounded source of "what declaration must be present."
2. `backend/services/compliance_engine.py` — specifically the `ALIASES`
   dict and `normalize_ocr_result()`, which show how the two rules that
   apply to packaged commodities (`LMPC-R6-MANDATORY-DECLARATIONS` for
   retail, `LMPC-R24-WHOLESALE-DECLARATIONS` for wholesale) already treat
   some rule-field names as the *same underlying declaration*.

Two rules in the ruleset were excluded from this exercise entirely:
`LMGEN-R12-VERIFICATION-INTERVALS` and `LMGEN-R14-STAMPING-SEALING` apply to
weighing/measuring **instruments** (`pos_hardware_audit` profile), not
packaged commodities — out of scope for a package-label detector.
`LMPC-R11-NET-QUANTITY-EXCLUSION` is a numeric check
(`net_quantity == total_package_weight - wrapper_packaging_weight`) against
metadata that isn't a separate printed declaration on the panel — nothing
for YOLO to localize.

## Correction to the earlier (unreviewed) scaffolding

`training/dataset.yaml` from the prior YOLO26-integration-infrastructure
pass used a 6-class list (`mrp`, `net_quantity`, `manufacturer_address`,
`fssai_license`, `batch_mfg_date`, `consumer_care`) that was **not** checked
against the ruleset. Re-deriving it properly here surfaced real problems
with that list:

- `fssai_license` **does not correspond to any rule in this ruleset.**
  FSSAI (food safety) is a different regulator from Legal Metrology; nothing
  in `legal_metrology_rules_2011.json` or `compliance_engine.py` checks for
  it. Including it would have been exactly the "arbitrary class" this task
  warned against. **Dropped.**
- `unit_sale_price` is a required field in `LMPC-R6` and was **missing**
  from that list.
- `country_of_origin` is a required field in `LMPC-R6` and was **missing**.
- Product identity (`common_generic_name_of_commodity` /
  `identity_of_commodity`) is a required field in both `LMPC-R6` and
  `LMPC-R24` and was **missing**.

`training/dataset.yaml` and `training/README.md` have been updated to the
corrected 8-class list below so the scaffolding matches this document (still
easy to change again after your review — nothing has been trained).

## Proposed classes (v1) — 8 classes

| # | class | rule field(s) it covers |
|---|---|---|
| 0 | `product_identity` | `common_generic_name_of_commodity` (R6) + `identity_of_commodity` (R24) |
| 1 | `mrp` | `maximum_retail_price_mrp` (R6) |
| 2 | `unit_sale_price` | `unit_sale_price` (R6) |
| 3 | `net_quantity` | `net_quantity` (R6) + `total_number_of_retail_packages_or_net_quantity` (R24) |
| 4 | `entity_details` | `manufacturer_packer_importer_details` (R6) + `name_and_address_of_manufacturer_or_packer` (R24) |
| 5 | `country_of_origin` | `country_of_origin` (R6) |
| 6 | `mfg_date_batch` | `month_and_year_of_manufacture_or_packing` (R6), see note below |
| 7 | `consumer_care` | `consumer_care_details` (R6) |

Every required field in both packaged-commodity rules is covered by exactly
one class. No class exists that isn't backing a real rule field, except
`mfg_date_batch`'s batch/expiry portion (explained below).

---

### 0. `product_identity`

- **Meaning:** the commodity's common/generic name — what the product *is*
  (e.g. "Chocolate Chip Cookies", "Refined Rice Bran Oil"), not the brand
  slogan or decorative packaging art.
- **Inside the box:** the product-type/commodity-name text as printed on
  the principal display panel.
- **Outside the box:** brand logo art, marketing taglines, nutritional
  claims ("low fat", "no added sugar"), the manufacturer's brand name alone
  if the generic commodity name is a separate line.
- **Example:** on the test biscuit packet already in
  `backend/test_images/`, this is "CHOCOLATE CHIP COOKIES" — not "Good Day"
  (that's the brand) and not "Britannia" (that's the manufacturer, covered
  by `entity_details`).
- **Difficult case:** brand and generic name are sometimes fused into one
  stylized logo lockup with no separable text (e.g. a product whose brand
  name *is* its generic description). Needs a labeling-convention decision
  — deferred to that phase.
- **Always/conditional:** mandatory for retail (`R6`) and wholesale (`R24`).
- **Why YOLO:** `compliance_engine.py`'s current alias match for this field
  (`ALIASES["common_generic_name_of_commodity"]`) is the **weakest** in the
  file — it matches literal words like `"rice"`, `"sugar"`, `"flour"`
  against the whole OCR text blob, which only works if the commodity
  happens to be one of those three words. This is the class most likely to
  benefit from real region localization instead of keyword luck.
- **OCR alone sufficient?** No — the gap isn't OCR accuracy (this text is
  usually large and clean), it's that there's currently no reliable way to
  say *which* OCR line is the product identity without a keyword coincidence.
- **Alternative approach:** none better than YOLO here; this is squarely a
  "find the region, then trust whatever text is inside it" problem.

### 1. `mrp`

- **Meaning:** the Maximum Retail Price declaration.
- **Inside the box:** the full price block — "MRP"/"M.R.P." label, the
  currency symbol/amount, and the "inclusive of all taxes" qualifier if
  printed together (matches the existing pattern in
  `compliance_engine.py::_mrp_result`, which requires both an amount and the
  MRP wording).
- **Outside the box:** unrelated prices (see difficult case below), the
  unit sale price block (separate class).
- **Example:** "MRP ₹599 (Incl. of all taxes)" as one box.
- **Difficult case:** promotional packaging sometimes shows a
  struck-through "original" price next to a discounted price — only one is
  the legal MRP. Also: MRP and unit sale price are sometimes printed
  immediately adjacent to or even inside the same printed block (e.g. "MRP
  ₹99 (Rs 33/100g)") — **flagged for the pilot** to check whether `mrp` and
  `unit_sale_price` should actually merge into one `price_declaration`
  class once real photos are examined; keeping them separate for now
  because they are legally distinct declarations with distinct validation
  logic in `compliance_engine.py`.
- **Always/conditional:** mandatory for retail packages (`R6`). Not a
  required field in `R24` (wholesale).
- **Why YOLO:** the alias match already works reasonably well
  (`"mrp"`, `"₹"`, `"rs."` are strong, distinguishing keywords) but cannot
  currently handle the "two prices, which one is real" case — a real gap in
  `_mrp_result`, which just takes whatever line matched. Per-region
  detection is what would let the compliance engine say "N candidate MRP
  regions found, needs manual verification" instead of silently picking one.
- **OCR alone sufficient?** Mostly, for the single-price case. The
  multi-price disambiguation case is where YOLO adds real value.

### 2. `unit_sale_price`

- **Meaning:** price per unit weight/volume (e.g. "₹33/100g"), a distinct
  legal declaration from MRP.
- **Inside the box:** the per-unit price text and its unit qualifier
  (`/kg`, `/g`, `/l`, etc.).
- **Outside the box:** the MRP amount itself.
- **Example:** "Unit Sale Price: Rs. 90/kg".
- **Difficult case:** see `mrp` above — the two are sometimes visually
  fused. Also: **not every product prints this at all** — it is far more
  common on loose-sold-by-weight-style goods than on fixed-unit packaged
  snacks. Confirm this during pilot photo collection rather than assuming
  it will show up in most images.
- **Always/conditional:** mandatory for retail packages per `R6`, but
  *physically absent on many real packages* in practice — this is exactly
  the "not every field is mandatory/present for every commodity" case the
  task called out. `compliance_engine.py::_field_result` already treats an
  undetected field as `FAIL` (assumes absence = non-compliance) rather than
  "not applicable," which is arguably a separate, pre-existing rules-engine
  question worth revisiting later — not something to solve via YOLO classes.
- **Why YOLO:** same reasoning as `mrp` — mostly about disambiguating from
  the adjacent MRP text.
- **OCR alone sufficient?** Probably, if it weren't for the physical
  adjacency to MRP text muddying whole-text keyword matching.

### 3. `net_quantity`

- **Meaning:** the declared net quantity/weight/volume of the commodity
  itself (excluding packaging).
- **Inside the box:** the quantity value + unit (e.g. "Net Wt. 250 g",
  "Net Qty: 1 Unit").
- **Outside the box:** the wrapper/total-weight figures used by
  `LMPC-R11`'s numeric check (these, when present at all, are typically lab
  metadata, not a panel declaration — not something to box).
- **Example:** "Net Quantity: 250 ml".
- **Difficult case:** multipacks declare quantity per unit AND total
  quantity (e.g. "10 x 20g, Net Wt. 200g") — decide in the labeling
  convention whether that's one box or two.
- **Always/conditional:** mandatory for both retail (`R6`) and wholesale
  (`R24`, as "total number of retail packages or net quantity" — the same
  visual declaration type, just evaluated under a different rule depending
  on package type).
- **Why YOLO:** alias matching here is already fairly reliable (strong
  keywords: "net quantity", "net wt"). Main value-add is the
  multiple-declarations case (multipacks) and general region-level
  auditability.
- **OCR alone sufficient?** Largely yes for single-declaration packages.

### 4. `entity_details`

- **Meaning:** the manufacturer/packer/importer identification block — who
  made, packed, or imported the product, and their address.
- **Inside the box:** the full entity name + address block, however it's
  labeled ("Manufactured by:", "Packed by:", "Marketed by:", "Imported
  by:").
- **Outside the box:** the FSSAI license line, consumer care contact
  details (separate class), country of origin if printed as its own
  standalone line (see difficult case).
- **Example:** "MANUFACTURED & MARKETED BY: BRITANNIA INDUSTRIES LTD., 5/1A
  Hungerford Street, Kolkata..." as one box.
- **Difficult case:** country of origin is sometimes embedded inside this
  same block (e.g. "...Pune, Maharashtra (India)") rather than printed as
  its own line — the labeling convention needs an explicit rule for whether
  to still carve out a separate `country_of_origin` box in that case, or
  leave it inside `entity_details` and let extraction handle it.
- **Always/conditional:** mandatory for both retail and wholesale.
- **Why one merged class, not three:** `compliance_engine.py`'s own
  `ALIASES["manufacturer_packer_importer_details"]` already lists
  `["manufacturer", "packer", "importer", "manufactured by", "packed by"]`
  as interchangeable triggers for **one** field — the code has already made
  this semantic decision. Repository analysis directly supports the
  merged-class recommendation from the task brief; there is no existing
  code path that treats manufacturer/packer/importer as visually or
  semantically distinct today.
- **OCR alone sufficient?** Partially — the keyword match works, but
  there's no way today to tell which of several candidate address-like
  blocks on a page is *the* entity block versus, e.g., the consumer-care
  address. Region detection resolves that ambiguity directly.

### 5. `country_of_origin`

- **Meaning:** country-of-origin declaration.
- **Inside the box:** "Made in India" / "Country of Origin: India" or
  equivalent, as its own line/stamp when printed standalone.
- **Outside the box:** country mentioned only as part of the address inside
  `entity_details` (see difficult case in #4 — decide during labeling
  convention which one wins when both appear).
- **Example:** a separate "COUNTRY OF ORIGIN: INDIA" line, common on
  imported goods and increasingly required for e-commerce listings.
- **Difficult case:** the entity-details overlap above; also some products
  print this in a small stamp/seal near the barcode rather than in the main
  declaration cluster.
- **Always/conditional:** mandatory per `R6`. Not listed in `R24`.
- **Why YOLO:** currently a fairly strong keyword match (`"country of
  origin"`, `"made in"`), but standalone-vs-embedded-in-entity-details is
  exactly the kind of ambiguity a dedicated region helps resolve
  deterministically instead of via substring luck.
- **OCR alone sufficient?** Reasonably, when printed standalone; the
  embedded case is where localization helps.

### 6. `mfg_date_batch`

- **Meaning:** the manufacture/packing date declaration, which real
  packaging almost always prints together with batch number and expiry/use-
  by/best-before date as one compact block.
- **Inside the box:** the whole date/batch cluster — e.g. "PKD. 12/05/2024
  / BATCH No. A05124C1 / USE BY 11/11/2024" (this exact pattern is what the
  Britannia test photo in `backend/test_images/` actually shows).
- **Outside the box:** unrelated dates (e.g. a promotional "offer valid
  until" date, if one exists on the package).
- **Difficult case:** deciding where the cluster's box ends when the block
  wraps across a fold or a differently-colored ink-jet-printed strip.
- **Always/conditional:** `month_and_year_of_manufacture_or_packing` is
  the only part of this cluster that is a **currently-enforced** required
  field (`R6`). Batch number and expiry/best-before are **not** required
  fields anywhere in the current ruleset. This class is included as one
  merged region primarily because of visual reality (the fields are
  physically inseparable on most real packages, so annotating three
  overlapping tiny boxes is neither realistic nor useful), and captures
  batch/expiry "for free" alongside the field that's actually required —
  it does not mean this project is claiming a legal requirement for
  batch/expiry that doesn't exist in the ruleset. Flag if the ruleset is
  ever extended to check expiry/batch directly, since that might justify
  splitting this class later.
- **Why YOLO:** the existing alias match for the date field
  (`"mfg"`, `"packed on"`, `"date of manufacture"`) doesn't distinguish
  mfg date from expiry/batch text sitting right next to it — a real
  ambiguity today. A single class still helps (crops attention to the
  right region of the image) even without full three-way splitting.
- **OCR alone sufficient?** No — this is genuinely one of the more useful
  classes for YOLO precisely because the current keyword approach can't
  reliably separate mfg-date text from the adjacent batch/expiry text it's
  printed with.
- **Downstream handling decision (batch/expiry are extracted, not
  dropped):** the box is one region, but the OCR text inside it can contain
  up to three distinct data points (mfg date, batch number, expiry/best
  before). Decision: **extract all three when present; only mfg date feeds
  a compliance verdict.** Concretely, once this region's OCR text exists,
  extraction sub-parses it with the same regex-over-a-matched-block
  approach `compliance_engine.py` already uses elsewhere (see
  `_mrp_amounts()` and `_care_block()` for the existing pattern of pulling
  structured sub-values out of one matched text block) into:
  - `month_and_year_of_manufacture_or_packing` → the existing required
    `fields` entry, evaluated by `LMPC-R6`/`R24` exactly as today.
  - `batch_lot_number` and `expiry_or_best_before_date` → **new, optional
    `metadata` keys**, following the precedent already set by
    `normalize_ocr_result()`'s existing metadata dict (which already carries
    optional, not-universally-required values like `last_verification_date`
    and `physical_seal_verified` that only specific rules consume, see
    `compliance_engine.py` line ~131). They are recorded on the scan record
    and available to any future rule or manual-review UI, but **no current
    rule in `legal_metrology_rules_2011.json` reads either key** — they do
    not affect `overall_status` today. This is a deliberate middle path
    between "drop the data" (wasteful — it was already read by OCR) and
    "treat it as a required declaration" (would invent a legal requirement
    that doesn't exist in this ruleset). If the ruleset is ever extended to
    check expiry or batch directly, the data is already there waiting to be
    consumed - no re-annotation needed, only a new rule + a new
    `_rule_result` branch.
  - This is extraction-layer work (`backend/services/compliance_engine.py`
    and/or a new sub-parser), not something annotators need to act on -
    noted here so the decision is on record before labeling starts, not
    discovered as an ambiguity later.

### 7. `consumer_care`

- **Meaning:** consumer/customer care contact details.
- **Inside the box:** phone number and/or email and/or care-cell address,
  as one block, however it's introduced ("Consumer Care:", "For
  Feedback-Contact:", "Customer Care Cell").
- **Outside the box:** the manufacturer's own primary address in
  `entity_details`, if printed separately (they're sometimes adjacent but
  distinct blocks, as in the Britannia test photo).
- **Example:** "For Feedback-Contact: ... 1-800-4254449, feedback@brand.com"
- **Difficult case:** care details sometimes appear folded into the same
  paragraph as `entity_details` with no clean visual break — see
  `compliance_engine.py::_care_block`, which already has to guess a
  block boundary by scanning forward until it hits another declaration
  keyword. A dedicated box removes that guesswork entirely once the model
  is good enough.
- **Always/conditional:** mandatory per `R6`. Not listed in `R24`.
- **Why YOLO:** `_care_block`'s current heuristic (start at a trigger
  line, scan up to 3 more lines, stop early if a different declaration's
  keyword appears) is a real, working, but fragile piece of logic — this is
  the second-clearest case (after `product_identity`) where a bounding box
  is strictly better evidence than a text-scanning heuristic.
- **OCR alone sufficient?** No, for the folded-paragraph case specifically.

---

## Candidates considered and NOT recommended as YOLO classes (v1)

| candidate | verdict | reasoning |
|---|---|---|
| `fssai_license` | **Excluded** | Not part of Legal Metrology; not checked anywhere in this ruleset. Belongs to a different regulator (FSSAI) that this project does not currently implement rules for. Was mistakenly included in the earlier unreviewed scaffolding — corrected here. |
| `dimensions` | **Excluded (v1)** | Not a required field anywhere in `legal_metrology_rules_2011.json`. Some jurisdictions' e-commerce rules do require package dimensions, but this codebase's ruleset doesn't check for it — adding a class for an unimplemented rule would be inventing a requirement. Revisit only if the ruleset is extended. |
| `barcode` | **Excluded** | The only "barcode" in this codebase is a report-tracking barcode generated for exported PDF compliance reports (`frontend/compliance-report.html`) — unrelated to product barcodes. No rule consumes a product barcode/EAN today. If ever added, per the task's own framing: YOLO would only *locate* it, a dedicated barcode decoder (pyzbar/zxing) would *read* it — never an OCR-text-extraction target. Not worth training for until a real rule needs it. |
| separate `manufacturer` / `packer` / `importer` classes | **Rejected in favor of merged `entity_details`** | `compliance_engine.py`'s own `ALIASES` dict already treats these as one interchangeable field. No code path anywhere distinguishes them. |
| separate `batch_number` / `expiry_date` classes | **Merged into `mfg_date_batch`** | Neither is a required field in the current ruleset (only mfg/packing date is), and they are visually inseparable from the mfg date on nearly every real package examined so far. |
| product **category** classification (e.g. "this is a food product" / "this is a garment") | **Not a YOLO class at all** | Category is a manufacturer-declared field today (`backend/api/manufacturer.py::ProductRequest.description`, category dropdowns in the frontend), not something detected from the image. Out of scope for a declaration-region detector — this task is about *where* a declaration is, not *what kind of product* the package is. |

## Known integration gap (found during Phase 1 inspection, not fixed here)

`backend/ocr/yolo_service.py` currently supports only **one** generic
crop-the-whole-product-region behavior
(`YOLOService.best_crop_region()` picks the single largest detection by
area, see `backend/ocr/README.md`). It has no concept yet of "N per-class
regions, run OCR per region, tag each region's text with its class." Once a
multi-class model exists, `backend/services/compliance_engine.py`'s
`normalize_ocr_result()` — which currently keyword-matches across one
whole-page `full_text` blob — will also need to be extended to consume
per-class-tagged OCR text instead of (or alongside) the whole-page text to
actually realize the benefits described above (multi-instance detection,
disambiguation, etc.). **This is real follow-up engineering work, not
something a trained model provides automatically** — flagging it now so
it's planned for, not discovered late. The `mfg_date_batch` sub-parsing
decision above (extract batch/expiry into `metadata`, don't drop them) is
part of this same future extraction-layer work, not something to build
during labeling/pilot-training.

## Files changed to match this document

- `training/dataset.yaml` — class list corrected to the 8 classes above
- `training/README.md` — class table corrected to match

## Changelog

- Added an explicit downstream-handling decision for `mfg_date_batch`'s
  batch-number/expiry-date sub-values (extracted into optional `metadata`,
  not dropped, not treated as a required field) - see class 6 above.

Nothing else in `training/` or `backend/` was changed. No labeling
convention, collection plan, or work split has been created yet — per your
instruction, stopping here for review.
