from __future__ import annotations

import hashlib
import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


REFERENCE_TYPES = {"STANDARD_REFERENCE", "VERIFIED_REFERENCE", "USER_ASSERTED_REFERENCE"}
REFERENCE_STATUSES = {"REGISTERED", "VERIFIED", "EXPIRED", "REJECTED"}


class PhysicalReferenceRegistration(BaseModel):
    reference_id: str = Field(min_length=1, max_length=120)
    reference_type: str
    object_description: str = Field(min_length=1, max_length=300)
    geometry_kind: Literal["rectangle", "circle", "aruco_square"]
    marker_id: int | None = Field(default=None, ge=0, le=999)
    nominal_dimension_mm: dict[str, float] = Field(default_factory=dict)
    measured_dimension_mm: dict[str, float] = Field(default_factory=dict)
    measurement_method: str | None = Field(default=None, max_length=300)
    measurement_instrument: str | None = Field(default=None, max_length=300)
    instrument_calibration_reference: str | None = Field(default=None, max_length=300)
    measurement_date: date | None = None
    uncertainty_mm: dict[str, float] = Field(default_factory=dict)
    verifier: str = Field(min_length=1, max_length=200)
    verification_document_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    source_authority: str | None = Field(default=None, max_length=300)
    source_document: str | None = Field(default=None, max_length=1000)
    source_version_date: str | None = Field(default=None, max_length=100)
    source_verified: bool = False
    valid_until: date | None = None

    @model_validator(mode="after")
    def validate_registration(self):
        if self.reference_type not in REFERENCE_TYPES:
            raise ValueError("reference_type must be STANDARD_REFERENCE, VERIFIED_REFERENCE, or USER_ASSERTED_REFERENCE")
        expected_dimensions = {
            "rectangle": {"width", "height"},
            "circle": {"diameter"},
            "aruco_square": {"side"},
        }[self.geometry_kind]
        for name, dimensions in (
            ("nominal_dimension_mm", self.nominal_dimension_mm),
            ("measured_dimension_mm", self.measured_dimension_mm),
            ("uncertainty_mm", self.uncertainty_mm),
        ):
            for axis, value in dimensions.items():
                if not axis or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} values must be finite positive dimensions")
        if self.reference_type == "STANDARD_REFERENCE":
            if not self.nominal_dimension_mm or not self.source_authority or not self.source_document or not self.source_version_date:
                raise ValueError("A standard reference requires nominal dimensions and source authority/document/version")
            if not self.source_verified:
                raise ValueError("An authorized registrar must confirm the standard dimensions against the cited source")
            if set(self.nominal_dimension_mm) != expected_dimensions:
                raise ValueError("Standard reference dimensions do not match its registered geometry kind")
            if set(self.nominal_dimension_mm) != set(self.uncertainty_mm):
                raise ValueError("Standard nominal dimensions require source-backed uncertainty for every axis")
        elif self.reference_type == "VERIFIED_REFERENCE":
            required = (
                self.measured_dimension_mm,
                self.measurement_method,
                self.measurement_instrument,
                self.instrument_calibration_reference,
                self.measurement_date,
                self.uncertainty_mm,
            )
            if not all(required):
                raise ValueError("A verified reference requires measured dimensions, method, calibrated instrument, date, and uncertainty")
            if set(self.measured_dimension_mm) != set(self.uncertainty_mm):
                raise ValueError("Measured dimensions and uncertainty must name identical axes")
        dimensions = self.measured_dimension_mm or self.nominal_dimension_mm
        if dimensions and set(dimensions) != expected_dimensions:
            raise ValueError("Physical dimensions do not match the registered geometry kind")
        if self.geometry_kind == "aruco_square" and self.marker_id is None:
            raise ValueError("An ArUco reference requires a registered marker_id")
        return self


