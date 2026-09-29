# PDP v1 Annotation Contract

Dataset: `legal-lense-pdp`, version `1.0.0`.

The only class is `principal_display_panel` (class 0). Annotate the visible
principal display panel region as one tight axis-aligned rectangle in the
original image frame, converted to the normalized YOLO `cx cy width height`
format. Do not annotate text declarations as separate boxes. Do not use a
generic object detector to pre-label this class.

For a frame with no visible qualifying PDP, provide an empty label file and
import it with explicit negative confirmation. Nutrition tables, barcode
regions, side panels, secondary packs, decoration, background rectangles and
unrelated objects are negative content, not PDP annotations.

Use `training/manage_pdp_dataset.py import` for local images only after the
operator confirms ownership or an applicable license. Record one or more
conditions such as perspective, glare, low light, curvature, occlusion, or a
hard-negative type. `product_identity` is hashed before storage and all views
of the same product identity must stay in one split.

The importer records source, license, category, operator, optional capture
date, image/label hashes, dimensions, annotation status and version. It does
not download images, infer rights, generate labels, or fabricate products.
Review annotations manually before training; the importer validates format,
not semantic correctness.

Quick workflow:

```powershell
backend\.venv311\Scripts\python.exe training\manage_pdp_dataset.py validate
backend\.venv311\Scripts\python.exe training\train_pdp.py --epochs 60 --device cpu
backend\.venv311\Scripts\python.exe training\evaluate_pdp.py --split test --device cpu
```

Training is refused until `train` and `val` contain labeled, provenance-
complete images. The detector remains disabled/experimental until independent
review approves the checkpoint, dataset manifest, and held-out validation
report hashes.
