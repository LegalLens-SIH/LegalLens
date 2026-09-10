# LegalLense YOLO26 labeling guide (for Ayush & Sarthak)

Read `training/CLASS_TAXONOMY.md` first — it defines the 8 classes, what
belongs in each box, and per-class difficult cases. This document is the
*general* rules that apply across all 8 classes: how to draw a box, what to
do with messy real-world photos, and when to skip rather than guess.

Tool: **LabelImg**, YOLO save format (see `training/README.md` for
install/setup — that section is unchanged by this guide). Every rule below
assumes you're drawing one rectangle per instance and picking one class from
the `dataset.yaml` list, same as LabelImg's normal workflow.

---

## A. Bounding box rule: box the COMPLETE semantic declaration

Draw one box around the **whole declaration as a single unit of meaning**,
not its sub-parts.

```
MRP ₹599
Inclusive of all taxes
```
→ **one** `mrp` box around both lines.

**Not** three boxes for "MRP", "₹599", and "Inclusive of all taxes"
separately. The extraction layer reads whatever text PaddleOCR finds inside
the box as one blob and parses it from there (see
`compliance_engine.py::_mrp_result` for how MRP text is already parsed as
one block) — splitting the box defeats that and gives YOLO an inconsistent,
harder-to-learn target.

This applies to every class. If you're unsure where a declaration "ends,"
default to including everything that is part of the same printed
paragraph/cluster and stop at the first line that is clearly a *different*
declaration (a different class, per `CLASS_TAXONOMY.md`'s "outside the box"
notes) or clearly unrelated marketing copy.

## B. Multi-line declarations: one box, however many lines

```
Manufactured by:
ABC Textiles Pvt Ltd
Plot 21, MIDC,
Pune, Maharashtra
```
→ **one** `entity_details` box spanning all four lines.

Same rule for `mfg_date_batch` (batch/date/expiry cluster, see
`CLASS_TAXONOMY.md` class 6) and `consumer_care` (phone + email + address,
if printed as one block). Do not create one box per line just because the
declaration wraps across several lines of text.

## C. Variable layout: there is no fixed location

MRP (or any other class) can legitimately appear on the front, back, side,
top, or bottom of a package, and its position varies by manufacturer,
package shape, and photo angle. **Never assume a class only appears in one
region of the image, and never skip labeling an instance because "MRP is
usually on the front and this is a back photo."** If it's visually present
and identifiable, box it, regardless of where on the package or where in
the frame it falls.

This also means: don't normalize or reposition boxes to match some expected
layout. Draw the box exactly where the declaration actually is in *this*
photo.

## D. Partial visibility

Four related-but-distinct situations, each with its own rule:

1. **Text cut off by the image frame edge** (declaration continues past
   where the photo ends): box only the visible portion, right up to the
   frame edge. Never extend a box into pixels that don't exist. Still label
   it with the correct class *if* enough is visible to be confident which
   class it is (e.g. "MRP ₹" visible even if the amount is cut off is still
   clearly `mrp`). If what's visible is too little to identify the class
   confidently, don't guess — see rule G below (skip/flag).

2. **Package folded / creased across the declaration:** box only the
   visible parts. If the fold hides enough of the declaration that you
   genuinely cannot tell what class it is, skip that instance for this
   image (see rule G) — do not draw a box across the folded-away, unseen
   portion "because you know it's probably there."

3. **Declaration partially hidden by something else in the photo** (a
   price sticker, a hand holding the package, shrink-wrap overlap, another
   product in front of it): same principle — box what is visible, decide
   class confidence on visible content only, skip if unidentifiable.

4. **Label physically damaged** (torn, faded ink, worn packaging): if the
   remaining visible text is still identifiable as belonging to a specific
   class, box the visible remnant. If the damage has erased it beyond
   recognition, don't box it.

**General principle across all four:** box what you can see, never what you
infer must be there. A dataset built on "I'm pretty sure that's an MRP even
though I can't see it clearly" teaches the model to hallucinate detections
on real photos later — the exact failure mode Phase H (below) exists to
prevent.

## E. Blur / smudging

YOLO's job here is *region localization*, not *text legibility* — that's
PaddleOCR's job downstream. So the bar for boxing a blurred/smudged
declaration is: **can you, a human, still tell this is visually a
declaration of a specific class** (based on position, format, partial
letterforms, surrounding context), even if you couldn't transcribe it
yourself? If yes, box it normally with that class — a blurry-but-boxed MRP
still teaches YOLO what an MRP region looks like, even if that particular
training image will produce garbage OCR text later (that's an OCR-quality
problem, not a labeling problem, and it's realistic: real inspection photos
are often imperfect).

If the blur is severe enough that you can't tell *which class* it is (not
just can't read the exact text), that's a class-identification problem, not
a legibility one — use rule G (unclear class), not this rule.

## F. Multiple instances of the same class in one image

