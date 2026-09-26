from __future__ import annotations

import pytest

from backend.services.confidence_gate import evaluate_confidence_gate


def test_confidence_gate_allows_high_confidence_verdict():
    result = evaluate_confidence_gate(
        ocr_confidence=0.94,
        vision_confidence=0.97,
        barcode_confidence=0.96,
        image_integrity_confidence=0.92,
    )
    assert result["decision"] == "eligible"
    assert result["score"] >= 0.90


def test_confidence_gate_rejects_below_threshold_with_reason():
    result = evaluate_confidence_gate(
        ocr_confidence=0.80,
        vision_confidence=0.82,
        barcode_confidence=0.85,
        image_integrity_confidence=0.76,
    )
    assert result["decision"] == "officer_review"
    assert "below" in result["review_reason"].lower()


def test_confidence_gate_exact_threshold_allowed():
    result = evaluate_confidence_gate(
        ocr_confidence=0.90,
        vision_confidence=0.90,
        barcode_confidence=0.90,
        image_integrity_confidence=0.90,
    )
    assert result["decision"] == "eligible"
    assert result["score"] == pytest.approx(0.90, abs=1e-9)


def test_confidence_gate_zeros_and_invalid_values_are_rejected():
    result = evaluate_confidence_gate(
        ocr_confidence=0.0,
        vision_confidence=0.0,
        barcode_confidence="bad",
        image_integrity_confidence=None,
    )
    assert result["decision"] == "officer_review"
    assert "invalid" in result["review_reason"].lower() or "missing" in result["review_reason"].lower()


def test_confidence_gate_prevents_bypass():
    result = evaluate_confidence_gate(
        ocr_confidence=0.95,
        vision_confidence=0.95,
        barcode_confidence=0.75,
        image_integrity_confidence=0.95,
    )
    assert result["decision"] == "officer_review"
    assert result["bypass_blocked"] is True
