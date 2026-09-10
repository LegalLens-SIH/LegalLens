# LabelImg setup (Ayush & Sarthak)

**Verified compatible with this project's training pipeline:** LabelImg's
YOLO save mode writes exactly the format `train_yolo.py`/Ultralytics expect
— `<class_id> <x_center> <y_center> <width> <height>`, normalized 0-1, one
line per box, one `.txt` per image — confirmed by reading LabelImg's YOLO
writer against the annotation format documented in `training/README.md`.
No other tool is configured in this repo; use LabelImg unless you've agreed
otherwise (see `training/README.md`'s Roboflow note if you ever need a
web-based alternative).

## 1. Installation

Install LabelImg into its **own** environment - not `backend/.venv311`.
That environment's dependency set is deliberately pinned (see
`backend/requirements.txt` and `backend/constraints.txt`, which document a
real conflict already hit once in this project between `opencv-python` and
`opencv-python-headless`); adding an unrelated GUI tool's dependencies to it
risks exactly that kind of breakage again.

```bash
py -m venv labelimg_env
labelimg_env\Scripts\activate
pip install labelImg
```

(Any Python 3.8+ install works for this - it doesn't need to be the
project's Python 3.11 environment at all, since LabelImg never touches
PaddleOCR/Ultralytics/torch.)

Launch it with the environment active:

```bash
labelimg_env\Scripts\activate
labelImg
```

## 2. Opening the dataset

LabelImg has two independent directory settings - **set both** before
drawing any boxes:

1. **Open Dir** (toolbar or `Ctrl+U`) → point at the images folder for the
   split you're working on, e.g. `training\images\train`.
2. **Change Save Dir** (toolbar) → point at the *matching* labels folder,
   e.g. `training\labels\train`. Ultralytics pairs an image and its label
   purely by matching filename stem (`product_042.jpg` ↔
   `product_042.txt`) - if Open Dir and Change Save Dir point at
   mismatched splits (e.g. images from `train` but saving into `val`),
   you'll silently corrupt the split without any error from LabelImg
   itself.

Repeat this (both settings) whenever you switch between working on `train`
and `val` images.

## 3. Setting YOLO format

LabelImg supports three save formats, cycled by clicking the format button
on the left toolbar (it shows the current format as its label - click it to
cycle: PascalVOC → YOLO → CreateML → back to PascalVOC). **Click until it
reads "YOLO"** before saving anything. If you save even one box in
PascalVOC (XML) format by mistake, `train_yolo.py` will not see it at all -
Ultralytics only reads YOLO `.txt` labels, and a stray `.xml` file next to
an image is silently ignored, not an error, so this kind of mistake is easy
to miss unless you check (see step 6).

## 4. Loading the class list

**Do this before labeling your first image.** `training/labels/train/classes.txt`,
`training/labels/val/classes.txt`, and `training/labels/test/classes.txt`
already exist in this repo, pre-populated with the 8 classes in the exact
order `training/dataset.yaml` uses:

```
0 product_identity
1 mrp
2 unit_sale_price
3 net_quantity
4 entity_details
5 country_of_origin
6 mfg_date_batch
7 consumer_care
```

Because your **Change Save Dir** in step 2 already points at
`training/labels/train` (or `val`), LabelImg will find that folder's
`classes.txt` automatically and populate its class dropdown from it, in
this exact order, on both of your machines identically.

**Do not delete, reorder, or hand-edit these `classes.txt` files once
labeling has started.** LabelImg (and Ultralytics) identify a class purely
by its *index* in this file, not by name - if the order ever changes after
labels already exist, every previously-saved label silently repoints to a
different class with no error or warning from any tool in this pipeline.
If a class genuinely needs to be renamed later, only the *name* in this
file and in `dataset.yaml` should change - never the *order*.

## 5. Saving annotations

- Draw a box: `w` (or the "Create RectBox" toolbar button), drag the
  rectangle around the complete declaration (per
  `training/LABELING_GUIDE.md` rule A), then pick the class from the
  dropdown that appears.
- Save: `Ctrl+S`, or enable **View → Auto Save mode** so every box is
  written immediately (recommended - avoids losing work if LabelImg
  crashes or you forget to save before closing).
- Move to the next/previous image: `d` / `a`.
- An image with **zero** declarations visible still needs an explicit save
  (an empty `.txt` file) per `training/LABELING_GUIDE.md`'s "no
  hallucinated labels" section - in LabelImg, this means opening the image,
  confirming there's genuinely nothing to box, and saving anyway (with
  auto-save mode this happens automatically as you move past the image;
  without it, use `Ctrl+S` even though you drew nothing).

## 6. Checking annotation files

Don't rely on LabelImg's UI alone - it won't warn you about save-format
mix-ups, missing labels, or malformed files. Two ways to check, in order of
effort:

**Quick manual spot-check:** open a saved `.txt` file in a plain text
editor. Each line should look like `3 0.5123 0.6890 0.1820 0.0430` - one
integer (0-7) followed by four floats between 0 and 1. If you see XML tags
instead, you saved in the wrong format (step 3) - re-save that image in
YOLO mode.

**Automated check (recommended before every training run, not just
once):**

```bash
backend\.venv311\Scripts\python.exe training\validate_dataset.py
```

This is a real dataset-integrity script (see `training/DATASET_STRUCTURE.md`)
that checks every image has a matching label file (or is intentionally
unlabeled), every label line is well-formed with a valid class ID,
`classes.txt` matches `dataset.yaml` exactly, and reports per-class instance
counts across `train`/`val` - run it any time, not just at the end.

## 7. Avoiding duplicate/missing labels

- **Missing label file** (image has no `.txt` at all): means "Ultralytics
  will skip this image entirely" - fine if intentional (e.g. you haven't
  gotten to it yet), a silent gap in your dataset if not. `validate_dataset.py`
  reports these so they don't go unnoticed.
- **Empty label file** (`.txt` exists, zero lines): means "confirmed - no
  declarations visible" (see step 5) - a different, equally valid state.
  Don't confuse "haven't labeled this yet" with "labeled it as having
  nothing."
- **Duplicate/orphan label files:** a `.txt` file with no matching image
  (usually from renaming or deleting an image without removing its old
  label) - `validate_dataset.py` flags these too. Delete the orphan rather
  than leaving it; Ultralytics ignores label files with no matching image,
  but an orphan sitting around is a sign something else may have gone
  wrong (e.g. you meant to relabel an image under a new name and forgot the
  old file).
- **Same image labeled twice under different filenames** (e.g. you
  accidentally imported a photo twice under different names): not caught by
  any tool automatically - this is what `training/LABELING_GUIDE.md`
  section G.4 (move to `rejected_images/`) and periodic review are for.
  Keep an eye out for near-duplicates as you go rather than relying on
  after-the-fact detection.
