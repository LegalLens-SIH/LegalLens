# LegalLense YOLO26 — training configuration (Phase 11)

This formalizes what's already baked into `train_yolo.py`'s defaults (see
`train_yolo.py --help` for the authoritative, always-current list) and
explains the reasoning, so the config isn't just implicit in argparse
definitions. Values confirmed directly against
`ultralytics/cfg/default.yaml` in the installed package (8.4.142), not
assumed.

## Model checkpoint

**`yolo26n.pt`** (pretrained, official Ultralytics release asset) - the
smallest YOLO26 variant, fine-tuned from ImageNet/COCO-pretrained weights,
**not** trained from random initialization. Confirmed during the original
YOLO26 integration work that `model.train()` correctly transfers 606/708
compatible weight tensors from the pretrained checkpoint and only replaces
the detection head for the new 8-class output
(`Overriding model.yaml nc=80 with nc=6` - now `nc=8` - was observed
directly in test output). Starting from pretrained weights rather than
random init matters a lot at this dataset size (hundreds, not tens of
thousands, of images) - the backbone's already-learned general visual
features (edges, textures, shapes) don't need to be relearned from scratch,
only the detection head needs to learn what an `mrp` box looks like.

Configurable via `--base` / `YOLO_BASE_MODEL` env var - never silently
switches to a different YOLO version (no code path in this project
references yolo8/9/10/11/12).

## Image size

**640px** (`imgsz=640`) - Ultralytics' standard default, a reasonable
balance for this task. Two things to watch once real data exists:
`mfg_date_batch` and `unit_sale_price` are often small/dense print (see
`training/CLASS_TAXONOMY.md`) - if the pilot (Phase 10) shows these classes
struggling specifically due to text being too small at 640px on your source
photos, increasing to `--imgsz 960` or `1280` is the first thing to try
before assuming it's a data-volume problem. Higher resolution means slower
CPU training, so this is a tradeoff to make deliberately, informed by pilot
results, not preemptively.

## Batch size

**4** (`batch=4`) - conservative, chosen for CPU training with no GPU
available locally (confirmed via `torch.cuda.is_available() == False` in
this environment). A larger batch would train faster per-epoch on a GPU but
risks running out of memory or being needlessly slow on CPU. Revisit if
GPU access becomes available - `--device 0` already supports switching
without other code changes.

## Epochs & patience

**60 epochs, patience=15** - a small dataset (hundreds of images) benefits
from more epochs than a large one (each epoch is cheap, and the model needs
more passes to extract signal from limited data), but risks overfitting the
longer it trains - `patience=15` (Ultralytics' early-stopping: stop if val
metrics don't improve for 15 consecutive epochs) guards against training
past the point of diminishing/negative returns without you having to guess
the right epoch count upfront. For the Phase 10 pilot specifically (30-40
images), training will likely hit the patience limit and stop well before
60 - that's expected, not a bug.

## Augmentation

Ultralytics' detection-task defaults are used as-is (not overridden) -
confirmed values from the installed package:

| param | default | effect |
|---|---|---|
| `mosaic` | 1.0 | 100% chance of combining 4 training images into one tiled sample |
| `close_mosaic` | 10 | mosaic disabled for the final 10 epochs, to stabilize training on realistic (non-tiled) images before it stops |
| `fliplr` | 0.5 | 50% chance of horizontal flip |
| `flipud` | 0.0 | vertical flip disabled |
| `hsv_h` / `hsv_s` / `hsv_v` | 0.015 / 0.7 / 0.4 | hue/saturation/brightness jitter |
| `translate` / `scale` | 0.1 / 0.5 | random translation/zoom jitter |
| `degrees` / `shear` / `perspective` | 0.0 / 0.0 / 0.0 | rotation/shear/perspective warping disabled |
| `mixup` / `copy_paste` | 0.0 / 0.0 | disabled |

**Why keep these rather than tune them:** augmentation happens only during
training, generating varied versions of training images to teach the
detector "this visual pattern is a declaration region" more robustly - it
does not affect the box coordinates fed downstream at inference time, so a
horizontally-flipped training image (making printed text unreadable in that
particular augmented sample) is not a problem the way it would be for an
OCR model - YOLO here only ever needs to learn *where* a region is, not
read it. Given a small dataset, keeping augmentation on (rather than
disabling it) is the right default - it's one of the more effective ways to
get more effective training signal out of limited real photos. `degrees`/
`shear`/`perspective` being off is also a reasonable default: real
inspection photos already have natural perspective/rotation variation (per
`training/COLLECTION_PLAN.md`'s diversity axes) - synthetic rotation on top
of an already-diverse real dataset adds less than it would for a narrow,
studio-shot dataset.

Note `augment: False` in Ultralytics' own config is a *different* setting
(test-time augmentation during `predict()`/inference, not training) - not
to be confused with the training-time augmentation table above, which is
active by its individual parameter values regardless of that flag.

**Revisit if:** the pilot (Phase 10) or full-dataset evaluation
(Phase 12) shows a specific class struggling in a way that traces to a
specific augmentation choice (e.g. if `mfg_date_batch`'s tiny text becomes
illegible under the default HSV/scale jitter more than it would in real
deployment conditions) - tune only in response to an observed problem, not
preemptively.

## Device & workers

**`device=cpu`, `workers=0`** - no GPU available locally
(`YOLOService._detect_device()` already auto-detects CUDA and would use it
if present, matching the production `yolo_service.py` behavior - this
training script and the production service share the same detection logic
philosophy, just implemented separately since one trains and one infers).
`workers=0` avoids Windows multiprocessing dataloader overhead/quirks at
this small dataset scale, where the dataloading itself is not the
bottleneck.

## Validation

Built into the training loop itself (`val=True` is Ultralytics' default,
not overridden) - every epoch's model is validated against `images/val`
to drive early stopping and to select which epoch's weights become
`best.pt`. This is separate from running `evaluate_yolo.py` afterward,
which re-validates the *final* `best.pt` checkpoint specifically and
produces the more detailed per-class/confusion report this project's
`evaluate_yolo.py` adds on top of Ultralytics' own output (see
`training/EVALUATION_CONFIG.md`).

## Output paths

- Full run artifacts (all epoch weights, loss curves, label distribution
  plots): `training/runs/legal_lense_yolo26/` - gitignored, regenerated
  each run.
- The one checkpoint that matters going forward:
  `models/legal_lense_yolo26.pt` (relative to `Portal/`) - copied there
  automatically from the run's `weights/best.pt` at the end of
  `train_yolo.py`, matching the `YOLO_MODEL_PATH` convention already
  documented in `backend/ocr/README.md` and `.env.example`.

## Explicitly not configured here

Test-time behavior at inference (confidence threshold, device) is
`YOLOService`'s concern (`backend/ocr/yolo_service.py`,
`YOLO_CONFIDENCE_THRESHOLD` / `YOLO_DEVICE` env vars) - separate from this
training configuration, and unaffected by anything in this document.
