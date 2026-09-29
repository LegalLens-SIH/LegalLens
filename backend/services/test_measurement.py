"""Phase 1, Requirement 5: mm-calibrated approximate physical measurement -
focused tests for the standalone math utility. Also proves, at the
architecture level, that this utility is never imported/used by
compliance_engine.py - see test_measurement_never_imported_by_compliance_
engine below.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.services.measurement import (
    CalibrationError, CalibratedMeasurement, estimate_height_mm,
    measure_ocr_dimensions, measure_pixel_geometry,
)


def test_calibration_math_simple_1_to_1_scale():
    """A reference that is exactly 100px wide for a 100mm-wide package
    gives a 1px-per-mm scale - a 50px-tall feature is then exactly 50mm."""
    result = estimate_height_mm(feature_height_px=50, reference_width_px=100, reference_width_mm=100)
    assert result.scale_px_per_mm == 1.0
    assert result.estimated_height_mm == 50.0


def test_calibration_math_realistic_package_width():
    """A more realistic case: a package photographed at 1200px wide, known
    to be 80mm wide in reality (scale = 15 px/mm), and a 45px-tall MRP
    numeral -> 3.0mm."""
    result = estimate_height_mm(feature_height_px=45, reference_width_px=1200, reference_width_mm=80)
    assert result.scale_px_per_mm == 15.0
    assert result.estimated_height_mm == 3.0


def test_calibration_math_non_integer_scale():
    result = estimate_height_mm(feature_height_px=30, reference_width_px=333, reference_width_mm=50)
    # scale = 333/50 = 6.66 px/mm; 30 / 6.66 ~= 4.5045 mm
    assert result.scale_px_per_mm == pytest.approx(6.66, abs=1e-6)
    assert result.estimated_height_mm == pytest.approx(4.5045, abs=1e-4)


def test_result_is_always_marked_approximate():
    result = estimate_height_mm(feature_height_px=45, reference_width_px=1200, reference_width_mm=80)
    assert result.approximate is True
    assert isinstance(result, CalibratedMeasurement)
    assert len(result.assumptions) >= 1


@pytest.mark.parametrize("kwargs", [
    {"feature_height_px": 0, "reference_width_px": 100, "reference_width_mm": 50},
    {"feature_height_px": -5, "reference_width_px": 100, "reference_width_mm": 50},
    {"feature_height_px": 10, "reference_width_px": 0, "reference_width_mm": 50},
    {"feature_height_px": 10, "reference_width_px": -100, "reference_width_mm": 50},
    {"feature_height_px": 10, "reference_width_px": 100, "reference_width_mm": 0},
    {"feature_height_px": 10, "reference_width_px": 100, "reference_width_mm": -50},
])
def test_missing_or_invalid_calibration_raises_never_fabricates(kwargs):
    """Zero/negative/missing calibration inputs must raise, never silently
    produce a fabricated measurement - the core safety requirement."""
    with pytest.raises(CalibrationError):
        estimate_height_mm(**kwargs)


def test_measurement_never_imported_by_compliance_engine():
    """Architectural guarantee, checked directly against the source (not
    just by convention/comment): compliance_engine.py must not import
    anything from measurement.py. No verified legal minimum-height
    provision exists in the corpus (see measurement.py's own module
    docstring), so no compliance verdict may ever depend on this utility
    until that changes."""
    compliance_engine_path = Path(__file__).parent / "compliance_engine.py"
    tree = ast.parse(compliance_engine_path.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
    assert not any("measurement" in module for module in imported_modules), (
        f"compliance_engine.py must never import measurement.py; found: {imported_modules}"
    )


def test_pixel_geometry_requires_explicit_calibration_and_preserves_evidence():
    evidence = {"region_id": "ocr-1", "bbox": [10, 20, 50, 40]}
    uncalibrated = measure_pixel_geometry(bbox=evidence["bbox"], evidence_region=evidence)
    assert uncalibrated["status"] == "review_required"
    assert uncalibrated["dimensions"] is None
    assert uncalibrated["pixel_dimensions"] == {"width": 40.0, "height": 20.0}
    assert uncalibrated["uncertainty"]["status"] == "not_quantified"

    measured = measure_pixel_geometry(
        bbox=evidence["bbox"], calibration={"status": "calibrated", "acceptable": True, "scale_px_per_mm": 2.0},
        unit="cm", confidence=0.91, evidence_region=evidence, provenance={"source": "paddleocr"}, uncertainty_px=2,
    )
    assert measured["status"] == "experimental_measurement"
    assert measured["dimensions"] == {"width": 2.0, "height": 1.0}
    assert measured["uncertainty"]["absolute_bound"] == 0.1
    assert measured["confidence"] == 0.91
    assert measured["evidence_region"] == evidence
    assert measured["provenance"] == {"source": "paddleocr"}
    assert measured["authoritative"] is False
    assert measured["used_for_compliance"] is False


def test_malformed_geometry_and_rejected_scale_remain_review_only():
    malformed = measure_pixel_geometry(bbox=[0, 0, float("nan"), 4])
    rejected = measure_ocr_dimensions(
        {"bbox": [0, 0, 20, 10], "confidence": 0.8},
        calibration={"status": "calibrated", "acceptable": False, "scale_px_per_mm": 2},
        evidence_region_ids=["ocr-2"],
    )
    assert malformed["available"] is False
    assert malformed["dimensions"] is None
    assert rejected["status"] == "review_required"
    assert rejected["dimensions"] is None
    assert rejected["evidence_region"]["region_ids"] == ["ocr-2"]
