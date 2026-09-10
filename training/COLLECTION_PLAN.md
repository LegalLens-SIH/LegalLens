# LegalLense YOLO26 — photo collection plan (Phase 5 & 6)

Builds on `training/CLASS_TAXONOMY.md` (the 8 classes) and
`training/LABELING_GUIDE.md` (how to box them). Read both first — this
document is about *what to photograph*, not how to annotate it.

## A note on the original candidate category list

The starting candidate list mixed **product categories** (food, garments,
cosmetics, ...) with **packaging form factors** (bottles, jars, pouches,
boxes). Treating "bottles/jars" as its own top-level category would double-
count: a beverage, a cosmetic, and a household cleaner can all come in a
bottle. Packaging form factor is handled below as a cross-cutting diversity
axis that applies *within* every category (a household cleaner might be a
bottle or a pouch refill; a food item might be a box, a pouch, or a jar) —
not as a category of its own. This keeps the category list to real,
distinct product domains with genuinely different declaration patterns.

## Why category matters here (not just "get more photos")

Legal Metrology declaration presence varies by product type — the task
brief was explicit that not every product carries every field, and Phase 2
already found that `unit_sale_price` in particular is conditional. The
table below is the concrete version of that principle: it tells you, per
category, which of the 8 classes to actually expect, so you're not hunting
for a declaration that basically never appears on that product type.

Legend: `✓✓✓` reliably present · `✓✓` common · `✓` occasional/variable ·
`—` rare or not applicable.

| Category | product_identity | mrp | unit_sale_price | net_quantity | entity_details | country_of_origin | mfg_date_batch | consumer_care |
|---|---|---|---|---|---|---|---|---|
| Food packages | ✓✓✓ | ✓✓✓ | ✓ | ✓✓✓ | ✓✓✓ | ✓✓ | ✓✓✓ | ✓✓✓ |
| Garments / textiles | ✓✓ | ✓✓✓ | — | ✓ | ✓✓ | ✓✓✓ | — | ✓✓ |
| Cosmetics / personal care | ✓✓✓ | ✓✓✓ | ✓ | ✓✓✓ | ✓✓✓ | ✓✓ | ✓✓✓ | ✓✓✓ |
| Household products (detergents, cleaners) | ✓✓✓ | ✓✓✓ | ✓✓ | ✓✓✓ | ✓✓✓ | ✓✓ | ✓✓ | ✓✓✓ |
| Electrical appliances | ✓✓✓ | ✓✓✓ | — | ✓ | ✓✓✓ | ✓✓✓ | ✓ | ✓✓✓ |
| Pharmaceuticals (OTC/supplements only — see caution below) | ✓✓✓ | ✓✓✓ | — | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓ |
| Beverages | ✓✓✓ | ✓✓✓ | ✓ | ✓✓✓ | ✓✓✓ | ✓✓ | ✓✓✓ | ✓✓✓ |
| **Loose-weight / bulk-sold staples** (rice, atta, sugar, dal, spices, edible oil, ghee — see below) | ✓✓✓ | ✓✓✓ | **✓✓✓** | ✓✓✓ | ✓✓✓ | ✓✓ | ✓✓ | ✓✓ |

**Practical/legal caution:** for pharmaceuticals, photograph only
over-the-counter medicines or health supplements you can buy/already have
at home — don't photograph prescription-only drugs. Also keep an eye on
photo backgrounds generally: avoid capturing other people, ID documents, or
unrelated personal/proprietary information in frame.

## The `unit_sale_price` sourcing problem, addressed directly

`unit_sale_price` is the one class that will NOT show up reliably if you
just photograph a random mix of products — the table above makes this
explicit rather than letting it surface as a surprise after labeling.
**Deliberately seek out loose-weight/bulk-sold items where per-unit pricing
is standard practice:**

- Rice, atta/flour, sugar, pulses/dal, spices — especially larger pack
  sizes (500g+), which print per-kg pricing far more consistently than
  small sachets.
- Edible oil (bottles/pouches/tins) and ghee — commonly print Rs/litre or
  Rs/kg.
- Liquid detergents and dishwashing liquids sold by volume.
- Milk (pouches/cartons).
- Any "family pack" / bulk-size variant of a product that also exists in a
  small single-serve size — the bulk version is more likely to carry a
  unit price than the small one.

This is the "Loose-weight / bulk-sold staples" row above — treat it as its
own deliberate sub-collection target, not something to hope for
incidentally while shooting the other 7 categories.

## Diversity axes (apply across every category and every photo session)

These matter because the real deployment context is a phone photo taken by
an inspector or manufacturer rep in an ordinary environment — not a studio
shot. A YOLO26 model trained only on clean, well-lit, front-facing photos
will fail on exactly the conditions it needs to work in.

