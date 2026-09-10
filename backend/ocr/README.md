# LegalLense OCR Layer (YOLO26 + PaddleOCR)

Text-extraction only. Given a product/label image, this module returns
recognized text, per-line confidence, and bounding boxes as structured JSON.
It makes **no compliance decisions** - that is the job of the deterministic
Legal Metrology rules engine (`backend/services/compliance_engine.py`) that
sits downstream of this output.

```
Product Image -> optional OpenCV preprocessing -> YOLO26 localization -> crop -> PaddleOCR -> OCR JSON
```

YOLO26 is an optional pre-step, not a replacement for anything: if it is
disabled, unavailable, or finds nothing usable in a given image, PaddleOCR
still runs on the full, un-cropped image exactly as it did before YOLO26 was
introduced. See "YOLO26 localization" below for what it does and does not do.

This module is plain Python with no HTTP/FastAPI dependency, so it can be
imported directly by a FastAPI route without modification:

```python
from backend.ocr.paddle_ocr_service import PaddleOCRService
from backend.ocr.yolo_service import YOLOService

service = PaddleOCRService()          # loads PaddleOCR models once, reused after
yolo = YOLOService()                  # loads the YOLO26 model once, reused after
result = service.run("label.jpg", yolo_service=yolo)   # -> OCRResult (pydantic model)
result.model_dump()                   # -> plain dict, ready for JSONResponse
```

## Files

| File | Purpose |
|---|---|
| `schemas.py` | `Detection`, `ObjectDetection`, `BoundingBox`, `OCRResult`, `OCRError` pydantic models - the stable output contract |
| `preprocessing.py` | Optional, individually-toggleable OpenCV steps (resize/grayscale/contrast/denoise) |
| `yolo_service.py` | `YOLOService` - loads YOLO26 once, runs object localization, returns detections + a crop region |
| `paddle_ocr_service.py` | `PaddleOCRService` - validates input, optionally crops via YOLO26, runs PaddleOCR, returns `OCRResult` |
| `test_ocr.py` | CLI to run OCR on one image and save the JSON result |
| `test_yolo.py` | CLI to run YOLO26 alone on one image and save the JSON detections |

## Install

