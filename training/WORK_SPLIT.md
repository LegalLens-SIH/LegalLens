# LegalLense YOLO26 — Ayush / Sarthak work split

Strategy: **A (by product category), richness-weighted** — see
`training/COLLECTION_PLAN.md` for the full category × class matrix and the
richness-score reasoning this assignment is based on. The `unit_sale_price`
loose-weight-staples pool is a **shared task**, not owned by either
category assignment (see below) — access to grocery/kitchen staples doesn't
have the same real-world asymmetry as, say, pharmacy or garment access, so
splitting it isn't expected to need the same real-world-access negotiation
as the main categories.

**Category-to-person assignment: FINALIZED below** (default recommendation —
swap individual rows between Ayush/Sarthak if real-world access is
lopsided, e.g. one of you has much easier pharmacy or garment access; the
richness-score balance is what matters, not which specific person holds
which row).

## Category assignment

| category | richness score | assigned to |
|---|---|---|
| Food packages | 21 | **Ayush** |
| Cosmetics / personal care | 21 | **Ayush** |
| Household products | 21 | **Ayush** |
| Garments / textiles | 13 | **Sarthak** |
| Electrical appliances | 17 | **Sarthak** |
| Pharmaceuticals (OTC/supplements only) | 20 | **Sarthak** |
| Beverages | 21 | **Sarthak** |

**Ayush: Food + Cosmetics + Household = richness 63 (3 categories).**
**Sarthak: Garments + Electrical + Pharma + Beverages = richness 71
(4 categories).** A ~6% richness gap (63 vs 71 out of 134 total) is well
within a reasonable balance band — deliberately not split 50/50 by raw
image count, since `unit_sale_price` per `COLLECTION_PLAN.md`'s difficulty
column is genuinely harder to source than the other 7 classes regardless of
category, and richness score already accounts for per-category class
density, not just headcount. Ayush's 3-category group is grocery/home/
personal-care items — typically the most volume-efficient to collect
(usually already on-hand); Sarthak's 4-category group has more categories
but each is individually lighter (garments in particular scores lowest at
13 — fewer of the 8 classes reliably appear on clothing labels, per
`COLLECTION_PLAN.md`'s category matrix).

Swap rows between yourselves before starting if access reality disagrees
with this default (e.g. if Sarthak has better pharmacy/electronics access
reversed from what's assumed here) — the richness-balance reasoning still
applies to whatever the final grouping is, just re-sum the two columns to
confirm it stays roughly balanced (each grouping should land within
~10% of the other, i.e. neither side much above ~74 or below ~60 given a
134 total).

## Shared task: `unit_sale_price` staples pool

Target (per `COLLECTION_PLAN.md`): ~40-60 dedicated photos of loose-weight/
bulk-sold staples (rice, atta, sugar, dal, spices, edible oil, ghee, liquid
detergents, milk).

**Default split: even, ~20-30 photos each**, unless one of you has
meaningfully better access to a wider variety of these items (e.g. a
bigger/more varied kitchen pantry, or easier access to a grocery store) —
adjust the split accordingly rather than treating 50/50 as mandatory.

**Ownership rule (applies to this pool and to the main category
assignment): whoever photographs an image also labels it.** This keeps a
single, unambiguous chain of responsibility per image and avoids a
"who's labeling whose photos" coordination problem — the shared pool is
shared in *target*, not in a way that requires jointly touching the same
images.

## What "ownership" of a category means in practice

For your assigned categories (and your share of the staples pool), you are
responsible for the full pipeline on those images, end to end:

1. Source/photograph the products (per `COLLECTION_PLAN.md`'s diversity
   axes - lighting, angle, packaging form, etc.)
2. Label them yourself in LabelImg (see `training/LABELIMG_SETUP.md`)
3. Log anything ambiguous in `training/flagged_for_review/NOTES.md` (per
   `training/LABELING_GUIDE.md` section G.2) and move unusable images to
   `training/rejected_images/` (section G.4)
4. Track your own progress against the table below

Flagged/ambiguous items get reviewed **together**, periodically, regardless
of who originally labeled them - that's the one place this is
collaborative rather than independently owned.

## Progress tracking

Lightweight, per-person checklist - update as you go. This tracks *your
assignment's* progress; a full dataset-wide tracker (collected/labeled/
reviewed/accepted/rejected counts across everything) comes in a later
phase and will pull from this, not replace it.

### Ayush

| category | target photos | photos taken | photos labeled | done? |
|---|---|---|---|---|
| Food packages | ~30-45 | | | ☐ |
| Cosmetics / personal care | ~20-30 | | | ☐ |
| Household products | ~20-35 | | | ☐ |
| Shared: unit-price staples (my share) | ~20-30 | | | ☐ |

### Sarthak

| category | target photos | photos taken | photos labeled | done? |
|---|---|---|---|---|
| Garments / textiles | ~15-25 | | | ☐ |
| Electrical appliances | ~10-15 | | | ☐ |
| Pharmaceuticals (OTC/supplements only) | ~15-25 | | | ☐ |
| Beverages | ~15-25 | | | ☐ |
| Shared: unit-price staples (my share) | ~20-30 | | | ☐ |

## Cross-references

- `training/CLASS_TAXONOMY.md` - the 8 classes and why
- `training/LABELING_GUIDE.md` - how to box them, when to skip/flag
- `training/COLLECTION_PLAN.md` - category × class matrix, richness scores,
  diversity requirements, per-class instance targets
- `training/LABELIMG_SETUP.md` - tool setup (once written)
