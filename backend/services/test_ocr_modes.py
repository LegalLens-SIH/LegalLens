"""Tests for backend/services/ocr_modes.py (Phase 2B)."""

from __future__ import annotations

import pytest

from backend.ocr.preprocessing import PreprocessOptions
from backend.services.ocr_modes import (
    DEFAULT_OCR_MODE,
    InvalidOCRModeError,
    VALID_OCR_MODES,
    forced_preprocess_options_for_mode,
    parse_ocr_mode,
)


def test_default_mode_is_standard():
    assert DEFAULT_OCR_MODE == "standard"


def test_none_input_resolves_to_default():
    assert parse_ocr_mode(None) == "standard"


def test_empty_string_resolves_to_default():
    assert parse_ocr_mode("") == "standard"
    assert parse_ocr_mode("   ") == "standard"


@pytest.mark.parametrize("mode", ["standard", "high_accuracy"])
def test_valid_modes_are_accepted_case_insensitively(mode):
    assert parse_ocr_mode(mode) == mode
    assert parse_ocr_mode(mode.upper()) == mode
    assert parse_ocr_mode(f"  {mode}  ") == mode


@pytest.mark.parametrize("bad_mode", [
    "ultra_accurate", "gpu_turbo", "'; DROP TABLE scans;--", "standard; high_accuracy",
    "server", "mobile", "<script>alert(1)</script>",
])
def test_invalid_modes_are_rejected(bad_mode):
    with pytest.raises(InvalidOCRModeError):
        parse_ocr_mode(bad_mode)


def test_fast_mode_is_retired_and_now_rejected():
    """"fast" was removed 2026-09-17 once resize (its one distinguishing
    feature) was promoted to the unconditional baseline every mode gets -
    see ocr_modes.py's module docstring. A client still sending
    ocr_mode="fast" must get a loud, explicit rejection, never a silent
    fallback to "standard" (which would hide the behavior change)."""
    with pytest.raises(InvalidOCRModeError):
        parse_ocr_mode("fast")
    assert "fast" not in VALID_OCR_MODES


def test_standard_mode_has_no_forced_preprocess_options():
    """standard defers to the caller's own existing baseline option
    (backend/api/ocr.py's module-level _preprocess_options) rather than a
    hardcoded value here - see ocr_modes.py's module docstring."""
    assert forced_preprocess_options_for_mode("standard") is None


def test_high_accuracy_forces_resize_plus_denoise_and_contrast_enhancement():
    """high_accuracy is a strict SUPERSET of the new resize baseline
    (resize + denoise + contrast), never a regression that drops resize -
    see ocr_modes.py's module docstring on why it layers rather than
    replaces."""
    options = forced_preprocess_options_for_mode("high_accuracy")
    assert options == PreprocessOptions(resize=True, max_side=2000, denoise=True, enhance_contrast=True)
    assert options.resize is True


def test_exactly_two_modes_are_defined():
    assert VALID_OCR_MODES == {"standard", "high_accuracy"}


def test_no_mode_ever_changes_the_underlying_paddleocr_engine_construction_parameters():
    """Guards the Phase 2B safety requirement: mode selection can only ever
    choose between fixed PreprocessOptions bundles, never influence
    lang/use_doc_orientation_classify/use_doc_unwarping/
    use_textline_orientation/enable_mkldnn/text_rec_score_thresh. This is
    verified structurally: forced_preprocess_options_for_mode's return type
    is always PreprocessOptions (a dataclass with only resize/max_side/
    grayscale/enhance_contrast/denoise fields) or None - it is architecturally
    incapable of returning anything that could reach PaddleOCR's constructor."""
    from dataclasses import fields as dataclass_fields
    field_names = {f.name for f in dataclass_fields(PreprocessOptions)}
    assert field_names == {"resize", "max_side", "grayscale", "enhance_contrast", "denoise"}
    for mode in VALID_OCR_MODES:
        result = forced_preprocess_options_for_mode(mode)
        assert result is None or isinstance(result, PreprocessOptions)
