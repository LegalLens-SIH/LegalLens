from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Polygon, box as image_rectangle
from shapely.ops import unary_union

from backend.database import get_database
from backend.services.audit import append_audit_event


def evaluate_pdp_geometry(boxes: list[dict[str, float]], image_shape: tuple[int, int]) -> dict[str, Any]:
    if not boxes:
        result = {"panel_coverage_ratio": 0.0, "status": "review_required", "reasons": ["No panel boxes detected"], "evidence_regions": []}
    elif not isinstance(image_shape, (tuple, list)) or len(image_shape) != 2:
        result = {"panel_coverage_ratio": None, "status": "review_required", "reasons": ["Image dimensions are missing or invalid"], "evidence_regions": []}
    else:
        try:
            height, width = (float(value) for value in image_shape)
        except (TypeError, ValueError):
            height, width = 0.0, 0.0

        if not all(math.isfinite(value) and value > 0 for value in (height, width)):
            result = {"panel_coverage_ratio": None, "status": "review_required", "reasons": ["Image dimensions must be finite and positive"], "evidence_regions": []}
        else:
            image_bounds = image_rectangle(0, 0, width, height)
            geometries = []
            evidence_regions = []
            reasons = []

            for index, panel in enumerate(boxes):
                if not isinstance(panel, dict):
                    reasons.append(f"Panel geometry {index} is not an object")
                    continue

                raw_polygon = panel.get("polygon")
                try:
                    if raw_polygon is not None:
                        coordinates = [(float(point[0]), float(point[1])) for point in raw_polygon if len(point) == 2]
                        if len(coordinates) != len(raw_polygon) or len(coordinates) < 3:
                            raise ValueError
                    else:
                        raw_bbox = panel.get("bbox")
                        if isinstance(raw_bbox, dict):
                            x1, y1, x2, y2 = (float(raw_bbox[key]) for key in ("x1", "y1", "x2", "y2"))
                        elif isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
                            x1, y1, x2, y2 = (float(value) for value in raw_bbox)
                        else:
                            x1, y1, x2, y2 = (float(panel[key]) for key in ("x1", "y1", "x2", "y2"))
                        coordinates = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                except (KeyError, TypeError, ValueError, IndexError):
                    reasons.append(f"Panel geometry {index} has invalid coordinates")
                    continue

                if any(not math.isfinite(value) for point in coordinates for value in point):
                    reasons.append(f"Panel geometry {index} has non-finite coordinates")
                    continue
                if any(x < 0 or x > width or y < 0 or y > height for x, y in coordinates):
                    reasons.append(f"Panel geometry {index} extends outside the image")
                    continue

                polygon = Polygon(coordinates)
                if not polygon.is_valid or polygon.area <= 0 or not image_bounds.covers(polygon):
                    reasons.append(f"Panel geometry {index} is degenerate or invalid")
                    continue

                geometries.append(polygon)
                evidence_regions.append({
                    "region_id": panel.get("region_id"),
                    "evidence_region_ids": panel.get("evidence_region_ids", []),
                    "bbox": [polygon.bounds[0], polygon.bounds[1], polygon.bounds[2], polygon.bounds[3]],
                    "polygon": coordinates,
                    "source_evidence": dict(panel),
                })

            if reasons:
                result = {"panel_coverage_ratio": None, "status": "review_required", "reasons": reasons, "evidence_regions": evidence_regions}
            else:
                coverage = unary_union(geometries).area
                ratio = coverage / (height * width)
                status = "eligible" if ratio >= 0.30 else "review_required"
                result = {
                    "panel_coverage_ratio": round(float(ratio), 4),
                    "status": status,
                    "reasons": [] if status == "eligible" else ["Panel geometry below minimum coverage"],
                    "evidence_regions": evidence_regions,
                }

    try:
        db = get_database()
        append_audit_event(
            db,
            event_type="geometry_calculation",
            entity_type="pdp_geometry",
            entity_id="pdp_geometry",
            actor_role="system",
            action="geometry_assessed",
            details={
                "panel_coverage_ratio": result.get("panel_coverage_ratio"),
                "status": result.get("status"),
                "reasons": result.get("reasons", []),
            },
        )
    except Exception:
        pass
    return result
