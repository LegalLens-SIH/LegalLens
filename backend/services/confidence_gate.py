from __future__ import annotations

import math
from typing import Any

from backend.services.audit import append_audit_event


def _coerce_component(value: Any, *, label: str) -> float:
    if value is None:
        raise ValueError(f"{label} is required")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a numeric confidence score") from exc
    if math.isnan(score) or math.isinf(score):
        raise ValueError(f"{label} must be finite")
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return score


def harmonic_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    clean = [max(0.0, min(1.0, float(v))) for v in values]
    if any(v == 0 for v in clean):
        return 0.0
    return len(clean) / sum(1.0 / v for v in clean)


def evaluate_confidence_gate(
    *,
    ocr_confidence: Any,
    vision_confidence: Any,
    barcode_confidence: Any,
    image_integrity_confidence: Any,
    db: Any | None = None,
    entity_id: str = "confidence_gate",
    user_id: Any | None = None,
    actor_role: str | None = None,
) -> dict[str, Any]:
    try:
        values = [
            _coerce_component(ocr_confidence, label="ocr_confidence"),
            _coerce_component(vision_confidence, label="vision_confidence"),
            _coerce_component(barcode_confidence, label="barcode_confidence"),
            _coerce_component(image_integrity_confidence, label="image_integrity_confidence"),
        ]
    except ValueError as exc:
        result = {
            "decision": "officer_review",
            "score": 0.0,
            "review_reason": f"Missing or invalid confidence input: {exc}",
            "bypass_blocked": True,
            "audit_event": {"eventType": "confidence_gate", "action": "invalid_input"},
        }
        if db is not None:
            result["audit_event"] = append_audit_event(
                db,
                event_type="confidence_gate",
                entity_type="decision",
                entity_id=str(entity_id),
                user_id=user_id,
                actor_role=actor_role,
                action="invalid_input",
                details={"score": 0.0, "review_reason": result["review_reason"]},
            )
        return result

    score = harmonic_mean(values)
    score = round(score, 12)
    threshold = 0.90
    if score >= threshold or abs(score - threshold) < 1e-12:
        result = {
            "decision": "eligible",
            "score": score,
            "review_reason": None,
            "bypass_blocked": True,
            "audit_event": {"eventType": "confidence_gate", "action": "eligible"},
        }
    else:
        result = {
            "decision": "officer_review",
            "score": score,
            "review_reason": f"Confidence gate below threshold ({score:.4f} < {threshold:.2f}); Officer Review required.",
            "bypass_blocked": True,
            "audit_event": {"eventType": "confidence_gate", "action": "officer_review"},
        }

    if db is not None:
        result["audit_event"] = append_audit_event(
            db,
            event_type="confidence_gate",
            entity_type="decision",
            entity_id=str(entity_id),
            user_id=user_id,
            actor_role=actor_role,
            action=result["audit_event"]["action"],
            details={
                "score": result["score"],
                "decision": result["decision"],
                "review_reason": result["review_reason"],
                "components": {"ocr": values[0], "vision": values[1], "barcode": values[2], "image_integrity": values[3]},
                "bypass_blocked": True,
            },
        )
    return result
