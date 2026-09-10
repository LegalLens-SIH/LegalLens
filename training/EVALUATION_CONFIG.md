# LegalLense YOLO26 — evaluation configuration (Phase 12)

`training/evaluate_yolo.py` reports, per run:

| metric | where | purpose |
|---|---|---|
| Overall precision/recall/mAP50/mAP50-95 | printed | one-line health check, not sufficient alone |
| **Per-class** precision/recall/mAP50/mAP50-95 | printed table | the actual point - see "why per-class matters" below |
| **Per-class confusion** (TP/FP/FN) | printed table (added this pass) | which classes are confused with something else vs. simply missed |
| Full confusion matrix (all class×class confusions) | saved PNG (`confusion_matrix.png`, `confusion_matrix_normalized.png`) | see exactly what gets confused with what, not just per-class totals |
| Precision-Recall / F1 / P / R curves | saved PNG (`BoxPR_curve.png` etc.) | how a metric changes across confidence thresholds |
| Zero-instance-class warning | printed | flags classes with no val examples at all - their metrics are meaningless, not just low |

All plots land in `training/runs/eval/` (or `training/runs/<name>/` if you
pass `--split test` for the final held-out read - see
`training/DATASET_STRUCTURE.md`).

## Why per-class matters (restating the task's own example)

Overall accuracy can look fine while specific legally-important classes
fail silently:

```
Overall mAP50: 0.84          <- looks fine
mrp        P=0.91
net_qty    P=0.87
entity     P=0.62            <- actually struggling, hidden by the average
```

This is exactly why `evaluate_yolo.py` was built to print per-class numbers
by default (not as an optional flag) from the very first version of this
script - there's no "just show me the overall score" mode.

## Reading the confusion summary

For each class:

- **High TP, low FP, low FN:** healthy - trust it.
- **Low recall (high FN):** the model is missing real instances of this
  class - usually a data-volume or data-variety problem (see
  `training/COLLECTION_PLAN.md`'s per-class difficulty column).
- **Low precision (high FP):** the model is calling other things this
  class - check the saved `confusion_matrix.png` for *which* other class
  it's confusing this with. If it's consistently one specific pair (e.g.
  `mrp` ↔ `unit_sale_price`), that's a strong, specific signal - review
  `training/CLASS_TAXONOMY.md`'s difficult-case note for that pair (already
  flagged as a watch item: the two are sometimes printed adjacent/fused)
  before assuming "just needs more data" will fix it. More data alone
  won't fix a genuine definitional ambiguity between two classes.
- **Both classes in a confused pair have real signal but keep swapping:**
  this is the labeling-convention problem, not a model-capacity problem -
  revisit the boundary rule in `CLASS_TAXONOMY.md`/`LABELING_GUIDE.md`
  before relabeling anything.

## Evaluation config values

| setting | default | notes |
|---|---|---|
| `--split` | `val` | the iteratively-used split during development; `test` is reserved for one final, unbiased read (see `DATASET_STRUCTURE.md`) - never used for decisions that feed back into training choices |
| `--imgsz` | 640 | should match whatever `--imgsz` was used in `train_yolo.py` for the checkpoint being evaluated, or metrics aren't a fair comparison |
| `--device` | `cpu` | same auto-detect philosophy as training and as production `YOLOService` |
| `--weights` | `models/legal_lense_yolo26.pt` | the one checkpoint `train_yolo.py` produces by default |

## What this does NOT evaluate

- **Downstream OCR/extraction/compliance quality** - `evaluate_yolo.py`
  only scores *detection* (did YOLO find the right region), not what
  PaddleOCR reads from it or what the compliance engine concludes. That's
  what `test_pilot_pipeline.py` (Phase 10/13) and the real-pipeline
  validation (Phase 13, below) are for - a class can score well on
  detection metrics here while still producing bad OCR text if its boxes
  are technically "correct" per IoU but too tight/loose in practice.
- **Generalization to product categories not yet photographed** - metrics
  are only as representative as `images/val`'s diversity. A model can score
  well on `val` while failing on a category `COLLECTION_PLAN.md` calls for
  but that hasn't been collected yet.
