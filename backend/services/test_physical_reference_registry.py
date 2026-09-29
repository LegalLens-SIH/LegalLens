from __future__ import annotations

from datetime import date

import cv2
import numpy as np
import pytest

from backend.services.physical_reference_registry import (
    PhysicalReferenceRegistration,
    calibrate_registered_dimensions,
    compare_reference_calibrations,
    propose_reference_geometry,
    reference_record,
)


def test_standard_reference_registration_requires_source_verification_and_uncertainty():
    base = {
        "reference_id": "ID1-REG-001",
        "reference_type": "STANDARD_REFERENCE",
        "object_description": "ID-1 identification card",
        "geometry_kind": "rectangle",
        "nominal_dimension_mm": {"width": 85.6, "height": 53.98},
        "uncertainty_mm": {"width": 0.1, "height": 0.1},
        "verifier": "Authorized registrar",
        "source_authority": "ISO",
        "source_document": "https://www.iso.org/standard/70483.html",
        "source_version_date": "ISO/IEC 7810:2019",
        "source_verified": False,
    }
    try:
        PhysicalReferenceRegistration(**base)
    except ValueError as exc:
        assert "confirm" in str(exc)
    else:
        raise AssertionError("unverified source must not register a standard reference")

    base["source_verified"] = True
    registration = PhysicalReferenceRegistration(**base)
    record = reference_record(registration, registered_by="registry-admin")

    assert record["status"] == "REGISTERED"
    assert record["calibration_eligible"] is True
    assert record["registered_by"] == "registry-admin"
    assert len(record["source_evidence_sha256"]) == 64


def test_verified_reference_requires_instrument_method_and_uncertainty():
    registration = PhysicalReferenceRegistration(
        reference_id="CALIPER-ITEM-1",
        reference_type="VERIFIED_REFERENCE",
        object_description="Measured card width",
        geometry_kind="rectangle",
        measured_dimension_mm={"width": 85.59, "height": 53.97},
        uncertainty_mm={"width": 0.02, "height": 0.02},
        measurement_method="Caliper measurement",
        measurement_instrument="Caliper SN-123",
        instrument_calibration_reference="Certificate hash recorded",
        measurement_date=date(2026, 9, 1),
        verifier="Authorized metrologist",
    )
    record = reference_record(registration, registered_by="registry-admin")
    assert record["status"] == "VERIFIED"
    assert record["calibration_eligible"] is True


def test_user_asserted_reference_never_calibrates():
    registration = PhysicalReferenceRegistration(
        reference_id="USER-OBJECT-1",
        reference_type="USER_ASSERTED_REFERENCE",
        object_description="User supplied object",
        geometry_kind="circle",
        measured_dimension_mm={"diameter": 100.0},
        verifier="Submitting user",
    )
    record = reference_record(registration, registered_by="registry-admin")
    result = calibrate_registered_dimensions(
        record,
        observed_dimensions_px={"diameter": 200},
        pixel_uncertainty={"diameter": 1},
        source_image_sha256="a" * 64,
        geometry_source="reviewed_source_image_region",
        geometry_reviewed=True,
    )
    assert record["status"] == "REGISTERED"
    assert record["calibration_eligible"] is False
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["scale_px_per_mm"] is None


def test_calibration_propagates_reference_and_pixel_uncertainty_review_only():
    registration = PhysicalReferenceRegistration(
        reference_id="MEASURED-REF-1",
        reference_type="VERIFIED_REFERENCE",
        object_description="Measured rectangular reference",
        geometry_kind="rectangle",
        measured_dimension_mm={"width": 100.0, "height": 50.0},
        uncertainty_mm={"width": 0.1, "height": 0.1},
        measurement_method="Calibrated caliper",
        measurement_instrument="Instrument X",
        instrument_calibration_reference="CAL-2026-1",
        measurement_date=date(2026, 9, 1),
        verifier="Authorized metrologist",
    )
    record = reference_record(registration, registered_by="registry-admin")
    result = calibrate_registered_dimensions(
        record,
        observed_dimensions_px={"width": 2000, "height": 1000},
        pixel_uncertainty={"width": 2, "height": 2},
        source_image_sha256="b" * 64,
        geometry_source="reviewed_source_image_region",
        geometry_reviewed=True,
    )

    assert result["status"] == "CALIBRATED_REVIEW_ONLY"
    assert result["scale_px_per_mm"] == 20.0
    assert result["scale_uncertainty_px_per_mm"] > 0
    assert result["used_for_compliance"] is False
    assert result["reference_id"] == "MEASURED-REF-1"


def test_expired_reference_and_axis_disagreement_fail_closed():
    registration = PhysicalReferenceRegistration(
        reference_id="EXPIRED-REF-1",
        reference_type="VERIFIED_REFERENCE",
        object_description="Expired test standard",
        geometry_kind="rectangle",
        measured_dimension_mm={"width": 100.0, "height": 50.0},
        uncertainty_mm={"width": 0.1, "height": 0.1},
        measurement_method="Calibrated caliper",
        measurement_instrument="Instrument X",
        instrument_calibration_reference="CAL-2026-1",
        measurement_date=date(2025, 1, 1),
        verifier="Authorized metrologist",
        valid_until=date(2026, 1, 1),
    )
    expired = reference_record(registration, registered_by="registry-admin", now=date(2026, 9, 28))
    assert expired["status"] == "EXPIRED"
    assert expired["calibration_eligible"] is False

    registration = registration.model_copy(update={"valid_until": None})
    active = reference_record(registration, registered_by="registry-admin", now=date(2026, 9, 28))
    disagreement = calibrate_registered_dimensions(
        active,
        observed_dimensions_px={"width": 2000, "height": 900},
        pixel_uncertainty={"width": 1, "height": 1},
        source_image_sha256="c" * 64,
        geometry_source="reviewed_source_image_region",
        geometry_reviewed=True,
    )
    assert disagreement["status"] == "REVIEW_REQUIRED"
    assert disagreement["scale_px_per_mm"] is None


def test_rectangle_detection_returns_unreviewed_source_geometry_proposals():
    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (200, 150), (600, 400), (0, 0, 0), 6)
    reference = {
        "reference_id": "ID1-PROPOSAL-1",
        "reference_type": "STANDARD_REFERENCE",
        "geometry_kind": "rectangle",
        "nominal_dimension_mm": {"width": 85.6, "height": 53.98},
        "status": "REGISTERED",
        "calibration_eligible": True,
    }

    result = propose_reference_geometry(image, reference)

    assert result["status"] == "PROPOSALS_REVIEW_REQUIRED"
    assert result["proposals"]
    assert result["geometry_reviewed"] is False
    assert result["used_for_compliance"] is False


def test_multiple_reference_estimates_are_compared_without_silent_combination():
    first = {"status": "CALIBRATED_REVIEW_ONLY", "available": True, "reference_id": "REF-A", "reference_type": "STANDARD_REFERENCE", "scale_px_per_mm": 20.0, "scale_uncertainty_px_per_mm": 0.1, "source_image_sha256": "d" * 64}
    second = {**first, "reference_id": "REF-B", "scale_px_per_mm": 20.2}
    agrees = compare_reference_calibrations([first, second])
    assert agrees["status"] == "REFERENCES_AGREE_REVIEW_ONLY"
    assert agrees["consistent"] is True
    assert len(agrees["reference_estimates"]) == 2
    assert "scale_px_per_mm" not in agrees

    conflicting = {**second, "scale_px_per_mm": 24.0}
    rejected = compare_reference_calibrations([first, conflicting])
    assert rejected["status"] == "REVIEW_REQUIRED"
    assert rejected["disagreements"]
