from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from backend.services.gs1_verification import GS1VerificationService, crop_barcode_image, normalize_gtin, validate_gtin


@pytest.mark.parametrize("gtin", ["96385074", "036000291452", "4006381333931", "10012345678902"])
def test_gtin_8_12_13_14_validate_offline(gtin):
    result = validate_gtin(gtin)
    assert result["valid"] is True
    assert result["length"] == len(gtin)
    assert result["check_digit"] == result["expected_check_digit"]


@pytest.mark.parametrize("gtin", ["96385075", "036000291453", "4006381333930", "10012345678903"])
def test_invalid_gtin_check_digits_for_each_length(gtin):
    result = validate_gtin(gtin)
    assert result["status"] == "INVALID_GTIN"
    assert result["expected_check_digit"] != gtin[-1]


def test_normalization_preserves_raw_and_rejects_malformed_identifiers():
    result = normalize_gtin("8901 2345-6789 01")
    assert result["raw_value"] == "8901 2345-6789 01"
    assert result["gtin"] == "89012345678901"
    assert result["status"] == "INVALID_GTIN"
    valid = normalize_gtin("1001 2345-6789 02")
    assert valid["gtin"] == "10012345678902"
    assert valid["valid"] is True
    assert normalize_gtin("8901234567890X")["status"] == "MALFORMED_IDENTIFIER"
    assert normalize_gtin("４００６３８１３３３９３１")["status"] == "MALFORMED_IDENTIFIER"
    assert normalize_gtin("")["status"] == "INVALID_GTIN"
    assert normalize_gtin("123456789012", "CODE_128")["status"] == "UNSUPPORTED_IDENTIFIER"


def test_symbology_length_and_upc_e_expansion():
    assert normalize_gtin("96385074", "EAN_8")["valid"] is True
    assert normalize_gtin("036000291452", "UPC-A")["valid"] is True
    assert normalize_gtin("4006381333931", "EAN-13")["valid"] is True
    assert normalize_gtin("10012345678902", "ITF-14")["valid"] is True
    assert normalize_gtin("04252614", "UPC-E")["gtin"] == "042100005264"
    assert normalize_gtin("123456789012", "EAN_13")["status"] == "INVALID_GTIN"


def test_local_only_status_is_explicit_and_report_scoped():
    result = GS1VerificationService().verify({"detections": [{
        "raw_value": "4006381333931", "symbology": "EAN_13", "bbox": [1, 2, 30, 20],
    }]}, scan_id="SCN-A", image_id="IMG-A", report_id="RPT-A")
    barcode = result["barcodes"][0]
    assert result["status"] == "LOCAL_VALIDATION_ONLY"
    assert result["provider_state"]["status"] == "NOT_CONFIGURED"
    assert result["checked_at"]
    assert "verified_at" not in result
    assert barcode["external_verification"]["verified"] is False
    assert barcode["source_image_id"] == "IMG-A"
    assert barcode["evidence"]["report_id"] == "RPT-A"
    assert result["authoritative"] is False and result["used_for_compliance"] is False


def test_product_and_image_verifications_do_not_share_barcode_state():
    service = GS1VerificationService()
    contexts = [
        ("SCN-PRODUCT-A", "IMG-A1", "RPT-A1", "4006381333931"),
        ("SCN-PRODUCT-A", "IMG-A2", "RPT-A2", "036000291452"),
        ("SCN-PRODUCT-B", "IMG-B1", "RPT-B1", "96385074"),
    ]
    results = [service.verify({"detections": [{
        "raw_value": gtin, "symbology": "EAN_13" if len(gtin) == 13 else "UPC_A" if len(gtin) == 12 else "EAN_8",
    }]}, scan_id=scan_id, image_id=image_id, report_id=report_id)
        for scan_id, image_id, report_id, gtin in contexts]
    assert [(item["scan_id"], item["image_id"], item["report_id"]) for item in results] == [
        context[:3] for context in contexts
    ]
    assert [item["barcodes"][0]["gtin"] for item in results] == [context[3] for context in contexts]
    assert len({item["barcodes"][0]["evidence"]["evidence_id"] for item in results}) == 3


def test_invalid_gtin_skips_provider_and_mismatch_is_review_only():
    class Provider:
        provider_name = "test-only"

        def __init__(self):
            self.calls = []
            self.availability_calls = 0

        def availability(self):
            self.availability_calls += 1
            return {"available": True, "status": "READY"}

        def verify_gtin(self, gtin):
            self.calls.append(gtin)
            return {"verified": True, "product_identity": {"brand": "Different"}}

    provider = Provider()
    service = GS1VerificationService(provider)
    invalid = service.verify({"detections": [{"raw_value": "4006381333930", "symbology": "EAN_13"}]})
    assert provider.calls == []
    assert provider.availability_calls == 0
    assert invalid["barcodes"][0]["validation_state"] == "INVALID_GTIN"

    mismatch = service.verify({"detections": [
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
    ]}, structured_extraction={"product": {"brand": {"value": "ABC"}}})
    assert provider.calls == ["4006381333931"]
    assert provider.availability_calls == 1
    assert mismatch["status"] == "REVIEW_REQUIRED"
    assert mismatch["barcodes"][0]["ocr_comparison"][0]["state"] == "MISMATCH"
    assert mismatch["authoritative"] is False
    assert "compliance" not in mismatch


