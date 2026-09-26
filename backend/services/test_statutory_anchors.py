from __future__ import annotations

from backend.services.statutory_anchors import classify_anchor, is_valid_bis_registration, is_valid_ean13, is_valid_fssai


def test_fssai_anchor_valid_invalid_and_ambiguous():
    valid = "11422010000018"
    assert is_valid_fssai(valid) is True
    assert classify_anchor(valid)["anchor_type"] == "fssai"
    assert classify_anchor(valid)["status"] == "valid"

    invalid = "11422010000017"
    assert is_valid_fssai(invalid) is False
    assert classify_anchor(invalid)["status"] == "invalid"

    ambiguous = "1142201000001"
    assert classify_anchor(ambiguous)["status"] == "ambiguous"
    assert classify_anchor(ambiguous)["anchor_type"] == "fssai"


def test_bis_registration_anchor_valid_invalid_and_ambiguous():
    valid = "R-12345678"
    assert is_valid_bis_registration(valid) is True
    assert classify_anchor(valid)["anchor_type"] == "bis_registration"
    assert classify_anchor(valid)["status"] == "valid"

    invalid = "R-1234567"
    assert is_valid_bis_registration(invalid) is False
    assert classify_anchor(invalid)["status"] == "invalid"

    ambiguous = "R-12345"
    assert classify_anchor(ambiguous)["status"] == "ambiguous"
    assert classify_anchor(ambiguous)["anchor_type"] == "bis_registration"


def test_ean13_anchor_valid_invalid_and_ambiguous():
    valid = "8901234567891"
    assert is_valid_ean13(valid) is True
    assert classify_anchor(valid)["anchor_type"] == "ean13"
    assert classify_anchor(valid)["status"] == "valid"

    invalid = "8901234567892"
    assert is_valid_ean13(invalid) is False
    assert classify_anchor(invalid)["status"] == "invalid"

    ambiguous = "890123456789"
    assert classify_anchor(ambiguous)["status"] == "ambiguous"
    assert classify_anchor(ambiguous)["anchor_type"] == "ean13"


def test_classify_anchor_unknown_inputs_are_not_confused_with_known_anchors():
    result = classify_anchor("ABC-DEF")
    assert result["anchor_type"] == "unknown"
    assert result["status"] in {"invalid", "ambiguous"}
