# LegalLense YOLO26 — pilot plan (Phase 10)

**Status: prepared, not executed.** Everything in this document has been
built and tested with synthetic/throwaway data to confirm the tooling
works mechanically. The actual pilot run requires 30-40 real, labeled
product photos that do not exist yet - this document is the exact
procedure to run once they do, not a report of a completed pilot.

## Objective (per the task brief - repeated here so it isn't lost)

The pilot is **not** about model accuracy. 30-40 images cannot produce a
production-quality detector. Its job is to catch, cheaply, before scaling
to the full ~300-400 photo collection:

- Incorrect class definitions (does a class make sense once you try to
  actually box it repeatedly, not just in theory)
- Bad annotations (labeling mistakes, format issues)
- Wrong dataset paths (already tested extensively with synthetic data -
  see below - but real data can still surprise)
- `train_yolo.py` problems
- `evaluate_yolo.py` problems
- Integration bugs
- Bad crops
- OCR failures caused by boxes

If the pilot reveals problems, fix them before labeling the full dataset -
that's the entire point of doing this at 30-40 images instead of 300-400.

## What's already done vs. what needs real data

**Done and verified (via synthetic throwaway data - random noise images,
trivial labels, never touching real product photos or this repo's actual
dataset folders):**

- `train_yolo.py` runs a full train loop successfully: loads `yolo26n.pt`,
  overrides its head for 8 classes, trains, saves `best.pt`, copies it to
  `models/legal_lense_yolo26.pt`.
- `evaluate_yolo.py` runs against a trained checkpoint, reports overall +
  per-class precision/recall/mAP50/mAP50-95, a confusion summary
  (TP/FP/FN per class), and a "zero instances for these classes" warning.
- `validate_dataset.py` correctly catches malformed labels, out-of-range
  class IDs/coordinates, `classes.txt` drift, missing/orphan label files,
  under-represented classes in a populated split, and cross-split
  duplicate images (verified against 8 deliberately injected problem
  types, zero false positives on well-formed data).
- The `train:`/`val:`/`test:` path resolution, the `--data`/`--output`
  overrides, and the dataset-readiness guards on both `train_yolo.py` and
  `evaluate_yolo.py` all behave correctly.
- `test_pilot_pipeline.py` (new - see Phase 13 below) mechanically runs
  end to end: loads a checkpoint, detects, crops each detected region,
  OCRs each crop via the existing `PaddleOCRService`, prints results.

**Cannot be verified until real data exists:**

- Whether the 8 classes are actually learnable from real product photos
  (synthetic random-noise images produce no real detection signal by
  construction - this was never a substitute for real data, only a way to
  test the *code*, not the *model*).
- Real annotation quality/consistency issues that only show up once you
  try to label real, messy photos (fold-overs, glare, ambiguous entity
  blocks, etc. - see `training/LABELING_GUIDE.md`).
- Real crop quality and OCR behavior on genuine label text (as opposed to
  running OCR against a) random noise or b) the pre-existing single test
  photo, neither of which represents the diversity `training/COLLECTION_PLAN.md`
  calls for).

## Exact pilot procedure

### Step 0 — source and label 30-40 real images

Per `training/COLLECTION_PLAN.md` and `training/LABELING_GUIDE.md`. For a
pilot specifically, favor **breadth over volume**: a handful of photos
each from several different categories (not 30 photos of the same
product) surfaces more distinct problems per photo than a narrow batch
does. Include at least a few images you expect to be awkward (a folded
label, a blurry shot, a multi-panel package) - the pilot's job is to find
problems, so don't only photograph easy cases.

Split roughly 80/20 into `training/images/train` / `training/images/val`
(e.g. ~26 train / ~8 val for a 34-image pilot). Leave `training/images/test`
empty for now - it's meant to stay empty until past the pilot stage (see
`training/DATASET_STRUCTURE.md`).

### Step 1 — validate before training

```bash
backend\.venv311\Scripts\python.exe training\validate_dataset.py
```

**Success looks like:** exit code 0, no errors. Warnings are fine to
proceed with (e.g. some classes under-represented is expected at this
scale) but read them - an unexpectedly large "unlabeled images" or "orphan
labels" warning usually means something went wrong in the LabelImg
workflow (see `training/LABELIMG_SETUP.md` section 7) and is worth fixing
before spending time training on it.

