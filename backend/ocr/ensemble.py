from __future__ import annotations

from typing import Any

from backend.services.audit import append_audit_event


def _normalize_detections(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    detections = raw.get("detections") or []
    if not isinstance(detections, list):
        return []
    results: list[dict[str, Any]] = []
    for detection in detections:
        if not isinstance(detection, dict):
            continue
        bbox = detection.get("bbox") or [0, 0, 0, 0]
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        polygon = detection.get("polygon")
        if isinstance(polygon, list) and polygon and all(isinstance(item, (list, tuple)) for item in polygon):
            polygon_value = [list(point) for point in polygon]
        else:
            polygon_value = [
                [int(bbox[0]), int(bbox[1])],
                [int(bbox[2]), int(bbox[1])],
                [int(bbox[2]), int(bbox[3])],
                [int(bbox[0]), int(bbox[3])],
            ]
        text = str(detection.get("text") or "").strip()
        try:
            confidence = float(detection.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        results.append(
            {
                "text": text,
                "confidence": max(0.0, min(1.0, confidence)),
                "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
                "polygon": polygon_value,
            }
        )
    return results


def _bbox_area(bbox: list[int]) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _intersection_over_union(a: list[int], b: list[int]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter_w = max(0, x2 - x1)
    inter_h = max(0, y2 - y1)
    inter = inter_w * inter_h
    union = _bbox_area(a) + _bbox_area(b) - inter
    if union <= 0:
        return 0.0
    return inter / union


def _combine_pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    total_weight = left["confidence"] + right["confidence"]
    x1 = int(round((left["bbox"][0] * left["confidence"] + right["bbox"][0] * right["confidence"]) / total_weight))
    y1 = int(round((left["bbox"][1] * left["confidence"] + right["bbox"][1] * right["confidence"]) / total_weight))
    x2 = int(round((left["bbox"][2] * left["confidence"] + right["bbox"][2] * right["confidence"]) / total_weight))
    y2 = int(round((left["bbox"][3] * left["confidence"] + right["bbox"][3] * right["confidence"]) / total_weight))
    merged_confidence = total_weight / 2.0
    candidate_text = left["text"] if left["confidence"] >= right["confidence"] else right["text"]
    if left["text"] and right["text"] and left["text"] != right["text"]:
        candidate_text = left["text"] if left["confidence"] > right["confidence"] else right["text"]
    return {
        "text": candidate_text,
        "confidence": max(0.0, min(1.0, merged_confidence)),
        "bbox": [x1, y1, x2, y2],
        "polygon": [
            [x1, y1],
            [x2, y1],
            [x2, y2],
            [x1, y2],
        ],
        "sources": ["paddleocr", "tesseract"],
        "provenance": ["paddleocr", "tesseract"],
        "vote_breakdown": {"paddleocr": left["confidence"], "tesseract": right["confidence"]},
    }


def _merge_tracks(paddle_detections: list[dict[str, Any]], tesseract_detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    matched_tesseract: set[int] = set()
    for idx, paddle in enumerate(paddle_detections):
        best_match = None
        best_iou = 0.0
        for tidx, tess in enumerate(tesseract_detections):
            if tidx in matched_tesseract:
                continue
            if paddle["confidence"] + tess["confidence"] <= 0:
                continue
            iou = _intersection_over_union(paddle["bbox"], tess["bbox"])
            if iou >= 0.50 and iou > best_iou:
                best_iou = iou
                best_match = tidx
        if best_match is not None:
            matched_tesseract.add(best_match)
            merged.append({
                **_combine_pair(paddle, tesseract_detections[best_match]),
                "iou": best_iou,
            })
        else:
            merged.append({
                **paddle,
                "sources": ["paddleocr"],
                "provenance": ["paddleocr"],
                "iou": None,
            })
    for tidx, tess in enumerate(tesseract_detections):
        if tidx in matched_tesseract:
            continue
        merged.append({
            **tess,
            "sources": ["tesseract"],
            "provenance": ["tesseract"],
            "iou": None,
        })
    return merged


def run_ensemble(paddle_result: Any, tesseract_result: Any, *, db: Any | None = None, entity_id: str = "ocr_ensemble_run", user_id: Any | None = None, actor_role: str | None = None) -> dict[str, Any]:
    paddle_detections = _normalize_detections(paddle_result) if isinstance(paddle_result, dict) and paddle_result.get("success") else []
    tesseract_detections = _normalize_detections(tesseract_result) if isinstance(tesseract_result, dict) and tesseract_result.get("success") else []

    if isinstance(tesseract_result, dict) and tesseract_result.get("error_code") == "not_available":
        tesseract_detections = []

    if not paddle_detections and not tesseract_detections:
        result = {
            "status": "ok",
            "mode": "no_data",
            "regions": [],
            "provenance": [],
            "audit_event": {"eventType": "ocr_ensemble_run", "action": "ensemble_empty"},
        }
    elif paddle_detections and not tesseract_detections:
        regions = [
            {**d, "sources": ["paddleocr"], "provenance": ["paddleocr"], "iou": None}
            for d in paddle_detections
        ]
        result = {
            "status": "ok",
            "mode": "paddleocr_only",
            "regions": regions,
            "provenance": [{"text": region["text"], "sources": region["sources"], "bbox": region["bbox"]} for region in regions],
            "audit_event": {"eventType": "ocr_ensemble_run", "action": "paddleocr_only"},
        }
    else:
        merged = _merge_tracks(paddle_detections, tesseract_detections)
        result = {
            "status": "ok",
            "mode": "weighted_soft_vote",
            "regions": merged,
            "provenance": [{"text": region["text"], "sources": region.get("sources", []), "bbox": region["bbox"], "iou": region.get("iou")} for region in merged],
            "audit_event": {"eventType": "ocr_ensemble_run", "action": "weighted_soft_vote"},
        }

    if db is not None:
        event = append_audit_event(
            db,
            event_type="ocr_ensemble_run",
            entity_type="ocr",
            entity_id=str(entity_id),
            user_id=user_id,
            actor_role=actor_role,
            action=result["audit_event"]["action"],
            details={
                "mode": result["mode"],
                "tracks": {"paddleocr": bool(paddle_detections), "tesseract": bool(tesseract_detections)},
                "regions": len(result["regions"]),
                "provenance": result.get("provenance", []),
            },
        )
        result["audit_event"] = event
    return result
