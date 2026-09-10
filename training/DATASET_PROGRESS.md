# LegalLense YOLO26 — dataset progress tracker (Phase 14)

Whole-dataset view, aggregating both Ayush's and Sarthak's work (see
`training/WORK_SPLIT.md` for individual assignment tracking - that tracks
*who's* doing *what*; this tracks the *dataset's* overall state). Update
this as collection scales from the Phase 10 pilot up to the full
~300-400 photo target from `training/COLLECTION_PLAN.md`.

**Live counts:** run `training/validate_dataset.py` any time for an
up-to-date per-class instance count across train/val/test - don't
hand-maintain those numbers here; use the tracker below for the things the
validator *can't* automatically know (review/acceptance status, per-category
progress against the collection plan).

## Per-category progress

| category | target photos (COLLECTION_PLAN.md) | collected | labeled | reviewed | accepted | rejected | status |
|---|---|---|---|---|---|---|---|
| Food packages | ~30-45 | 0 | 0 | 0 | 0 | 0 | not started |
| Garments / textiles | ~15-25 | 0 | 0 | 0 | 0 | 0 | not started |
| Cosmetics / personal care | ~20-30 | 0 | 0 | 0 | 0 | 0 | not started |
| Household products | ~20-35 | 0 | 0 | 0 | 0 | 0 | not started |
| Electrical appliances | ~10-15 | 0 | 0 | 0 | 0 | 0 | not started |
| Pharmaceuticals (OTC/supplements) | ~15-25 | 0 | 0 | 0 | 0 | 0 | not started |
| Beverages | ~15-25 | 0 | 0 | 0 | 0 | 0 | not started |
| Loose-weight/bulk staples (shared, unit_sale_price) | ~40-60 | 0 | 0 | 0 | 0 | 0 | not started |
| **Total** | **~300-400** | **0** | **0** | **0** | **0** | **0** | pilot pending |

Column definitions:
- **Collected** - photos taken, sitting in a pre-labeling holding area (or
  directly in `images/<split>` if labeling immediately).
- **Labeled** - has a corresponding `.txt` in `labels/<split>`.
- **Reviewed** - has gone through the QC cross-review process
  (`training/QC_PROCESS.md`).
- **Accepted** - passed review, counted toward real dataset totals.
- **Rejected** - moved to `training/rejected_images/` per
  `training/LABELING_GUIDE.md` section G.4, or failed QC review and sent
  back for relabeling (track the relabel separately once done, don't just
  delete the row).

## Per-class instance progress

Pull current numbers from `validate_dataset.py`'s output and paste them
here at each milestone (pilot complete, 50% of full target, full target) -
a point-in-time snapshot is more useful for tracking trend over time than
only ever looking at the live number.

| class | min target | ambitious target | latest snapshot (train) | latest snapshot (val) | snapshot date |
|---|---|---|---|---|---|
| `product_identity` | 100 | 200 | 0 | 0 | - |
| `mrp` | 100 | 200 | 0 | 0 | - |
| `unit_sale_price` | 80 | 150 | 0 | 0 | - |
| `net_quantity` | 100 | 200 | 0 | 0 | - |
| `entity_details` | 100 | 200 | 0 | 0 | - |
| `country_of_origin` | 100 | 180 | 0 | 0 | - |
| `mfg_date_batch` | 100 | 180 | 0 | 0 | - |
| `consumer_care` | 100 | 180 | 0 | 0 | - |

## Missing classes / at-risk items

Running list - add an entry whenever `validate_dataset.py`'s "zero
instances" warning or a review session surfaces a real gap, remove it once
resolved:

- *(none yet - dataset collection hasn't started)*

## Milestones

- [ ] **Pilot** (Phase 10): 30-40 images, train/val only, pipeline validated
      end-to-end (see `training/PILOT_PLAN.md`)
- [ ] **Pilot findings addressed**: any taxonomy/labeling-convention issues
      found during the pilot fixed before scaling up
- [ ] **~50% of full target**: ~150-200 photos, first `test/` split
      population begins
- [ ] **Full target reached**: ~300-400 photos, `training/COLLECTION_PLAN.md`
      targets met or consciously adjusted
- [ ] **Full QC pass complete** (`training/QC_PROCESS.md`)
- [ ] **Full training + evaluation run** on the complete dataset
- [ ] **Pipeline validation** (`training/PIPELINE_VALIDATION_PLAN.md`)
      shows real practical improvement over the pretrained baseline