def reference_record(registration: PhysicalReferenceRegistration, *, registered_by: str, now: date | None = None) -> dict[str, Any]:
    today = now or date.today()
    if registration.valid_until is not None and registration.valid_until < today:
        status = "EXPIRED"
    elif registration.reference_type == "VERIFIED_REFERENCE":
        status = "VERIFIED"
    else:
        status = "REGISTERED"
    dimensions = registration.measured_dimension_mm or registration.nominal_dimension_mm
    eligible = (
        status in {"REGISTERED", "VERIFIED"}
        and registration.reference_type in {"STANDARD_REFERENCE", "VERIFIED_REFERENCE"}
        and bool(dimensions)
        and (registration.reference_type != "STANDARD_REFERENCE" or registration.source_verified)
    )
    record = registration.model_dump(mode="json")
    record.update({
        "status": status,
        "calibration_eligible": eligible,
        "registered_by": registered_by,
        "registered_date": today.isoformat(),
        "source_evidence_sha256": hashlib.sha256("\n".join((
            registration.source_authority or "",
            registration.source_document or "",
            registration.source_version_date or "",
        )).encode("utf-8")).hexdigest(),
    })
    return record


def propose_reference_geometry(image: Any, reference: dict[str, Any]) -> dict[str, Any]:
    unavailable = {
        "status": "UNAVAILABLE", "geometry_reviewed": False,
        "reference_id": reference.get("reference_id"), "proposals": [],
        "used_for_compliance": False,
    }
    if reference.get("calibration_eligible") is not True:
        return {**unavailable, "reason": "Reference is not registered and eligible for geometry search"}
    if image is None or getattr(image, "size", 0) == 0 or not hasattr(image, "shape"):
        return {**unavailable, "reason": "Decoded source image is unavailable"}
    try:
        import cv2
        import numpy as np
        height, width = image.shape[:2]
        if width <= 0 or height <= 0 or width * height > 50_000_000:
            return {**unavailable, "reason": "Decoded source image dimensions are invalid or exceed the limit"}
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        return {**unavailable, "reason": f"Reference geometry detector unavailable ({type(exc).__name__})"}

    proposals = []
    geometry_kind = reference.get("geometry_kind")
    if geometry_kind == "rectangle":
        dimensions = reference.get("measured_dimension_mm") or reference.get("nominal_dimension_mm") or {}
        try:
            expected_ratio = max(float(dimensions["width"]), float(dimensions["height"])) / min(float(dimensions["width"]), float(dimensions["height"]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return {**unavailable, "reason": "Registered rectangle dimensions are unavailable"}
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 45, 140)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        image_area = width * height
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:100]:
            area = cv2.contourArea(contour)
            if area < image_area * 0.002 or area > image_area * 0.8:
                continue
            perimeter = cv2.arcLength(contour, True)
            polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                continue
            points = polygon.reshape(4, 2).astype(float).tolist()
            edge_lengths = [
                math.hypot(points[(index + 1) % 4][0] - points[index][0], points[(index + 1) % 4][1] - points[index][1])
                for index in range(4)
            ]
            first_axis = (edge_lengths[0] + edge_lengths[2]) / 2
            second_axis = (edge_lengths[1] + edge_lengths[3]) / 2
            ratio = max(first_axis, second_axis) / min(first_axis, second_axis)
            if abs(ratio - expected_ratio) / expected_ratio > 0.15:
                continue
            proposals.append({"geometry": {"polygon": points}, "area_px": float(area), "aspect_ratio": round(ratio, 5)})
            if len(proposals) >= 20:
                break
    elif geometry_kind == "circle":
        circles = cv2.HoughCircles(
            cv2.GaussianBlur(gray, (5, 5), 0), cv2.HOUGH_GRADIENT,
            dp=1.2, minDist=max(20, min(width, height) // 8),
            param1=100, param2=35,
            minRadius=max(8, min(width, height) // 100),
            maxRadius=max(12, min(width, height) // 3),
        )
        if circles is not None:
            for center_x, center_y, radius in np.round(circles[0]).astype(int)[:20]:
                if center_x - radius < 0 or center_y - radius < 0 or center_x + radius >= width or center_y + radius >= height:
                    continue
                proposals.append({"geometry": {"circle": {"center": [int(center_x), int(center_y)], "radius_px": int(radius)}}, "radius_px": int(radius)})
    elif geometry_kind == "aruco_square":
        aruco = getattr(cv2, "aruco", None)
        if aruco is None:
            return {**unavailable, "reason": "This OpenCV build does not include the ArUco detector"}
        marker_id = reference.get("marker_id")
        if not isinstance(marker_id, int) or isinstance(marker_id, bool) or not 0 <= marker_id <= 999:
            return {**unavailable, "reason": "ArUco marker ID is not registered"}
        dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_1000)
        detector = aruco.ArucoDetector(dictionary, aruco.DetectorParameters())
        corners, ids, _rejected = detector.detectMarkers(gray)
        for marker_corners, detected_id in zip(corners, ids.flatten() if ids is not None else []):
            if int(detected_id) == marker_id:
                proposals.append({"geometry": {"polygon": marker_corners.reshape(4, 2).astype(float).tolist(), "marker_id": int(detected_id)}})
    else:
        return {**unavailable, "reason": "Registered reference geometry kind is unsupported"}

    return {
        "status": "PROPOSALS_REVIEW_REQUIRED" if proposals else "NO_CANDIDATE_REVIEW_REQUIRED",
        "geometry_reviewed": False,
        "reference_id": reference.get("reference_id"),
        "reference_type": reference.get("reference_type"),
        "geometry_kind": geometry_kind,
        "proposals": proposals,
        "used_for_compliance": False,
        "reason": None if proposals else "No complete, geometry-compatible reference candidate was detected",
    }


def calibrate_registered_dimensions(
    reference: dict[str, Any],
    *,
    observed_dimensions_px: dict[str, float],
    pixel_uncertainty: dict[str, float],
    source_image_sha256: str,
    geometry_source: str,
    geometry_reviewed: bool,
    partial_or_distorted: bool = False,
) -> dict[str, Any]:
    def review(reason: str) -> dict[str, Any]:
        return {
            "status": "REVIEW_REQUIRED", "available": False, "scale_px_per_mm": None,
            "used_for_compliance": False, "authoritative": False, "reference_id": reference.get("reference_id"),
            "reference_status": reference.get("status"), "source_image_sha256": source_image_sha256,
            "reason": reason,
        }

    if reference.get("status") not in REFERENCE_STATUSES or reference.get("calibration_eligible") is not True:
        return review("Reference is user-asserted, unverified, expired, rejected, or otherwise ineligible")
    if not isinstance(source_image_sha256, str) or len(source_image_sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in source_image_sha256):
        return review("Source image SHA-256 is invalid")
    if not geometry_source or not geometry_reviewed:
        return review("Reference geometry must be source-linked and explicitly reviewed")
    if partial_or_distorted:
        return review("Reference is partially occluded or materially distorted")

    physical = reference.get("measured_dimension_mm") or reference.get("nominal_dimension_mm") or {}
    uncertainties = reference.get("uncertainty_mm") or {}
    if not isinstance(physical, dict) or not physical:
        return review("Reference dimensions are unavailable")
    scales: dict[str, float] = {}
    scale_uncertainty: dict[str, float] = {}
    for axis, millimeters in physical.items():
        pixels = observed_dimensions_px.get(axis)
        pixel_error = pixel_uncertainty.get(axis)
        physical_error = uncertainties.get(axis)
        if any(isinstance(value, bool) for value in (pixels, pixel_error, physical_error)):
            return review(f"Measurement uncertainty for {axis} is unavailable")
        try:
            pixels, pixel_error, physical_error, millimeters = map(float, (pixels, pixel_error, physical_error, millimeters))
        except (TypeError, ValueError):
            return review(f"Observed dimension or uncertainty for {axis} is unavailable")
        if not all(math.isfinite(value) for value in (pixels, pixel_error, physical_error, millimeters)) or min(pixels, millimeters) <= 0 or min(pixel_error, physical_error) < 0:
            return review(f"Observed dimension or uncertainty for {axis} is invalid")
        scale = pixels / millimeters
        relative_variance = (pixel_error / pixels) ** 2 + (physical_error / millimeters) ** 2
        scales[axis] = scale
        scale_uncertainty[axis] = scale * math.sqrt(relative_variance)
    mean_scale = sum(scales.values()) / len(scales)
    if len(scales) > 1:
        axis_names = list(scales)
        for index, first_axis in enumerate(axis_names):
            for second_axis in axis_names[index + 1:]:
                allowed_difference = scale_uncertainty[first_axis] + scale_uncertainty[second_axis]
                if abs(scales[first_axis] - scales[second_axis]) > allowed_difference:
                    return review("Independent reference axes disagree beyond their propagated uncertainty")
    mean_uncertainty = math.sqrt(sum(value ** 2 for value in scale_uncertainty.values())) / len(scale_uncertainty)
    return {
        "status": "CALIBRATED_REVIEW_ONLY",
        "available": True,
        "reference_id": reference["reference_id"],
        "reference_type": reference["reference_type"],
        "reference_status": reference["status"],
        "scale_px_per_mm": round(mean_scale, 8),
        "scale_uncertainty_px_per_mm": round(mean_uncertainty, 8),
        "axis_measurements": {
            axis: {"pixels": observed_dimensions_px[axis], "millimeters": physical[axis], "scale_px_per_mm": scales[axis], "scale_uncertainty_px_per_mm": scale_uncertainty[axis]}
            for axis in scales
        },
        "geometry_source": geometry_source,
        "geometry_reviewed": True,
        "source_image_sha256": source_image_sha256.lower(),
        "reference_provenance": {
            "source_authority": reference.get("source_authority"),
            "source_document": reference.get("source_document"),
            "source_version_date": reference.get("source_version_date"),
            "measurement_method": reference.get("measurement_method"),
            "measurement_instrument": reference.get("measurement_instrument"),
            "instrument_calibration_reference": reference.get("instrument_calibration_reference"),
            "measurement_date": reference.get("measurement_date"),
            "verifier": reference.get("verifier"),
            "verification_document_sha256": reference.get("verification_document_sha256"),
        },
        "authoritative": False,
        "used_for_compliance": False,
        "reasons": ["Supplemental calibration evidence only; physical measurements and legal determinations require officer review."],
    }


def compare_reference_calibrations(calibrations: list[dict[str, Any]], *, agreement_sigma: float = 3.0) -> dict[str, Any]:
    if not isinstance(calibrations, list) or len(calibrations) < 2:
        return {"status": "REVIEW_REQUIRED", "consistent": False, "reason": "At least two independent calibrations are required for comparison."}
    if not math.isfinite(agreement_sigma) or agreement_sigma <= 0:
        return {"status": "REVIEW_REQUIRED", "consistent": False, "reason": "Agreement uncertainty multiplier is invalid."}
    source_hashes = {item.get("source_image_sha256") for item in calibrations if isinstance(item, dict)}
    if len(source_hashes) != 1 or None in source_hashes:
        return {"status": "REVIEW_REQUIRED", "consistent": False, "reason": "References must be compared in the same source image coordinate frame."}
    estimates = []
    for item in calibrations:
        if item.get("status") != "CALIBRATED_REVIEW_ONLY" or item.get("available") is not True:
            return {"status": "REVIEW_REQUIRED", "consistent": False, "reason": "All references must have eligible, uncertainty-bearing calibration estimates."}
        try:
            value = float(item["scale_px_per_mm"])
            uncertainty = float(item["scale_uncertainty_px_per_mm"])
        except (KeyError, TypeError, ValueError):
            return {"status": "REVIEW_REQUIRED", "consistent": False, "reason": "A reference calibration is missing scale or uncertainty."}
        if not math.isfinite(value) or not math.isfinite(uncertainty) or value <= 0 or uncertainty < 0:
            return {"status": "REVIEW_REQUIRED", "consistent": False, "reason": "A reference calibration has invalid scale or uncertainty."}
        estimates.append((item, value, uncertainty))
    disagreements = []
    for index, (first, first_value, first_uncertainty) in enumerate(estimates):
        for second, second_value, second_uncertainty in estimates[index + 1:]:
            allowed_difference = agreement_sigma * math.hypot(first_uncertainty, second_uncertainty)
            difference = abs(first_value - second_value)
            if difference > allowed_difference:
                disagreements.append({
                    "reference_ids": [first.get("reference_id"), second.get("reference_id")],
                    "difference_px_per_mm": difference,
                    "allowed_difference_px_per_mm": allowed_difference,
                })
    return {
        "status": "REVIEW_REQUIRED" if disagreements else "REFERENCES_AGREE_REVIEW_ONLY",
        "consistent": not disagreements,
        "source_image_sha256": next(iter(source_hashes)),
        "agreement_sigma": agreement_sigma,
        "reference_estimates": [
            {"reference_id": item.get("reference_id"), "reference_type": item.get("reference_type"), "scale_px_per_mm": value, "uncertainty_px_per_mm": uncertainty}
            for item, value, uncertainty in estimates
        ],
        "disagreements": disagreements,
        "used_for_compliance": False,
        "authoritative": False,
    }
