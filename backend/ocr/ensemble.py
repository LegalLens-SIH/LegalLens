from __future__ import annotations

import math
import re
from typing import Any

from backend.services.audit import append_audit_event


def _normalize_detections(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("detections"), list):
        return []
    results = []
    for detection in raw["detections"][:500]:
        if not isinstance(detection, dict):
            continue
        try:
            values = [float(value) for value in detection.get("bbox", [])]
            confidence = float(detection.get("confidence", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if len(values) != 4 or not all(math.isfinite(value) for value in values):
            continue
        x1, y1, x2, y2 = values
        if x2 <= x1 or y2 <= y1 or not math.isfinite(confidence):
            continue
        bbox = [round(x1), round(y1), round(x2), round(y2)]
        text = str(detection.get("text") or "").strip()[:512]
        confidence = max(0.0, min(1.0, confidence))
        results.append({
            "text": text, "confidence": confidence, "bbox": bbox,
            "polygon": detection.get("polygon") or [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]],
        })
    return results


def _bbox_area(bbox: list[int]) -> float:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _intersection_over_union(a: list[int], b: list[int]) -> float:
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = _bbox_area(a) + _bbox_area(b) - inter
    return inter / union if union else 0.0


def _text_similarity(a: str, b: str) -> float:
    left = re.sub(r"\s+", " ", a.casefold()).strip()
    right = re.sub(r"\s+", " ", b.casefold()).strip()
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    # Levenshtein similarity keeps OCR substitutions visible while allowing
    # geometry matching to handle punctuation/case differences separately.
    previous = list(range(len(right) + 1))
    for i, ca in enumerate(left, 1):
        current = [i]
        for j, cb in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


def _geometry_score(a: list[int], b: list[int]) -> float:
    iou = _intersection_over_union(a, b)
    ah, bh = max(1, a[3] - a[1]), max(1, b[3] - b[1])
    aw, bw = max(1, a[2] - a[0]), max(1, b[2] - b[0])
    vertical_overlap = max(0, min(a[3], b[3]) - max(a[1], b[1])) / min(ah, bh)
    horizontal_overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) / min(aw, bw)
    containment_overlap = min(1.0, vertical_overlap) * min(1.0, horizontal_overlap)
    if containment_overlap >= 0.5:
        return max(iou, 0.7 * containment_overlap)
    vertical_centers = abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2)
    horizontal_gap = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    same_line = vertical_centers <= 0.5 * max(ah, bh) and horizontal_gap <= 0.5 * max(ah, bh)
    return max(iou, 0.5 if same_line else 0.0)


def _merge_tracks(paddle: list[dict[str, Any]], tesseract: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pairs: list[tuple[float, int, int]] = []
    for pi, left in enumerate(paddle):
        for ti, right in enumerate(tesseract):
            # Zero-confidence boxes do not provide enough evidence to claim
            # cross-engine correspondence, even when their geometry overlaps.
            if left["confidence"] <= 0 and right["confidence"] <= 0:
                continue
            geometry = _geometry_score(left["bbox"], right["bbox"])
            if geometry >= 0.45:
                pairs.append((geometry, pi, ti))
    used_p: set[int] = set()
    used_t: set[int] = set()
    matches: dict[int, tuple[int, float]] = {}
    for score, pi, ti in sorted(pairs, reverse=True):
        if pi not in used_p and ti not in used_t:
            used_p.add(pi); used_t.add(ti); matches[pi] = (ti, score)

    regions: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    for pi, left in enumerate(paddle):
        if pi not in matches:
            regions.append({**left, "sources": ["paddleocr"], "provenance": ["paddleocr"], "iou": None, "status": "single_source"})
            continue
        ti, geometry = matches[pi]
        right = tesseract[ti]
        same_text = re.sub(r"\s+", " ", left["text"].casefold()).strip() == re.sub(r"\s+", " ", right["text"].casefold()).strip()
        text_similarity = _text_similarity(left["text"], right["text"])
        w1, w2 = max(left["confidence"], 0.01), max(right["confidence"], 0.01)
        total = w1 + w2
        bbox = [round((left["bbox"][i] * w1 + right["bbox"][i] * w2) / total) for i in range(4)]
        region = {
            "text": left["text"], "confidence": left["confidence"], "bbox": bbox,
            "polygon": left["polygon"], "sources": ["paddleocr", "tesseract"],
            "provenance": ["paddleocr", "tesseract"], "iou": _intersection_over_union(left["bbox"], right["bbox"]),
            "geometry_score": round(geometry, 4), "text_similarity": round(text_similarity, 4),
            "status": "consensus" if same_text else "disagreement",
            "authoritative_source": "paddleocr", "tesseract_text": right["text"],
            "confidence_by_source": {"paddleocr": left["confidence"], "tesseract": right["confidence"]},
            "evidence_region": {"bbox": bbox, "source": "ocr_input_pixels"},
        }
        regions.append(region)
        if not same_text:
            disagreements.append({
                "status": "review_required", "paddleocr_text": left["text"], "tesseract_text": right["text"],
                "paddleocr_confidence": left["confidence"], "tesseract_confidence": right["confidence"],
                "geometry_score": round(geometry, 4), "text_similarity": round(text_similarity, 4),
                "evidence_region": region["evidence_region"], "authoritative_source": "paddleocr",
            })
    for ti, right in enumerate(tesseract):
        if ti not in used_t:
            regions.append({**right, "sources": ["tesseract"], "provenance": ["tesseract"], "iou": None, "status": "tesseract_only_review"})
            disagreements.append({"status": "review_required", "paddleocr_text": None, "tesseract_text": right["text"], "tesseract_confidence": right["confidence"], "evidence_region": {"bbox": right["bbox"], "source": "ocr_input_pixels"}, "authoritative_source": "paddleocr"})
    return regions, disagreements


def run_ensemble(paddle_result: Any, tesseract_result: Any, *, db: Any | None = None, entity_id: str = "ocr_ensemble_run", user_id: Any | None = None, actor_role: str | None = None) -> dict[str, Any]:
    paddle = _normalize_detections(paddle_result) if isinstance(paddle_result, dict) and paddle_result.get("success") else []
    tess = _normalize_detections(tesseract_result) if isinstance(tesseract_result, dict) and tesseract_result.get("success") else []
    regions, disagreements = _merge_tracks(paddle, tess)
    mode = "no_data" if not paddle and not tess else "paddleocr_only" if paddle and not tess else "tesseract_only_review" if tess and not paddle else "comparison_review_only"
    action = "ensemble_empty" if mode == "no_data" else "paddleocr_only" if mode == "paddleocr_only" else "tracks_compared"
    result = {
        "status": "review_required" if disagreements else "ok", "mode": mode, "regions": regions,
        "disagreements": disagreements, "disagreement_count": len(disagreements),
        "provenance": [{"text": r["text"], "sources": r["sources"], "bbox": r["bbox"], "status": r["status"]} for r in regions],
        "authoritative_engine": "paddleocr", "production_authority": False, "used_for_compliance": False,
        "audit_event": {"eventType": "ocr_ensemble_run", "action": action},
    }
    if db is not None:
        result["audit_event"] = append_audit_event(db, event_type="ocr_ensemble_run", entity_type="ocr", entity_id=str(entity_id), user_id=user_id, actor_role=actor_role, action=action, details={"mode": mode, "tracks": {"paddleocr": bool(paddle), "tesseract": bool(tess)}, "regions": len(regions), "disagreement_count": len(disagreements), "used_for_compliance": False})
    return result