- **Packaging form:** plastic pouch, cardboard box, glass jar, bottle,
  tube, carton, flexible/foil packaging, wrapped/shrink-wrapped package.
- **Lighting:** indoor, outdoor, bright, low light, with flash, without
  flash.
- **Angle:** front, back, side, slight rotation, perspective/off-axis.
- **Image quality:** clear, slightly blurred, reflections/glare, shadows,
  small/dense text.
- **Capture device:** vary phones/cameras between you and Sarthak where
  possible — different sensors and lens distortion characteristics matter
  for generalization.
- **Background:** plain surface, cluttered surface, a realistic
  inspection-style environment (shelf, counter).

Each *product* should be photographed at least twice — front (mainly for
`product_identity`, sometimes `mrp`) and back/main declaration panel
(mainly `entity_details`, `net_quantity`, `mfg_date_batch`,
`consumer_care`) — plus a side/bottom shot specifically when
`country_of_origin` or `unit_sale_price` is printed there instead. Don't
force every product to exactly 2 photos if a third angle is where a
declaration actually lives — that would just recreate the "hallucinated
label" problem from the other direction (skipping a real, visible
declaration because it wasn't on the "standard" two angles).

---

# Phase 6 — dataset size targets per class

Targets are **instances** (one bounding box = one instance), not images —
a single photo usually contributes instances of several classes at once
(see `training/README.md`'s existing minimum-instances guidance, which
this table refines per-class rather than as one flat number).

| class | min target (floor) | ambitious target | expected source | priority | difficulty | notes |
|---|---|---|---|---|---|---|
| `product_identity` | 100 | 200 | Rides along with almost every photo taken for any other class | High | Low | Present in ~every photo; hardest part is annotator consistency (brand vs. generic name), not scarcity — see `CLASS_TAXONOMY.md` difficult case |
| `mrp` | 100 | 200 | Rides along with almost every photo | High | Low | Reliably printed on nearly all retail packaging |
| `unit_sale_price` | 80 | 150 | **Dedicated** loose-weight/bulk-staples sub-collection (see above) | Medium | **High** | Will NOT be hit by general category photos alone; budget extra photos specifically for this, and expect it to lag the other classes even with deliberate sourcing |
| `net_quantity` | 100 | 200 | Rides along with most photos except unit-sold appliances/single garments | High | Low-Medium | Weaker on garments/appliances categories per matrix above |
| `entity_details` | 100 | 200 | Rides along with almost every photo | High | Low | Multi-line box, but presence is reliable across all categories |
| `country_of_origin` | 100 | 180 | Rides along with most photos; garments/electronics/pharma are the strongest sources | Medium | Medium | Difficulty is the embedded-in-entity-details ambiguity (see `CLASS_TAXONOMY.md`), not scarcity |
| `mfg_date_batch` | 100 | 180 | Rides along with most photos except garments | High | Medium | Often small/dense print — may need close-up photos to keep the box tight and legible per the labeling guide's blur rules |
| `consumer_care` | 100 | 180 | Rides along with most photos | Medium | Low | Reliable presence; occasional ambiguity when folded into the entity-details paragraph (see labeling guide rule E/`_care_block` note) |

**Totals:** floor ≈ 780 instances, ambitious ≈ 1,490 instances. Given most
classes co-occur 4-7 per photo (everything except `unit_sale_price`), a
realistic photography target is:

- **~120-150 distinct products** across the 7 general categories, each
  photographed 2-3 times (front/back/side as needed) → roughly 250-350
  general photos, which should comfortably reach the floor and likely the
  ambitious target for the 7 co-occurring classes given reasonable
  diversity.
- **~40-60 additional dedicated photos** of loose-weight/bulk-sold staples,
  specifically to hit the `unit_sale_price` floor — this is on top of the
  general count, not counted against it, since general category photos
  will under-deliver this class as explained above.

**Total realistic target: roughly 300-400 photos overall.** This is a
planning estimate, not a hard requirement — same spirit as
`training/README.md`'s existing "rules of thumb, not a hard requirement"
framing. Track actual progress against this once collection starts (a
tracker comes in a later phase); adjust the loose-weight sub-collection
size up if `unit_sale_price` is still lagging once real numbers come in.

## Which classes are likely to need more data than this plan assumes

- `unit_sale_price` — already flagged above as the structurally hardest
  class to source; if the dedicated sub-collection still underperforms,
  this is the first class to revisit rather than assuming more of the
  general 7-category photos will eventually cover it.
- `country_of_origin` and `mfg_date_batch` — not scarce, but the two
  classes most likely to need **annotation review** (not more raw photos)
  because of the embedded-in-another-class ambiguity and small-print
  legibility issues respectively — worth watching in the pilot (a later
  phase) before assuming more data alone will fix low precision/recall on
  these two.
