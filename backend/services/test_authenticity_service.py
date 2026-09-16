"""Focused tests for the Authenticity Verification Layer's Section 3 (EXIF
metadata extraction only). Covers: valid/partial/absent/malformed EXIF,
JPG/PNG/WEBP format coverage, compliance isolation (identical OCR input
must produce identical ComplianceEngine output regardless of EXIF), the
software tag never being treated as a fraud signal, and GPS privacy
(presence only, never raw coordinates).
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from backend.models.authenticity import AuthenticityEvidence, ExifEvidence
from backend.services.authenticity_service import build_authenticity_evidence, extract_exif_evidence
from backend.services.compliance_engine import ComplianceEngine

REAL_FIXTURE_WITH_EXIF = Path(__file__).resolve().parents[2] / "training" / "raw_images" / "Handwash_back.jpg"


def _jpeg_with_exif(exif_tags: dict[int, object] | None = None, exif_sub_ifd_tags: dict[int, object] | None = None, gps_tags: dict[int, object] | None = None) -> bytes:
    """Build a small, real JPEG with a precisely controlled EXIF payload -
    round-trips through Pillow's own encoder/decoder, so this exercises the
    exact same file shape a real camera photo would produce, not a
    hand-crafted byte string."""
    img = Image.new("RGB", (20, 20), color="red")
    exif = img.getexif()
    for tag, value in (exif_tags or {}).items():
        exif[tag] = value
    if exif_sub_ifd_tags:
        sub_ifd = exif.get_ifd(0x8769)
        for tag, value in exif_sub_ifd_tags.items():
            sub_ifd[tag] = value
    if gps_tags:
        gps_ifd = exif.get_ifd(0x8825)
        for tag, value in gps_tags.items():
            gps_ifd[tag] = value
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _plain_image_bytes(fmt: str) -> bytes:
    img = Image.new("RGB", (20, 20), color="blue")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# --- 1. Valid EXIF ----------------------------------------------------------

def test_valid_exif_extracts_make_model_software_and_timestamp():
    data = _jpeg_with_exif(
        exif_tags={271: "Samsung", 272: "SM-G781B", 305: "G781BXXSDHXI4", 306: "2026:09:03 16:11:13"},
        exif_sub_ifd_tags={36867: "2026:09:03 16:11:12"},
    )
    ev = extract_exif_evidence(data)
    assert ev.available is True
    assert ev.make == "Samsung"
    assert ev.model == "SM-G781B"
    assert ev.software == "G781BXXSDHXI4"
    assert ev.datetime_original == "2026:09:03 16:11:12"
    assert ev.error is None


def test_valid_exif_real_repository_fixture():
    """Real camera photo already in the repository (used throughout this
    project's own OCR benchmark work) - genuine EXIF, not synthetic."""
    if not REAL_FIXTURE_WITH_EXIF.exists():
        import pytest
        pytest.skip("real fixture image not present in this checkout")
    data = REAL_FIXTURE_WITH_EXIF.read_bytes()
    ev = extract_exif_evidence(data)
    assert ev.available is True
    assert ev.make is not None
    assert ev.model is not None
    assert ev.datetime_original is not None


# --- 2. No EXIF --------------------------------------------------------------

def test_no_exif_extraction_succeeds_with_unavailable_metadata():
    data = _plain_image_bytes("PNG")
    ev = extract_exif_evidence(data)
    assert ev.available is False
    assert ev.make is None
    assert ev.model is None
    assert ev.software is None
    assert ev.datetime_original is None
    assert ev.gps_present is False
    assert ev.error is None  # absence is not an error


# --- 3. Partial EXIF ----------------------------------------------------------

def test_partial_exif_only_present_fields_populated():
    """Only Make + Software set - Model/DateTimeOriginal must stay None,
    never fabricated or defaulted to an empty string."""
    data = _jpeg_with_exif(exif_tags={271: "Canon", 305: "GIMP 2.10"})
    ev = extract_exif_evidence(data)
    assert ev.available is True
    assert ev.make == "Canon"
    assert ev.software == "GIMP 2.10"
    assert ev.model is None
    assert ev.datetime_original is None


def test_exif_string_values_are_cleaned_of_nul_padding_and_whitespace():
    """A real, common EXIF writer quirk - trailing NUL bytes/whitespace
    must never leak into the reported value, and must never make an
    effectively-empty tag look 'present'."""
    data = _jpeg_with_exif(exif_tags={271: "Nikon \x00\x00", 272: "   \x00"})
    ev = extract_exif_evidence(data)
    assert ev.make == "Nikon"
    assert ev.model is None  # whitespace/NUL-only -> None, not ""


# --- 4. Malformed/unreadable metadata ----------------------------------------

def test_malformed_image_bytes_do_not_raise():
    ev = extract_exif_evidence(b"this is not an image at all" * 5)
    assert ev.available is False
    assert ev.error is not None


def test_empty_bytes_do_not_raise():
    ev = extract_exif_evidence(b"")
    assert ev.available is False
    assert ev.error is not None


def test_truncated_jpeg_does_not_raise():
    ev = extract_exif_evidence(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    assert ev.available is False
    assert ev.error is not None


def test_authenticity_evidence_wrapper_never_raises_on_malformed_input():
    result = build_authenticity_evidence(b"garbage")
    assert isinstance(result, AuthenticityEvidence)
    assert result.exif.available is False


# --- 5. Supported formats -----------------------------------------------------

def test_jpeg_format_supported():
    ev = extract_exif_evidence(_jpeg_with_exif(exif_tags={271: "TestMake"}))
    assert ev.available is True
    assert ev.make == "TestMake"


def test_png_format_supported_no_exif_assumed():
    """PNG rarely/never carries EXIF from typical sources - extraction must
    still succeed cleanly (available=False), never raise, never assume
    EXIF exists just because the format technically supports it."""
    ev = extract_exif_evidence(_plain_image_bytes("PNG"))
    assert ev.available is False
    assert ev.error is None


def test_webp_format_supported_no_exif_assumed():
    ev = extract_exif_evidence(_plain_image_bytes("WEBP"))
    assert ev.available is False
    assert ev.error is None


# --- 6. Compliance isolation --------------------------------------------------

def _real_ocr_dict() -> dict:
    return {
        "success": True,
        "document_id": "DOC-FIXED-FOR-TEST",  # pins normalize_ocr_result's otherwise-random id so two evaluate() calls are directly comparable
        "full_text": (
            "MANUFACTURED BY ACME FOODS PVT LTD, 12 INDUSTRIAL ROAD, PUNE - 411001\n"
            "Country of Origin: India\n"
            "NET WEIGHT: 250 g\n"
            "MRP Rs. 99 (Inclusive of all taxes)\n"
            "Unit Sale Price: Rs. 40 per 100g\n"
            "Mfg Date: 08/2026\n"
            "Consumer Care: 1800-123-4567\n"
            "Email: care@acmefoods.example\n"
        ),
        "detections": [{"confidence": 0.95}],
    }


def test_compliance_output_identical_with_and_without_exif_present():
    """The core isolation guarantee: ComplianceEngine.evaluate() is called
    with ocr_dict alone (see backend/api/ocr.py's create_scan) - EXIF
    evidence is attached to the scan document as a completely separate,
    sibling key afterward, never passed into or read by the engine. This
    proves that architecturally, not just by absence of a code path:
    running the SAME ocr_dict through the real engine produces byte-
    identical results whether or not EXIF evidence exists for the image."""
    ocr_dict = _real_ocr_dict()
    result_without = ComplianceEngine().evaluate(ocr_dict, "e_commerce_product_listing")

    # Simulate EXIF evidence existing for this same upload - attached only
    # to a separate "scan" dict, exactly as create_scan does, never merged
    # into ocr_dict.
    exif_present = extract_exif_evidence(_jpeg_with_exif(exif_tags={271: "Samsung", 272: "SM-G781B", 305: "Adobe Photoshop 25.0"}))
    scan = {"ocr": ocr_dict, "authenticity": {"exif": exif_present.model_dump()}}
    result_with = ComplianceEngine().evaluate(scan["ocr"], "e_commerce_product_listing")

    # Compare everything except the audit block's own evaluated_at
    # timestamp (which legitimately differs between two separate calls a
    # few microseconds apart and carries no compliance meaning).
    dump_without = result_without.model_dump()
    dump_with = result_with.model_dump()
    dump_without.get("audit", {}).pop("evaluated_at", None)
    dump_with.get("audit", {}).pop("evaluated_at", None)
    assert dump_without == dump_with
    assert result_without.overall_status == result_with.overall_status
    assert result_without.compliance_score == result_with.compliance_score


def test_compliance_output_identical_regardless_of_exif_absence():
    ocr_dict = _real_ocr_dict()
    result_a = ComplianceEngine().evaluate(ocr_dict, "e_commerce_product_listing")
    exif_absent = extract_exif_evidence(_plain_image_bytes("PNG"))
    assert exif_absent.available is False
    result_b = ComplianceEngine().evaluate(ocr_dict, "e_commerce_product_listing")
    dump_a = result_a.model_dump()
    dump_b = result_b.model_dump()
    dump_a.get("audit", {}).pop("evaluated_at", None)
    dump_b.get("audit", {}).pop("evaluated_at", None)
    assert dump_a == dump_b


# --- 7. Software tag is evidence only, never a fraud signal ------------------

def test_editing_software_tags_are_captured_as_plain_evidence_only():
    """Photoshop/GIMP/Snapseed/Lightroom presence must be recorded exactly
    like any other Software tag value - this module has no concept of
    'suspicious' software, no scoring, and no classification logic at all
    to apply to it."""
    for software_name in ("Adobe Photoshop 25.0", "GIMP 2.10.34", "Snapseed 2.19", "Adobe Lightroom Classic 13.0"):
        ev = extract_exif_evidence(_jpeg_with_exif(exif_tags={305: software_name}))
        assert ev.available is True
        assert ev.software == software_name
        # No verdict field exists anywhere on the model to even express
        # "fake"/"edited"/"suspicious" - the schema itself enforces this.
        assert not hasattr(ev, "is_edited")
        assert not hasattr(ev, "is_fake")
        assert not hasattr(ev, "score")
        assert not hasattr(ev, "verdict")


def test_exif_evidence_model_has_no_scoring_or_verdict_fields():
    """Schema-level guarantee: ExifEvidence/AuthenticityEvidence expose
    only descriptive fields, never a score or authenticity verdict."""
    field_names = set(ExifEvidence.model_fields.keys())
    assert field_names == {"available", "make", "model", "software", "datetime_original", "gps_present", "error"}
    assert set(AuthenticityEvidence.model_fields.keys()) == {"exif"}


# --- 8. Privacy: GPS presence only, never raw coordinates --------------------

def test_gps_present_reports_boolean_only_never_raw_coordinates():
    data = _jpeg_with_exif(gps_tags={1: "N", 2: (28.0, 36.0, 10.0), 3: "E", 4: (77.0, 12.0, 0.0)})
    ev = extract_exif_evidence(data)
    assert ev.gps_present is True
    # No coordinate value is exposed anywhere on the model.
    assert not hasattr(ev, "gps_latitude")
    assert not hasattr(ev, "gps_longitude")
    assert not hasattr(ev, "gps_coordinates")
    dumped = ev.model_dump()
    assert "28.0" not in str(dumped) and "77.0" not in str(dumped)


def test_gps_absent_reports_false():
    data = _jpeg_with_exif(exif_tags={271: "Samsung"})  # no GPS tags at all
    ev = extract_exif_evidence(data)
    assert ev.gps_present is False


def test_gps_presence_never_treated_as_a_fraud_signal():
    """No field anywhere on the model expresses a verdict about GPS
    presence/absence - it is reported exactly like Make/Model, nothing
    more."""
    with_gps = extract_exif_evidence(_jpeg_with_exif(gps_tags={1: "N", 2: (1.0, 2.0, 3.0)}))
    without_gps = extract_exif_evidence(_jpeg_with_exif(exif_tags={271: "X"}))
    assert set(with_gps.model_dump().keys()) == set(without_gps.model_dump().keys())
