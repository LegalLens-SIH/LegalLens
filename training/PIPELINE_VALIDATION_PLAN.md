# LegalLense YOLO26 — real pipeline validation plan (Phase 13)

**Status: procedure defined, not yet run** - requires a trained custom
checkpoint (Phase 10/11) and real photos, neither of which exist yet. This
is the comparison procedure to execute once they do.

## The rule this phase exists to enforce

> Do not claim improvement merely because YOLO training metrics increased.

`evaluate_yolo.py`'s precision/recall/mAP are detection-quality metrics
computed against your own labels - they say nothing on their own about
whether the *practical outcome* (what a user or inspector actually sees) got
better. This phase is the check that closes that gap: run real images
through and look at the actual output, not just the training report.

## What can be compared today vs. what needs the integration gap closed first

**Can compare now, once a custom checkpoint exists:** detection and
per-region-crop-then-OCR quality, using `training/test_pilot_pipeline.py`
against both the pretrained `yolo26n.pt` and the custom checkpoint on the
*same* set of real photos.

**Cannot yet compare:** full production-API-level compliance results
(`/api/ocr`, `/api/scans`), because - as documented in
`training/CLASS_TAXONOMY.md`'s "Known integration gap" - `backend/ocr/
yolo_service.py`'s current `best_crop_region()` picks one region by area,
which is correct for the current generic single-object model but would be
actively wrong for an 8-class declaration detector (it would crop away 7 of
8 declarations, keeping only whichever one has the largest box). Comparing
at the production-API level requires the per-class multi-region extraction
work that gap describes - out of scope until it's built. **Do not** set
`YOLO_MODEL_PATH` to a pilot/custom checkpoint in `Portal/.env` to try to
force this comparison early; it would make the live app worse, not better,
for the reason above.

## Baseline: what the CURRENT (pretrained) model actually does

Already measured directly, on the real test photo already in this repo
(`backend/test_images/product_photo.png`, a Britannia biscuit packet):

```
[1] class='donut' conf=0.737 bbox=(573,340,768,484)   -> OCR text: '' (empty)
[2] class='book'  conf=0.279 bbox=(134,74,1491,954)   -> OCR text: <full label text, 63 detections>
```

This is the concrete baseline "before" state: the pretrained model
correctly localizes something declaration-relevant only by accident (it
thinks the whole packet is a "book," which happens to be a big-enough
crop to keep all the label text) and separately, confusingly, thinks a
printed photo of a cookie is a real "donut" - a small, wrong, useless
region that would produce empty OCR if it were ever chosen instead of the
"book" box. **This is not a strawman** - it's this project's own
already-recorded pretrained-model behavior, from the same integration work
that built `yolo_service.py`.

## Comparison procedure (once a custom checkpoint exists)

Run the SAME set of real photos through both models via
`test_pilot_pipeline.py`:

```bash
backend\.venv311\Scripts\python.exe training\test_pilot_pipeline.py <photo> --model yolo26n.pt          > before.txt
backend\.venv311\Scripts\python.exe training\test_pilot_pipeline.py <photo> --model models\legal_lense_yolo26.pt > after.txt
```

For each photo, compare `before.txt` and `after.txt` against this
checklist - each is a concrete, checkable criterion, not a vibe:

| criterion | how to check |
|---|---|
| **Better region localization** | Does the custom model produce boxes that visually correspond to actual declarations (per `CLASS_TAXONOMY.md`), vs. the pretrained model's generic/accidental COCO-class boxes? |
| **Fewer irrelevant OCR regions** | Does the custom model avoid detecting things like the pretrained model's "donut" (a real object in the photo that has nothing to do with any declaration)? |
| **Fewer OCR errors** | For each detected region, is the OCR text plausible for that declared class (an `mrp` region's text contains a price; a `consumer_care` region's text contains a phone/email)? |
| **Better field extraction** | Does the OCR text pulled from a *correctly-cropped, class-specific* region read more cleanly than the pretrained model's one-giant-crop full-page text dump (which mixes nutrition info, ingredients, and every declaration together indiscriminately, as seen in the baseline above)? |
| **Less overlapping visualization** | If/when boxes are visualized (not currently built into any script - a `cv2.rectangle` overlay could be added to `test_pilot_pipeline.py` if useful during review), do the 8 class boxes stay visually distinct rather than heavily overlapping each other? |
| **Improved downstream compliance extraction** | **Deferred** - requires the per-class multi-region integration (see above); not testable at the production level yet. The closest current proxy: does each class's cropped OCR text contain what `backend/services/compliance_engine.py`'s `ALIASES` dict looks for (e.g. does the `mrp` crop's text actually contain "MRP" or "₹")? Check by eye against `compliance_engine.py`'s `ALIASES` dict for now. |

## What "pipeline validation passed" looks like

Not a single number - a qualitative comparison across the checklist above,
across enough real photos (the pilot's 30-40, or more once the full
dataset exists) to see a consistent pattern, not just one lucky/unlucky
photo. Document specific before/after examples (like the baseline
donut/book example above) rather than only summary statistics - a concrete
"here's a photo where the old model failed and the new one didn't" is more
convincing and more useful for spotting remaining problems than an
aggregate score.

If the custom model's detection metrics (Phase 12) look good but this
comparison doesn't show a real practical improvement on actual photos,
**trust this comparison over the training metrics** - that's the whole
point of this phase existing separately from Phase 12.