From `stitch_legallense_compliance_portal/` (this project's root):

```bash
uv venv --python 3.11 backend/.venv
uv pip install --python backend/.venv paddlepaddle paddleocr opencv-python-headless setuptools
```

(`setuptools` is required at runtime by `paddle.utils.cpp_extension` even
though nothing here compiles a C++ extension - paddle imports it unconditionally.)

Or with plain pip inside an activated venv:

```bash
python -m venv backend/.venv
backend\.venv\Scripts\activate
pip install --no-deps -r backend/requirements.txt -c backend/constraints.txt
```

`backend/requirements.txt` is a full `pip freeze` of a known-working
environment (CPU-only, Python 3.11.16). The packages installed intentionally
were `paddlepaddle`, `paddleocr`, `opencv-python-headless`,
`opencv-contrib-python`, `setuptools`, `fastapi`, `uvicorn`, and
`ultralytics` (for YOLO26) - everything else in that file is a transitive
dependency pulled in automatically.

**Always install with `--no-deps -c backend/constraints.txt`, not a bare
`pip install -r backend/requirements.txt`.** `ultralytics` declares a
dependency on plain `opencv-python` (the GUI build, needs system libs like
`libGL.so` that are absent in headless/server/Docker environments) - having
it installed alongside `opencv-python-headless` causes them to silently
clobber each other's files in the shared `cv2/` site-packages directory
(this happened once during development and produced a `WinError 5: Access
is denied` on `cv2.pyd` when a running server held the file open).
`--no-deps` tells pip to install exactly the pinned versions in
`requirements.txt`, in file order, without re-resolving each package's own
declared dependencies, so `opencv-python` is never pulled back in.

Note that `opencv-contrib-python` is a *separate* keep: it is NOT removed.
PaddleOCR's backend (`paddlex`) does its own internal dependency check,
independent of pip, that hard-requires `opencv-contrib-python==4.10.0.84` by
exact name+version before it will construct a pipeline at all - removing it
breaks PaddleOCR (`DependencyError: "OCR" requires additional
dependencies`), confirmed by testing. `requirements.txt` installs it first
and `opencv-python-headless` second so headless's files end up as the actual
on-disk/imported `cv2` (verified via `cv2.getBuildInformation()` ->
`GUI: NONE`), while `opencv-contrib-python`'s pip metadata - which is all
paddlex actually inspects - stays present to satisfy that check. See
`backend/constraints.txt` for the full explanation and for what to do if you
upgrade a package (e.g. `ultralytics`) by hand instead of via this file.

## Run

```bash
backend\.venv\Scripts\python.exe -m backend.ocr.test_ocr backend/test_images/synthetic_label.png
backend\.venv\Scripts\python.exe -m backend.ocr.test_ocr path\to\your\photo.jpg --preprocess
```

This prints every detection (text + confidence + bbox) and writes
`backend/ocr_output/<image_name>_ocr.json`.

## Known CPU issue on this machine (and the fix already applied)

With `paddlepaddle==3.3.1` + the default PP-OCRv6 models, running on this
CPU's oneDNN backend crashes with:

```
NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
not support [pir::ArrayAttribute<pir::DoubleAttribute>]
```

This is a real PaddlePaddle/PaddleX oneDNN-PIR-executor incompatibility, not
an OCR logic bug. The fix that was verified to work is disabling MKL-DNN:

```python
PaddleOCR(..., enable_mkldnn=False)
```

`PaddleOCRService` sets `enable_mkldnn=False` by default for exactly this
reason (`DEFAULT_ENABLE_MKLDNN` in `paddle_ocr_service.py`). It costs some
CPU inference speed versus MKL-DNN acceleration. If you upgrade
`paddlepaddle` later and this is fixed upstream, flip the default back to
`True` and re-test - `PaddleOCRService(enable_mkldnn=True)` is fully
supported as an override in the meantime.

## Test image

`backend/test_images/synthetic_label.png` is a **synthetically generated**
label (drawn with PIL: product name, manufacturer, net quantity, MRP, mfg
date, consumer care, country of origin) used to validate the pipeline
end-to-end without a real product photo on hand. It is not a photograph and
should not be treated as a real-world OCR accuracy benchmark - it exists to
prove text detection, confidence scoring, bounding boxes, and JSON
serialization all work correctly. **Run a real photographed label through
`test_ocr.py` before trusting this for actual product images** - real
photos introduce glare, blur, skew, and curved surfaces that a rendered
image does not.

## Preprocessing

Off by default. Enable selectively:

```python
from backend.ocr.preprocessing import PreprocessOptions

opts = PreprocessOptions(resize=True, denoise=True, enhance_contrast=True, grayscale=False)
result = service.run("photo.jpg", preprocess_options=opts)
```

- `resize`: downscale only if the longer side exceeds `max_side` (default 2000px) - keeps inference fast on huge phone photos, never upscales.
- `denoise`: `cv2.fastNlMeansDenoisingColored` - helps grainy/low-light shots.
- `enhance_contrast`: CLAHE on the L channel (LAB color space) - helps faint/low-contrast print.
- `grayscale`: converts to grayscale (kept 3-channel since PaddleOCR expects color input).

## Output shape

```json
{
  "success": true,
  "image": "product.jpg",
  "image_path": "backend/test_images/product.jpg",
  "full_text": "HERBAL GLOW HANDWASH\nNet Quantity: 250 ml\n...",
  "detections": [
    {
      "text": "Net Quantity: 250 ml",
      "confidence": 0.9998,
      "bbox": [38, 218, 300, 245],
      "polygon": [[38, 218], [300, 218], [300, 245], [38, 245]]
    }
  ],
  "detection_count": 10,
  "preprocessing_applied": false,
  "object_detections": [
    {
      "class_id": 39,
      "class_name": "bottle",
      "confidence": 0.87,
      "bbox": { "x1": 42, "y1": 18, "x2": 512, "y2": 900 }
    }
  ],
  "region_cropped": true,
  "error": null
}
```

`bbox` is the axis-aligned rectangle `[x1, y1, x2, y2]`; `polygon` preserves
PaddleOCR's original (usually 4-point) detection quadrilateral so no spatial
information from rotated/skewed text is lost. Both are int pixel
coordinates in the (possibly YOLO-cropped, then preprocessed/resized) image
that was actually fed to PaddleOCR.

`object_detections` are YOLO26's raw, generic object detections (see "YOLO26
localization" below) - empty whenever YOLO is disabled, unavailable, or
found nothing above the confidence threshold. `region_cropped` is `true`
only when OCR actually ran on a YOLO-cropped sub-image rather than the full
photo.

## Error handling

`PaddleOCRService.run()` never raises for expected failure modes - it always
returns an `OCRResult`, with `success=False` and a populated `error` for:

- `file_not_found` - missing path
- `unsupported_file_type` - extension not in `.jpg/.jpeg/.png/.webp`
- `unreadable_image` - empty file or corrupt/undecodable image data
- `preprocessing_failed` - an OpenCV step raised
- `engine_init_failed` - PaddleOCR/PaddlePaddle failed to construct
- `inference_failed` - `.predict()` raised
- `empty_result` - OCR ran successfully but found no text (`success=True`, since this isn't an error condition, just an empty label/photo)

## YOLO26 localization

`yolo_service.py` wraps the official [Ultralytics](https://docs.ultralytics.com/)
`YOLO26` model to locate the product/label in a photo before OCR runs, so
PaddleOCR can focus on the label instead of a busy background (a hand, a
shelf, other packages, etc).

**What it does:** returns generic object detections (class name, confidence,
bounding box) using the pretrained `yolo26n.pt` checkpoint, and crops the
image to the single highest-confidence detection (padded 5%) before handing
it to PaddleOCR.

**What it explicitly does NOT do:** the pretrained checkpoint only knows
generic COCO classes (e.g. `bottle`, `book`, `box`) - it has **no concept of
Legal Metrology fields** (MRP, Net Quantity, Manufacturer, Country of Origin,
Consumer Care, ...). Nothing in this codebase claims otherwise. Field-level
understanding is entirely PaddleOCR's (text) and the compliance engine's
(rules) job, unchanged by this integration.

**Fail-safe by design:** YOLO is never a single point of failure for OCR.
`PaddleOCRService.run()` catches every YOLO error (model missing, import
failure, inference error, no usable detection) internally, logs it, and
falls through to running OCR on the original, un-cropped image. A broken or
absent YOLO model degrades OCR to its pre-YOLO26 behavior; it never crashes
the request.

### Configuration (`Portal/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `YOLO_ENABLED` | `true` | Master on/off switch. `false` skips YOLO entirely (`_yolo_service = None` in `api/ocr.py`). |
| `YOLO_MODEL_PATH` | `yolo26n.pt` | Model name (auto-downloaded by ultralytics) or a path to a custom checkpoint. |
| `YOLO_CONFIDENCE_THRESHOLD` | `0.25` | Minimum detection confidence. |
| `YOLO_DEVICE` | *(blank = auto)* | `cpu` or `cuda:0` to force a device; blank auto-detects via `torch.cuda.is_available()`. |

The model path is read in exactly one place (`yolo_service.py`'s
`DEFAULT_MODEL_PATH`) and never hardcoded elsewhere, so swapping in a future
custom-trained checkpoint is a one-line env change:

```bash
YOLO_MODEL_PATH=models/legal_lense_yolo26.pt
```

No code changes are required for that swap - `YOLOService` and every caller
are checkpoint-agnostic.

### Install

Already covered by installing `backend/requirements.txt` (see "Install" above
- use `pip install --no-deps -r backend/requirements.txt -c backend/constraints.txt`,
**not** a bare `pip install -U ultralytics`, which would pull in plain
`opencv-python` and reintroduce the cv2 conflict described there). Ultralytics
pulls in `torch`/`torchvision` automatically; the CPU build is used unless
CUDA is available. On first run, ultralytics downloads `yolo26n.pt` (a few
MB) into its cache directory if not already present.

### Run standalone

```bash
backend\.venv311\Scripts\python.exe -m backend.ocr.test_yolo backend/test_images/synthetic_label.png
```

Prints every detection (class, confidence, bbox) and writes
`backend/ocr_output/<image_name>_yolo.json`.

### CPU/GPU behavior

`YOLOService._detect_device()` picks `cuda:0` if `torch.cuda.is_available()`,
otherwise `cpu`, unless `YOLO_DEVICE` forces one. Development machines
without a GPU run CPU inference automatically - no CUDA is assumed or
required to start the backend or process an image.

### Memory management

- The model is loaded once per process (`YOLOService._model_cache`, a
  class-level dict keyed by `(model_path, device)`) and reused for every
  request - never reloaded per image.
- Cropping (`image[y1:y2, x1:x2]`) is a NumPy view/slice, not a full extra
  copy of the source image.
- Inference results/boxes are dereferenced (`del results`) as soon as the
  plain-Python `ObjectDetection` list is built, so raw tensors don't
  accumulate across requests.
- Uploaded images are already written to, and cleaned up from, the OS temp
  dir by `api/ocr.py` (unchanged by this integration).

## Explicitly out of scope for this module

- Legal Metrology rule evaluation / compliance verdicts
- Qwen3-VL structured field extraction
- Custom-trained YOLO weights (the pretrained `yolo26n.pt` COCO checkpoint is
  used as-is; training a LegalLense-specific model is future work - the
  architecture supports swapping it in via `YOLO_MODEL_PATH` with no code
  changes)
- Any FastAPI routes beyond what already exists (this package is
  framework-independent by design; it's wired into
  `backend/api/ocr.py::run_ocr`)
