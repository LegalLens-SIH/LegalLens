# LegalLense YOLO26 — 5-10 image annotation calibration exercise

**Status: prepared, not executed.** Run this BEFORE `training/PILOT_PLAN.md`'s
30-40 image pilot, not instead of it — this is a much smaller, cheaper step
with a different purpose.

## Why this is a separate step from the 30-40 image pilot

`PILOT_PLAN.md`'s pilot trains an actual model and is the right scale to
tell you whether the 8 classes are *learnable*. It is the wrong scale to
cheaply catch something simpler and more common: **you and Sarthak
interpreting `CLASS_TAXONOMY.md`/`LABELING_GUIDE.md` slightly differently**
(where exactly an `entity_details` box ends, whether a struck-through price
counts as a second `mrp` instance, how tightly to crop `mfg_date_batch`).
If that happens, discovering it after 30-40 images means relabeling a
meaningful chunk of the pilot. Discovering it after 5-10 means relabeling
almost nothing. This exercise exists purely to find and fix that kind of
disagreement while it's still cheap — it produces no trained model and
isn't scored against one.

## Procedure

### Step 1 — pick 5-10 images together, deliberately diverse

Do not use this exercise's images from your own separate category
assignments (`WORK_SPLIT.md`) — pick a small shared pool together so
you're both labeling the *same* photos. Deliberately include:

- At least 2-3 different product categories (not 8 photos of similar
  snack packets).
- At least one genuinely easy case (clean, front-on, well-lit — a
  sanity-check baseline).
- At least one awkward case per `LABELING_GUIDE.md` section D/E (a fold,
  glare, a slight blur, small/dense print) — the exercise is more useful if
  it surfaces disagreement, not just confirms agreement on easy shots.
- At least one image where `entity_details` and `consumer_care` (or `mrp`
  and `unit_sale_price`) are printed close together or adjacently — the two
  pairs `CLASS_TAXONOMY.md` already flags as the most boundary-ambiguous.
- If you can get one: a multipack or multi-panel package, to test the
  "multiple instances of one class" rule (`LABELING_GUIDE.md` section F)
  early.

These can later be moved into the real pilot's `images/train` or
`images/val` if the resulting labels turn out fine — this exercise doesn't
have to be throwaway, just don't count it toward pilot progress until it's
been through Step 3 below.

### Step 2 — label independently, don't compare yet

Both of you label all 5-10 images **separately**, each using your own copy
of the same source images, following `LABELING_GUIDE.md` exactly as
written. Don't discuss specific boxes with each other while doing this —
the whole point is to see where you land *without* coordinating, since
that's what will actually happen once you split up the full ~300-400 photo
collection by category.

Save your two label sets into separate temporary folders (e.g.
`training/_calibration_ayush/` and `training/_calibration_sarthak/`, both
outside `images/`/`labels/` so they never get mistaken for real dataset
content or picked up by `train_yolo.py`/`validate_dataset.py`) — don't
overwrite each other's `.txt` files in the same location.

### Step 3 — compare and reconcile

For each image, compare your two label sets side by side (open both in
LabelImg, or just read the two `.txt` files together):

1. **Same number of boxes?** If not, figure out why — did one of you miss a
   declaration, or did one of you box something the other correctly skipped
   (per `LABELING_GUIDE.md` section G.3/H)?
2. **Same class per corresponding box?** A disagreement here is the most
   important signal — it means a class boundary is unclear in practice, not
   just in theory.
3. **Roughly the same box extent?** Minor pixel differences are normal and
   fine (`QC_PROCESS.md` already treats "box tightness" as a judgment call,
   not a bug). A *large* disagreement (one of you boxed one line, the other
   boxed a whole paragraph) signals a rule A/B ("complete declaration, one
   box") misunderstanding worth discussing.

### What counts as agreement vs. disagreement

Not a numeric IoU threshold — at this scale (5-10 images, done by hand),
just look at each pair of boxes together and ask "would these two boxes
plausibly train the model on the same lesson?" That's a yes/no per box, not
a percentage.

- **Agreement:** same class, boxes cover the same declaration, both would
  clearly teach the model the same thing even if the exact pixel edges
  differ slightly.
- **Disagreement:** different class chosen for what's obviously the same
  declaration; one person boxed something the other deliberately skipped
  (or vice versa); wildly different box extent (whole paragraph vs. one
  line) for the same declaration.

### Step 4 — resolve and record

- **Isolated one-off mistake** (clearly just an error, not a genuine
  ambiguity): agree on the correct label, move on — nothing to change in
  the docs.
- **Genuine ambiguity** (both interpretations are defensible given the
  current wording): this is a real gap in `CLASS_TAXONOMY.md` or
  `LABELING_GUIDE.md`, not a labeling mistake. Fix the wording in whichever
  document is ambiguous so the *next* annotator (either of you, on the next
  image) doesn't hit the same fork. Log what was found and what was
  clarified in this document's changelog below.
- If a genuine ambiguity is found, **also re-check whether it appears
  elsewhere** in the same document (e.g. if `mrp` vs. `unit_sale_price`
  adjacency turns out to be a real recurring problem, check whether the
  existing `LABELING_GUIDE.md` section F guidance already covers it or
  needs sharpening).

### What "exercise passed" looks like

Not zero disagreements — some are expected and useful to find. It's passed
once:

1. Every disagreement found has been resolved to one agreed answer.
2. Any genuine (not one-off) ambiguity has a corresponding wording fix in
   `CLASS_TAXONOMY.md` or `LABELING_GUIDE.md`, so it's now written down, not
   just verbally agreed between the two of you.
3. You both re-label just the images where a rule changed, so the final
   5-10 labels are consistent with the (possibly updated) written
   convention, not with two different unwritten interpretations of an
   earlier draft.

Once that holds, move on to `training/PILOT_PLAN.md`'s 30-40 image pilot —
these 5-10 images (now reconciled) can be folded into that pilot's
train/val split rather than discarded, per Step 1's note above.

## Changelog

Log each calibration pass here — date, what disagreed, what was
clarified/changed as a result. Keeps this auditable the same way
`QC_PROCESS.md`'s changelog does for the larger review process.

| date | disagreement found | resolution / doc change |
|---|---|---|
| *(none yet - exercise hasn't been run)* | | |
