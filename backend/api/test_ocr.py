import asyncio

import cv2
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from backend.api import manufacturer as manufacturer_module
from backend.api import ocr as ocr_module
from backend.api.auth import manufacturer_user, official_user
from backend.main import app
from backend.api.ocr import _select_validation_profile
from backend.ocr import paddle_ocr_service as paddle_module
from backend.ocr.paddle_ocr_service import PaddleOCRService


def test_default_category_selects_retail_profile():
    """Existing e-commerce/retail behavior is unchanged by the wholesale
    wiring added alongside it."""
    assert _select_validation_profile("General") == ("e_commerce_product_listing", False)
    assert _select_validation_profile("") == ("e_commerce_product_listing", False)
    assert _select_validation_profile("Food & Beverage") == ("e_commerce_product_listing", False)


def test_instrument_category_selects_pos_hardware_profile_unchanged():
    """Pre-existing branch (not part of this task) - confirms it still
    works exactly as before after the wholesale branch was added next to
    it, and never forces package_type to wholesale."""
    for value in ("weighing_instrument", "Instrument", "POS Hardware", "  pos hardware  "):
        assert _select_validation_profile(value) == ("pos_hardware_audit", False)


def test_wholesale_category_selects_wholesale_profile():
    for value in ("wholesale", "Wholesale", "wholesale_package", "wholesale package", "  WHOLESALE  "):
        assert _select_validation_profile(value) == ("wholesale_package", True)


def test_upload_read_is_bounded_to_one_byte_over_the_configured_limit(monkeypatch):
    class Upload:
        def __init__(self):
            self.requested_sizes = []

        async def read(self, size=-1):
            self.requested_sizes.append(size)
            return b"x" * size

    monkeypatch.setattr(ocr_module, "MAX_UPLOAD_BYTES", 8)
    upload = Upload()

    contents = asyncio.run(ocr_module.read_upload_limited(upload))

    assert upload.requested_sizes == [9]
    assert len(contents) == 9


def test_all_upload_workflows_reject_oversize_files_after_bounded_reads(monkeypatch):
    official = {"_id": "oversize-official", "role": "official"}
    manufacturer = {"_id": "oversize-manufacturer", "role": "manufacturer"}
    monkeypatch.setattr(ocr_module, "MAX_UPLOAD_BYTES", 8)
    monkeypatch.setattr(manufacturer_module, "MAX_UPLOAD_BYTES", 8)
    read_sizes = []
    original_read = UploadFile.read

    async def track_read(upload, size=-1):
        read_sizes.append(size)
        return await original_read(upload, size)

    monkeypatch.setattr(UploadFile, "read", track_read)
    app.dependency_overrides[official_user] = lambda: official
    app.dependency_overrides[manufacturer_user] = lambda: manufacturer
    try:
        with TestClient(app) as client:
            upload = ("large.png", b"123456789", "image/png")
            responses = [
                client.post("/api/ocr", files={"file": upload}),
                client.post("/api/scans", files={"file": upload}),
                client.post("/api/analysis-jobs", files=[("file", upload)]),
                client.post("/api/manufacturer/self-check", files={"file": upload}),
            ]

        assert [response.status_code for response in responses] == [400, 400, 400, 400]
        assert all(size == 9 for size in read_sizes)
        assert all(response.json().get("error", {}).get("code") == "file_too_large" for response in (responses[0], responses[1], responses[3]))
    finally:
        app.dependency_overrides.pop(official_user, None)
        app.dependency_overrides.pop(manufacturer_user, None)


def test_oversized_image_dimensions_are_rejected_before_opencv_decode(monkeypatch, tmp_path):
    class ImageHeader:
        size = (11, 1)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(paddle_module, "MAX_OCR_IMAGE_PIXELS", 10)
    monkeypatch.setattr(paddle_module.Image, "open", lambda _path: ImageHeader())
    monkeypatch.setattr(cv2, "imdecode", lambda *_args: pytest.fail("oversized image must not be decoded"))
    image_path = tmp_path / "large.png"
    image_path.write_bytes(b"image-header")

    with pytest.raises(ValueError, match="pixel limit"):
        PaddleOCRService.load_image(image_path)
