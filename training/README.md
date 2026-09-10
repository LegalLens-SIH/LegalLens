# LegalLense custom YOLO26 training

Master index for the custom YOLO26 effort. Read in this order the first
time; come back to specific documents as needed once you're underway.

## Current status

- **Class taxonomy: approved.** `training/CLASS_TAXONOMY.md`
- **Labeling convention: approved.** `training/LABELING_GUIDE.md`
- **Collection plan: approved.** `training/COLLECTION_PLAN.md`
- **Work split: finalized.** `training/WORK_SPLIT.md`
- **Annotation calibration exercise (5-10 images, before the 30-40 image
  pilot): prepared, not executed.** `training/ANNOTATION_EXERCISE.md`
- **Tooling (LabelImg setup, dataset structure, all three scripts):
  built and tested.** Verified with synthetic/throwaway data - see each
  document's own testing notes.
- **Pilot (Phase 10): prepared, not executed.** Needs 30-40 real labeled
  photos that don't exist yet. `training/PILOT_PLAN.md`
- **No real training has occurred.** `models/legal_lense_yolo26.pt` does
  not exist yet. The app still runs entirely on the pretrained `yolo26n.pt`
  for generic product-region cropping.

## Reading order

1. **`training/CLASS_TAXONOMY.md`** - what the 8 classes are and why,
   including rejected candidates, grounded in `backend/rules/
   legal_metrology_rules_2011.json` and `backend/services/compliance_engine.py`
   (topic 2 below).
2. **`training/LABELING_GUIDE.md`** - how to draw boxes (topic 3 below).
3. **`training/COLLECTION_PLAN.md`** - what to photograph, category-by-class
   expectations, the `unit_sale_price` sourcing plan (topic 5 below).
4. **`training/WORK_SPLIT.md`** - Ayush/Sarthak division of labor.
5. **`training/LABELIMG_SETUP.md`** - exact tool setup.
6. **`training/DATASET_STRUCTURE.md`** - folder layout and integrity rules
   (topic 4 below).
7. **`training/ANNOTATION_EXERCISE.md`** - a 5-10 image calibration round
   BOTH of you label independently, before the 30-40 image pilot - catches
   taxonomy/convention disagreements while they're cheap to fix.
8. **`training/PILOT_PLAN.md`** - the 30-40 image pilot procedure (prepared,
   not yet run).
9. **`training/TRAINING_CONFIG.md`** - training configuration and why
   (topic 6 below).
10. **`training/EVALUATION_CONFIG.md`** - evaluation configuration and how
    to read the results (topic 7 below).
11. **`training/PIPELINE_VALIDATION_PLAN.md`** - comparing custom vs.
    pretrained-model behavior on real images, not just training metrics.
12. **`training/DATASET_PROGRESS.md`** - whole-dataset collection/labeling
    tracker, to be filled in as work proceeds.
13. **`training/QC_PROCESS.md`** - annotation review process.

## The 10 things this documentation set covers

1. **YOLO26 purpose** - localize *where* a Legal Metrology declaration is
   on a package label, so PaddleOCR can read that region specifically
   instead of the whole image indiscriminately. YOLO26 never makes a
   compliance decision - that stays entirely
   `backend/services/compliance_engine.py`'s job. See the pipeline diagram
   below and `backend/ocr/README.md`'s existing YOLO26-integration section
   (from the earlier, pretrained-model integration pass).
2. **Class taxonomy** - `training/CLASS_TAXONOMY.md`.
3. **Labeling rules** - `training/LABELING_GUIDE.md`.
4. **Dataset structure** - `training/DATASET_STRUCTURE.md`.
5. **Collection strategy** - `training/COLLECTION_PLAN.md` +
   `training/WORK_SPLIT.md`.
6. **Training process** - `training/TRAINING_CONFIG.md` +
   `training/train_yolo.py` + `training/PILOT_PLAN.md`.
7. **Evaluation process** - `training/EVALUATION_CONFIG.md` +
   `training/evaluate_yolo.py`.
8. **Integration with PaddleOCR** - see "How this connects to the running
   app" below - this is the one topic with a real, documented limitation
   (the "known integration gap"), not just a pointer to another file.
9. **Known limitations** - see "Known limitations" below.
10. **Future custom model improvements** - see "Future improvements" below.

## How this connects to the running app (and its current limitation)

```
Product Image -> optional OpenCV preprocessing -> YOLO26 -> crop -> PaddleOCR -> OCR JSON
                                                     ^
                                     currently: pretrained yolo26n.pt only
```

The pretrained-model integration (YOLO26 as a generic product-region
cropper) is already live - see `backend/ocr/README.md`. Everything in
`training/` builds a *replacement* detector for that same slot, one that
understands 8 specific declaration types instead of generic COCO objects.

