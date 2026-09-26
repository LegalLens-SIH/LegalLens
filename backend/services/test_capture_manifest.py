from __future__ import annotations

from backend.services.capture_manifest import CaptureManifest


def test_capture_manifest_requires_front_back_and_label():
    manifest = CaptureManifest()
    status = manifest.get_status()
    assert status["complete"] is False
    assert status["missing_panels"] == ["front", "back", "label"]


def test_capture_manifest_blocks_completion_until_all_required_panels_exist():
    manifest = CaptureManifest()
    manifest.record("front")
    manifest.record("back")
    status = manifest.get_status()
    assert status["complete"] is False
    assert status["officer_review_required"] is True
    assert status["missing_panels"] == ["label"]

    manifest.record("label")
    status = manifest.get_status()
    assert status["complete"] is True
    assert status["officer_review_required"] is False