The YOLO annotation format natively supports any number of boxes per class
per image — this is not a limitation to work around, it's exactly the
mechanism that lets the compliance engine later say "2 MRP declarations
found, needs manual verification" instead of silently guessing (see
`CLASS_TAXONOMY.md` class 1's difficult case). **Label every genuinely
separate visual instance as its own box of the correct class. Do not merge
distant instances into one box, and do not suppress/pick-a-winner among
duplicates — that decision belongs to the compliance engine, not to you.**

Specific cases:

- **Two MRP values** (e.g. a struck-through "old" price next to a
  discounted price): label **both** as separate `mrp` boxes. Don't decide
  which one is "the real MRP" — you don't have the information to judge
  that from a photo, and the whole point of multi-instance detection is to
  surface this ambiguity downstream instead of hiding it.
- **MRP next to unit sale price, printed close together or overlapping**
  (e.g. "MRP ₹99 (Rs 33/100g)"): this is **not** two MRP instances — use
  the class *definition*, not proximity: the total package price is `mrp`,
  the per-unit price is `unit_sale_price`. Label them as one box of each
  class (or one box covering both if they're genuinely printed as a single
  inseparable run of text — see rule A's "when in doubt" guidance).
- **Multiple manufacturer/entity blocks** (e.g. both "Manufactured by: X"
  and "Marketed by: Y" printed as separate blocks): label each as its own
  `entity_details` box. Do not draw one box spanning both if there's
  unrelated content between them.
- **Multiple net-quantity mentions** (e.g. a multipack showing "10 x 20g"
  in one place and "Net Wt. 200g" elsewhere): label each occurrence as a
  separate `net_quantity` box.
- **Multiple dates:** only box the ones that are part of the actual
  mfg/batch/expiry declaration cluster as `mfg_date_batch` (per
  `CLASS_TAXONOMY.md` class 6). An unrelated promotional date (e.g. "Offer
  valid until DD/MM") is not part of that declaration and should not be
  boxed as any class.

## G. Unclear class — decision tree

Work through these in order for every candidate declaration you notice:

1. **Confident it's a declaration AND confident which of the 8 classes?**
   → Label it normally.
2. **Confident something is a legal declaration, but unsure which of the 8
   classes it is** (ambiguous wording, unfamiliar layout, genuinely could be
   two different things): → Assign your best-guess class **and** add a line
   to `training/flagged_for_review/NOTES.md` (image filename + one-sentence
   reason). Do not invent a 9th "unknown" class — a catch-all class with no
   consistent visual definition would actively hurt training, since YOLO
   would have no coherent pattern to learn from it. Review flagged items
   together periodically (this feeds the QC pass planned for a later phase).
3. **You can tell there's *some* text/graphic there, but it isn't
   recognizable as any of the 8 legal declarations at all** (marketing
   copy, a certification logo, a random icon, nutritional info that isn't
   one of our classes): → Skip. Do not box it under any class.
4. **The image itself is unusable** (wrong/irrelevant product, an
   accidental near-duplicate of a photo you already took, exposure/focus so
   bad that *nothing* on the package is legible): → Move the whole image to
   `training/rejected_images/` instead of `images/train` or `images/val`,
   and don't create a label file for it at all. Rejected images don't count
   toward instance targets and must never end up in the trained/evaluated
   dataset.

`training/flagged_for_review/` and `training/rejected_images/` have been
created (currently empty, `.gitkeep` placeholders) so this workflow has
somewhere to put things from day one.

## H. No hallucinated labels

**Never draw a box for a declaration class just because the ruleset says a
product "should" have it.** If a declaration genuinely isn't visible on a
given package — most commonly `unit_sale_price`, which
`CLASS_TAXONOMY.md` already documents as frequently and legitimately absent
on real packaging — simply don't label it for that image. This is normal
and expected, not a sign of an incomplete annotation. It is *never*
acceptable to box empty space, a blank area, or unrelated text under a
class label because "this product is supposed to have an MRP somewhere."

This is why `training/README.md`'s existing convention matters: **a label
file listing zero lines means "none of the 8 classes are visible in this
photo,"** which is a legitimate, useful negative example for training —
it's different from omitting the label file entirely (which tells
Ultralytics to skip the image altogether). Use an empty (zero-line) `.txt`
file for "photographed a product but genuinely see none of the 8
declarations," and skip images entirely (rule G.4) only for images that
shouldn't be in the dataset at all.

## Quick reference

| situation | action |
|---|---|
| Declaration fully visible, class obvious | Box it, per `CLASS_TAXONOMY.md` |
| Cut off by frame / folded / occluded / damaged, but still identifiable | Box only the visible part |
| Cut off / folded / occluded / damaged, class unidentifiable | Skip this instance |
| Blurry but you can tell what class it is | Box it normally |
| Blurry and you can't tell what class it is | Treat as unclear class (G.2) |
| Two+ genuine instances of one class | Box each separately, never merge or pick-a-winner |
| Some declaration-like text, unsure which class | Best-guess label + note in `flagged_for_review/NOTES.md` |
| Something present but not one of the 8 classes | Skip, don't box |
| Declaration genuinely absent from this package | Don't box it - leave out of that image's labels |
| Whole image unusable | Move to `rejected_images/`, no label file |