**This is not a drop-in swap, and the existing "just change
`YOLO_MODEL_PATH`" framing from the pretrained-model integration does NOT
apply once a custom multi-class checkpoint exists.** `backend/ocr/
yolo_service.py`'s current `best_crop_region()` selects the single
largest-by-area detection and crops the whole image down to just that -
correct when the model detects one generic "the product" region, actively
wrong for an 8-class detector: it would keep only whichever one
declaration happens to have the biggest box and silently discard the other
seven before OCR ever runs. **Do not set `YOLO_MODEL_PATH` to a custom
checkpoint in `Portal/.env` until the per-class multi-region integration
below is built** - doing so today would make the live app's OCR worse, not
better. Validate a trained custom model with `training/test_pilot_pipeline.py`
and `training/PIPELINE_VALIDATION_PLAN.md` instead, entirely outside the
production code path, until that integration work is done.

**The integration work still needed** (not built in this pass - out of
scope until a validated custom model exists): `yolo_service.py` needs a
multi-region mode that, given N per-class detections, crops and OCRs each
one separately and tags the resulting text with its class; and
`compliance_engine.py`'s `normalize_ocr_result()` (which currently
keyword-matches across one whole-page `full_text` blob) needs to consume
that per-class-tagged text instead of, or alongside, the whole-page text to
realize the actual benefits (multi-instance detection like "2 MRP values
found," precise region-based extraction) a per-class detector is for.

## Known limitations

- **No real training has happened.** Every number, config default, and
  procedure in this documentation set has been verified *mechanically*
  (scripts run correctly, paths resolve, error handling works) using
  synthetic/throwaway data - never real product photos, and never a
  meaningfully-trained model. Whether the 8 classes are actually learnable
  from real photos at the scale planned is genuinely unknown until the
  Phase 10 pilot runs.
- **The production integration gap above** - a trained custom model is not
  usable in the live app without further engineering work.
- **`unit_sale_price` is structurally hard to source** - see
  `training/COLLECTION_PLAN.md`. Expect this class to lag the others even
  with deliberate effort.
- **`country_of_origin` and `mfg_date_batch` have annotation ambiguity**,
  not scarcity, as their main risk (embedded-in-another-class boundary
  calls) - more data won't fix this on its own; convention clarity will.
- **`fssai_license`, `dimensions`, and `barcode`** were deliberately
  excluded from the taxonomy (see `CLASS_TAXONOMY.md`) - if a future
  business requirement needs any of these, that requires a taxonomy change
  and likely new rules-engine work, not just more labeled photos.

## Future improvements

- Closing the per-class multi-region integration gap (above) - the
  necessary next engineering step once a validated model exists.
- Extending `mfg_date_batch`'s sub-parsing (mfg date -> required field,
  batch/expiry -> optional `metadata` keys per `CLASS_TAXONOMY.md`'s
  documented decision) once that region-level OCR text is actually
  available to parse.
- Revisiting whether `mrp`/`unit_sale_price` should merge into one class,
  informed by real pilot photos (flagged as a possibility, not decided, in
  `CLASS_TAXONOMY.md`).
- A GPU training path, if/when available - `--device` already supports
  this without other code changes (`training/TRAINING_CONFIG.md`).
- Revisiting `dimensions`/other excluded candidates if the underlying
  ruleset (`backend/rules/legal_metrology_rules_2011.json`) is ever
  extended to require them.

## Scripts quick reference

| script | purpose | touches production code? |
|---|---|---|
| `train_yolo.py` | fine-tune `yolo26n.pt` -> `models/legal_lense_yolo26.pt` | no |
| `evaluate_yolo.py` | per-class precision/recall/confusion on any split | no |
| `validate_dataset.py` | dataset integrity checks, run anytime | no |
| `test_pilot_pipeline.py` | per-class crop+OCR diagnostic on a real photo | no - reads `PaddleOCRService`, never modifies it |
| `_dataset_utils.py` | shared path-resolution helpers for the four scripts above | no |

None of these five files are imported by, or affect, the running
application (`backend/main.py` and everything it imports) - confirmed by
inspection, since nothing under `backend/` imports anything from
`training/`.

## Explicitly out of scope for this directory

- Any actual training images or labels (supplied separately)
- Automatically wiring a trained checkpoint into the running app -
  `YOLO_MODEL_PATH` is a manual, deliberate opt-in via `.env`, and per
  "Known limitations" above, not yet safe to do for a multi-class checkpoint
  regardless
- Changes to `backend/ocr/yolo_service.py`'s current cropping/detection
  logic - it keeps using the pretrained `yolo26n.pt` until the integration
  gap above is closed
