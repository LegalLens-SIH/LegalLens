# LegalLense YOLO26 — quality control process (Phase 15)

Two layers: **automated** (`validate_dataset.py`, catches structural/format
problems, run constantly - see `training/DATASET_STRUCTURE.md`) and
**human review** (this document, catches judgment problems the validator
structurally cannot).

## What's already automated (don't re-do these by hand)

`training/validate_dataset.py` already catches, without any human review
step:

- Malformed label lines, out-of-range class IDs/coordinates
- `classes.txt` drift from `dataset.yaml`
- Images with no label file / orphan label files
- Zero-instance classes in a populated split
- **Exact byte-for-byte duplicate images**, including across splits (data
  leakage)

Run it before every review session so reviewers spend time on judgment
calls, not on things a script already checks.

## What needs human review

| check | what to look for |
|---|---|
| **Box tightness** | Does the box hug the actual declaration per `LABELING_GUIDE.md` rule A (complete region, not too loose/too tight), not just "roughly around it"? |
| **Class correctness** | Is this actually the right one of the 8 classes, per `CLASS_TAXONOMY.md`'s definitions - not a plausible-but-wrong guess? |
| **Missing objects** | Is there a declaration visibly present in the photo that has NO box at all - an annotator simply missed it? |
| **Incorrect labels** | Box exists, but for the wrong reason (e.g. boxed a promotional date as `mfg_date_batch` when `LABELING_GUIDE.md` says not to) |
| **Inconsistent conventions** | Does this annotator's boxing style match `LABELING_GUIDE.md` consistently, or drift over time/across images (e.g. sometimes including the "MRP" label text in the box, sometimes not)? |
| **Duplicate images** | *Near*-duplicates specifically - two photos of the same product from slightly different angles/lighting that `validate_dataset.py`'s exact-byte-hash check won't catch (they're different files, not identical). Judgment call: genuinely different angle (keep both, good diversity) vs. accidental near-repeat (redundant, consider dropping one). |
| **Near-duplicate images** | Same as above - the line between "good diversity" (per `COLLECTION_PLAN.md`) and "wasted effort re-labeling something we already have" is a judgment call, not a script's job. |
| **Unreadable annotations** | Not a format problem (validator catches that) - a box that's technically well-formed YOLO syntax but doesn't correspond to anything sensible when you look at the image (e.g. a box with `0.5 0.5 0.001 0.001` - syntactically fine, practically useless). |

## Review process

**Cross-review, not self-review.** Per `training/WORK_SPLIT.md`'s ownership
model (whoever photographs also labels), the *other* person reviews -
fresh eyes catch conventions the original labeler has stopped
consciously noticing.

**Sample size: review at least 15-20% of each person's labeled images**,
chosen at random (not just the easy/obvious ones) - not exhaustive
(reviewing 100% of everything defeats the point of splitting the work), but
enough to catch a systematic problem (a misunderstood class, a consistent
convention drift) before it's baked into hundreds of images. If a reviewed
sample turns up a real, repeated problem, that's a signal to review a
larger fraction of that person's remaining work specifically, not to stop
at the planned 15-20%.

**When to review:** in batches, not only at the very end - e.g. after each
person's first ~20-30 labeled images (catches convention drift early, while
it's cheap to fix going forward), then periodically (every ~50-100 more
images) rather than one giant review pass after the full dataset is done.

**Flagged items** (`training/flagged_for_review/NOTES.md`, per
`LABELING_GUIDE.md` section G.2) are reviewed together as part of this same
process, not separately.

## What to do with a finding

- **Isolated mistake** (one box, one image): fix it directly in LabelImg,
  re-save, done.
- **Systematic issue** (a class consistently mislabeled, a convention
  consistently not followed): don't just fix the sampled instances - go
  back through *all* of that person's images for that specific issue. Note
  it in this document's changelog (below) so it's visible that a pass
  happened and why, and update `training/LABELING_GUIDE.md` if the root
  cause was an underspecified rule rather than a simple mistake.
- **Taxonomy-level issue** (a class genuinely doesn't work as defined once
  you've tried to box it repeatedly): this is worth surfacing immediately,
  not silently working around - it means `training/CLASS_TAXONOMY.md`
  itself may need revisiting, which affects every image already labeled
  under the old definition.

## QC changelog

Log each review pass here - date, scope, what was found, what was done.
Keeps the review process itself auditable rather than an untracked side
activity.

| date | scope | findings | action taken |
|---|---|---|---|
| *(none yet - dataset collection hasn't started)* | | | |