def test_verified_provider_uses_exact_ocr_comparison_with_quantity_format_normalization():
    class Provider:
        provider_name = "test-only"

        def availability(self):
            return {"available": True, "status": "READY"}

        def verify_gtin(self, gtin):
            return {"verified": True, "product_identity": {
                "brand": "ABC Foods", "description": "Rice", "net_content": "500g",
            }}

    result = GS1VerificationService(Provider()).verify({"detections": [
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
    ]}, structured_extraction={
        "product": {"brand": {"value": "abc-foods"}, "name": {"value": "Rice"}},
        "quantity": {"value": "500 g"},
    })
    assert result["status"] == "EXTERNAL_VERIFIED_SUPPORTING"
    assert [item["state"] for item in result["barcodes"][0]["ocr_comparison"]] == ["CONSISTENT"] * 3


def test_provider_retries_only_bounded_transient_failures():
    class Provider:
        provider_name = "test-only"

        def __init__(self):
            self.calls = 0

        def availability(self):
            return {"available": True, "status": "READY"}

        def verify_gtin(self, gtin):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("transient timeout")
            return {"verified": True, "product_identity": {"brand": "ABC"}, "verified_at": "2026-10-02T12:00:00Z"}

    provider = Provider()
    result = GS1VerificationService(provider, max_retries=2).verify({"detections": [
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
    ]})
    assert provider.calls == 3
    external = result["barcodes"][0]["external_verification"]
    assert external["status"] == "VERIFIED"
    assert external["verified_at"] == "2026-10-02T12:00:00Z"


def test_provider_authentication_failure_is_not_retried():
    class Provider:
        provider_name = "test-only"

        def __init__(self):
            self.calls = 0

        def availability(self):
            return {"available": True, "status": "READY"}

        def verify_gtin(self, gtin):
            self.calls += 1
            raise PermissionError("auth failure")

    provider = Provider()
    result = GS1VerificationService(provider, max_retries=2).verify({"detections": [
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
    ]})
    assert provider.calls == 1
    assert result["barcodes"][0]["external_verification"]["status"] == "AUTHENTICATION_FAILURE"


def test_provider_unavailable_keeps_local_validation_and_no_identity_claim():
    class Provider:
        provider_name = "test-only"

        def availability(self):
            return {"available": False, "status": "PROVIDER_UNAVAILABLE"}

        def verify_gtin(self, gtin):
            raise AssertionError("unavailable providers must not be queried")

    result = GS1VerificationService(Provider()).verify({"detections": [
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
    ]})
    barcode = result["barcodes"][0]
    assert result["status"] == "LOCAL_VALIDATION_ONLY"
    assert barcode["check_digit_valid"] is True
    assert barcode["external_verification"]["status"] == "PROVIDER_UNAVAILABLE"
    assert barcode["product_identity"] == {"brand": None, "description": None, "net_content": None}


def test_barcode_crop_is_a_separate_image_and_rejects_degenerate_geometry():
    original = BytesIO()
    Image.new("RGB", (80, 60), "white").save(original, format="PNG")
    image_bytes = original.getvalue()
    crop = crop_barcode_image(image_bytes, [10, 12, 40, 32])
    assert crop and crop != image_bytes
    with Image.open(BytesIO(crop)) as cropped:
        assert cropped.format == "PNG"
        assert cropped.width > 0 and cropped.height > 0
    assert crop_barcode_image(image_bytes, [12, 12, 12, 12]) is None


@pytest.mark.parametrize(("failure", "expected"), [
    ("auth", "AUTHENTICATION_FAILURE"), ("timeout", "TIMEOUT"), ("malformed", "MALFORMED_RESPONSE"),
])
def test_provider_failure_does_not_become_success_or_leak_error_text(failure, expected):
    class Provider:
        provider_name = "test-only"

        def availability(self):
            return {"available": True, "status": "READY"}

        def verify_gtin(self, gtin):
            if failure == "auth":
                raise PermissionError("secret token")
            if failure == "timeout":
                raise TimeoutError("slow provider")
            return {"verified": True, "product_identity": "invalid"}

    result = GS1VerificationService(Provider()).verify({"detections": [
        {"raw_value": "4006381333931", "symbology": "EAN_13"},
    ]})
    external = result["barcodes"][0]["external_verification"]
    assert external["status"] == expected
    assert external["verified"] is False
    assert "secret" not in str(result)
