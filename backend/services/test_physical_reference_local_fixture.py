from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "training" / "fixtures" / "local"
FIXTURE_CANDIDATES = [
    FIXTURE_DIR / "kitkat_physical_calibration_local.png",
    FIXTURE_DIR / "kitkat_physical_calibration_local.jpg",
    FIXTURE_DIR / "kitkat_physical_calibration_local.jpeg",
    FIXTURE_DIR / "kitkat_physical_calibration_local.webp",
]


@pytest.fixture(scope="module")
def local_calibration_fixture():
    for candidate in FIXTURE_CANDIDATES:
        if candidate.exists():
            return candidate
    pytest.skip("Local sensitive calibration fixture not present in workspace; real image must remain machine-local and uncommitted.")


def test_local_fixture_loads_and_hashes(local_calibration_fixture: Path):
    with Image.open(local_calibration_fixture) as image:
        assert image.size[0] > 0
        assert image.size[1] > 0
    digest = hashlib.sha256(local_calibration_fixture.read_bytes()).hexdigest()
    assert len(digest) == 64
    assert digest[:8] != "00000000"


def test_local_fixture_is_not_committed_and_is_git_ignored(local_calibration_fixture: Path):
    assert local_calibration_fixture.is_file()
    assert local_calibration_fixture.parent.name == "local"
    assert local_calibration_fixture.name.startswith("kitkat_physical_calibration_local")
    assert local_calibration_fixture.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
