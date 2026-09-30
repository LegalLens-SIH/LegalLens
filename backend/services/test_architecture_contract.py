from pathlib import Path

import pytest

from backend.services.architecture_contract import (
    ARCHITECTURE_CONTRACT,
    OperationalStatus,
    load_architecture_contract,
)


def test_architecture_contract_has_shared_statuses_and_safe_defaults():
    assert set(ARCHITECTURE_CONTRACT.status_definitions) == {status.value for status in OperationalStatus}
    assert ARCHITECTURE_CONTRACT.production_defaults["experimental_authority"] is False
    assert ARCHITECTURE_CONTRACT.production_defaults["missing_evidence_status"] == "REVIEW_REQUIRED"
    assert ARCHITECTURE_CONTRACT.feature_flags["pdp_enabled"] is False


def test_invalid_architecture_contract_fails_closed(tmp_path: Path):
    path = tmp_path / "architecture.yaml"
    path.write_text("version: 1\nstatus_definitions: [COMPLIANT]\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid architecture contract"):
        load_architecture_contract(path)