**If it fails:** fix the reported errors (malformed lines, `classes.txt`
drift, etc.) and re-run before moving to Step 2. Do not train through
validator errors.

### Step 2 — train the pilot model

```bash
backend\.venv311\Scripts\python.exe training\train_yolo.py
```

Defaults are already tuned for a small dataset (see
`training/TRAINING_CONFIG.md`) - no flags needed for the pilot. Expect this
to take longer than the synthetic 1-epoch smoke tests already run (60
epochs, real image sizes, CPU) - realistically tens of minutes to a few
hours depending on hardware, not seconds.

**Success looks like:** the script completes, prints "Best checkpoint
copied to: ...models\legal_lense_yolo26.pt", and exits 0. Loss values
printed during training should generally trend downward across epochs (not
required to reach a particularly low number at this data scale - just
shouldn't be flat or diverging, which would suggest a labeling or
configuration problem worth investigating before scaling up).

**If it fails:** re-run `validate_dataset.py` first - most training-time
failures at this stage trace back to a dataset issue the validator should
have already caught; if it didn't, that's a validator gap worth reporting.

### Step 3 — evaluate

```bash
backend\.venv311\Scripts\python.exe training\evaluate_yolo.py
```

**Success looks like:** a per-class table where classes that actually had
several labeled examples show non-zero precision/recall (exact numbers
don't matter yet at pilot scale - the fact that the model learned *some*
of the pattern does). Classes with very few pilot examples may reasonably
show 0 - that's expected at this scale, not necessarily a bug. Check the
confusion summary for any class that's confused with a *specific* other
class disproportionately (e.g. `mrp` boxes frequently misclassified as
`unit_sale_price`) - that's a strong, specific signal to review those two
classes' definitions against `training/CLASS_TAXONOMY.md` before scaling
up, rather than just "add more data" generically.

**If every class shows exactly 0 across the board:** something is likely
wrong structurally (labels not being read, wrong `dataset.yaml`, etc.)
rather than just "not enough data yet" - 30-40 images should produce at
least some non-zero signal on the more common classes.

### Step 4 — visually validate crops + OCR on real images

```bash
backend\.venv311\Scripts\python.exe training\test_pilot_pipeline.py path\to\a\pilot\photo.jpg
```

Run this against a handful of your pilot photos (ideally including val
images the model didn't train on). Read `training/test_pilot_pipeline.py`'s
own module docstring first - **do not** set `YOLO_MODEL_PATH` in
`Portal/.env` to the pilot checkpoint; this script exists specifically so
you can validate the model without doing that (see the docstring for why
that would currently make the live app's OCR worse, not better).

**Success looks like:** for each detected box, the printed OCR text
plausibly matches what that region's class should contain (e.g. an `mrp`
detection's OCR text contains a price and/or "MRP"). Boxes with garbled,
truncated, or completely unrelated OCR text indicate either a bad
detection (wrong region) or a bad crop (too tight/loose) - both are useful
pilot findings.

**If it fails:** check the checkpoint path and that `backend/.venv311` has
`ultralytics`/`opencv-python-headless`/`paddleocr` all installed (see
`backend/ocr/README.md`) - this script depends on the same environment as
the live app.

### What counts as "pilot passed"

Not a specific accuracy number - a pilot this small isn't meant to
produce one. The pilot has succeeded once you can say:
1. All four steps above ran without unexpected tool/script errors.
2. At least the higher-priority, easier classes (`product_identity`,
   `mrp`, `entity_details` per `training/COLLECTION_PLAN.md`'s
   difficulty ratings) show *some* real detection signal.
3. No labeling-convention or class-definition problems were found that
   would need retroactively relabeling pilot images (if one is found, fix
   the convention/taxonomy doc, re-label the affected pilot images, and
   confirm the fix before scaling up - don't carry a known-bad convention
   into 300+ images).
4. `test_pilot_pipeline.py`'s crop+OCR output looks reasonable on at least
   the more common classes.

If 2-4 don't hold, that's a legitimate pilot outcome too - it means fix
the identified problem(s) and re-run the affected steps, still at pilot
scale, before scaling the dataset up.
