# LegalLense YOLO26 — dataset directory structure & integrity rules

## Structure

```
training/
  dataset.yaml               <- Ultralytics config: class names + split paths
  images/
    train/                   <- ~most of your labeled photos
    val/                     <- held-out during training, used for validation/early-stopping
    test/                    <- FULLY held out - see "Why three splits" below
  labels/
    train/                   <- one .txt per image in images/train, same basename
      classes.txt            <- LabelImg class-index file, DO NOT REORDER (see LABELIMG_SETUP.md)
    val/
      classes.txt
    test/
      classes.txt
  flagged_for_review/
    NOTES.md                 <- ambiguous-class log (LABELING_GUIDE.md section G.2)
  rejected_images/           <- unusable photos set aside, never in images/train|val|test
  train_yolo.py                <- trains train/val only, NEVER touches test/
  evaluate_yolo.py             <- evaluates any split, incl. test, on demand
  validate_dataset.py          <- integrity checker, run anytime (see below)
  _dataset_utils.py            <- shared path-resolution helpers (used by the three scripts above)
```

This is the standard Ultralytics `images/`+`labels/`+`dataset.yaml` layout,
adapted from the task brief's generic `dataset/` example to match this
project's actual root (`training/` inside `Portal/`, alongside `models/`
where trained weights land - see `training/README.md`).

## Annotation format (YOLO `.txt`)

One line per labeled instance, space-separated, **normalized to 0-1**:

```
<class_id> <x_center> <y_center> <width> <height>
```

Example `product_042.txt` (an image with an `mrp` box and a `net_quantity` box):

```
1 0.5123 0.6890 0.1820 0.0430
3 0.2210 0.3125 0.3040 0.0510
```

`class_id` is the integer from `dataset.yaml`'s `names:` list (0-7, see
`training/CLASS_TAXONOMY.md`). All four coordinates are plain floats
normalized by image width/height - this is the same flat-text format used
by YOLOv5 through YOLO11, and Ultralytics' YOLO26 training API
(`train_yolo.py`) consumes it unchanged. LabelImg writes this format
automatically once set to YOLO mode (`training/LABELIMG_SETUP.md`) - you
should never need to hand-write a label file, but recognizing this format
is what "checking annotation files" (`LABELIMG_SETUP.md` section 6) means
in practice.

## Why three splits, not two

`train` and `val` alone are enough to *train* a model (val drives
early-stopping and the metrics you watch during training), but a model can
still end up indirectly overfit to val - every architecture/hyperparameter
decision made while watching val metrics is a form of information leaking
from val into the "trained" model, even without literal data leakage.
`test` is the antidote: a split that `train_yolo.py` **never reads at
all** (not a policy - the script's dataset-readiness check literally only
resolves `train`/`val`, see `_dataset_utils.py`), reserved for one final,
unbiased read on the model via `evaluate_yolo.py --split test` once you're
done iterating. This is what the task's "keep test data separate, avoid
leakage" requirement means in concrete terms here.

**`test/` is allowed to stay empty during the Phase 10 pilot** (30-40
images, train/val only, per `training/COLLECTION_PLAN.md`'s next phase) -
`train_yolo.py` runs fine with an empty or absent `test/` (verified by
testing). Populate `test/` once you're past the pilot and scaling to the
full dataset - a natural point to set aside, say, 10-15% of your total
photos that never get trained or validated on until final evaluation.

## Integrity rules

1. **Images match labels.** Every image should have a same-named `.txt` in
   the matching `labels/<split>/` folder - either with real annotation
   lines, or deliberately empty (confirmed "no declarations visible", see
   `LABELING_GUIDE.md` section H). An image with no label file at all is a
   different state: Ultralytics silently skips it entirely. Both states are
   legitimate; an *unintentional* gap between them is what
   `validate_dataset.py` catches (reported as a warning, since a missing
   label is often just "not labeled yet," not necessarily a mistake).

2. **Class IDs are consistent.** All three `classes.txt` files (`train`,
   `val`, `test`) and `dataset.yaml`'s `names:` list must list the same 8
   classes in the same order - LabelImg and Ultralytics both identify a
   class purely by index, not name. `validate_dataset.py` checks this
   exactly (byte-for-byte list comparison) and treats a mismatch as an
   **error**, not a warning, because a drifted `classes.txt` silently
   corrupts every label saved after the drift with no visible symptom.

3. **Train/val/test classes are represented.** Once a split has real
   images in it, every class ideally has at least some instances there -
   `validate_dataset.py` warns (per split) about any class with zero
   instances in a populated split. This is a warning, not an error: it's
   completely normal for `unit_sale_price` in particular to be
   under-represented early (see `COLLECTION_PLAN.md`), and normal for a
   small pilot to not yet cover every class in every split. Use this as a
   progress signal, not a hard gate.

4. **Test data is not leaked into training.** Enforced two ways:
   - **Structurally:** `train_yolo.py` never reads `test:` from
     `dataset.yaml` - not a discipline you have to maintain by hand.
   - **Detectively:** `validate_dataset.py` hashes every image's raw bytes
     and flags (as an **error**) any exact duplicate that appears in more
     than one split - catching the realistic leakage scenario of
     accidentally copying/re-exporting the same photo into two splits, not
     just a hypothetical.

5. **Split by PRODUCT, not by individual photo (a leakage class the
   hash check above cannot catch).** `COLLECTION_PLAN.md` deliberately has
   you photograph each product 2-3 times (front/back/side, per its diversity
   axes) - these are DIFFERENT files (different angle, different pixels), so
   the exact-byte-hash check in point 4 does not and cannot flag them as
   duplicates. But if one angle of a product lands in `train` and another
   angle of the *same physical product* lands in `val`/`test`, the model can
   partly learn to recognize that specific product's packaging (colors,
   fonts, layout) rather than the general visual pattern of a declaration
   class - inflating val/test metrics without real generalization. **Rule:
   when you split a batch of labeled images into train/val(/test), keep
   every photo of the same product together in one split - never spread one
   product's front/back/side photos across two different splits.** This is
   a manual discipline at labeling/splitting time (there is currently no
   automated check for it, unlike point 4's exact-duplicate case, since
   "same product" isn't something byte-hashing can detect) - the practical
   way to do this reliably is to move a product's photos into their target
   split folder as a set, right after labeling all of that product's
   angles, rather than shuffling individual files into `train`/`val`
   later by chance.

## Running the checker

```bash
backend\.venv311\Scripts\python.exe training\validate_dataset.py
```

Run this **repeatedly during labeling**, not just once at the end - see
`training/LABELIMG_SETUP.md` section 6. It never modifies anything; it only
reports. Exit code `0` means no errors (warnings may still be present and
are worth reading); exit code `1` means at least one error was found.

What it checks, concretely (verified against synthetic test cases during
development - each of these was confirmed to actually fire, not just
described):

- Malformed label lines (wrong number of values)
- Out-of-range class IDs
- Out-of-range coordinates (must be within [0, 1])
- `classes.txt` drift from `dataset.yaml`
- Images with no label file / orphan label files with no image
- Zero-instance classes in a populated split
- Duplicate image content within a split, and (as an error) across splits

What it does **not** check (out of scope for an automated script, see
`training/LABELING_GUIDE.md` and the QC phase that follows this one):
box tightness, whether the chosen class is actually correct, whether a
declaration was hallucinated. Those need human review.
