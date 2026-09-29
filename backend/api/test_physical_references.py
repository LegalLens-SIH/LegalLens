from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.physical_references import _geometry_measurements, _authorize_registry_key
from backend.services.physical_reference_registry import PhysicalReferenceRegistration, reference_record


def test_registered_id1_geometry_maps_to_card_width_and_height():
    record = reference_record(PhysicalReferenceRegistration(
        reference_id="ID1-001",
        reference_type="STANDARD_REFERENCE",
        object_description="Registered ID-1 card",
        geometry_kind="rectangle",
        nominal_dimension_mm={"width": 85.6, "height": 53.98},
        uncertainty_mm={"width": 0.1, "height": 0.1},
        verifier="Authorized registrar",
        source_authority="ISO",
        source_document="https://www.iso.org/standard/70483.html",
        source_version_date="ISO/IEC 7810:2019",
        source_verified=True,
    ), registered_by="registry-admin")

    dimensions = _geometry_measurements(record, {"polygon": [[0, 0], [856, 0], [856, 540], [0, 540]]}, image_dimensions=(1000, 700))

    assert dimensions == {"width": 856.0, "height": 540.0}


def test_registered_coin_geometry_requires_circle():
    record = {"geometry_kind": "circle"}
    assert _geometry_measurements(record, {"circle": {"center": [100, 100], "radius_px": 50}}, image_dimensions=(200, 200)) == {"diameter": 100.0}
    with pytest.raises(HTTPException, match="Circular reference"):
        _geometry_measurements(record, {"polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}, image_dimensions=(200, 200))


def test_aruco_marker_pixel_square_is_only_geometry_and_not_a_physical_standard():
    record = {"geometry_kind": "aruco_square", "calibration_eligible": False, "reference_type": "USER_ASSERTED_REFERENCE"}
    geometry = _geometry_measurements(record, {"polygon": [[0, 0], [120, 0], [120, 120], [0, 120]]}, image_dimensions=(200, 200))
    assert geometry == {"side": 120.0}
    assert record["calibration_eligible"] is False


def test_rectangular_geometry_rejects_self_intersection_and_out_of_frame():
    record = {"geometry_kind": "rectangle"}
    with pytest.raises(HTTPException, match="self-intersecting"):
        _geometry_measurements(record, {"polygon": [[0, 0], [100, 100], [0, 100], [100, 0]]}, image_dimensions=(120, 120))
    with pytest.raises(HTTPException, match="outside"):
        _geometry_measurements(record, {"polygon": [[0, 0], [150, 0], [150, 100], [0, 100]]}, image_dimensions=(120, 120))


def test_registry_requires_server_provision_key(monkeypatch):
    monkeypatch.setenv("OFFICIAL_PROVISION_KEY", "configured-key")
    with pytest.raises(HTTPException) as error:
        _authorize_registry_key("wrong-key")
    assert error.value.status_code == 403
    assert _authorize_registry_key("configured-key") == "configured-key"
