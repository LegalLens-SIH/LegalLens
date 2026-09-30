from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


PORTAL_DIR = Path(__file__).resolve().parents[2]
ARCHITECTURE_PATH = PORTAL_DIR / "config" / "architecture.yaml"


class OperationalStatus(str, Enum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NEEDS_MANUAL_VERIFICATION = "NEEDS_MANUAL_VERIFICATION"


class ArchitectureContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    status_definitions: list[str]
    authoritative_components: list[str] = Field(min_length=1)
    experimental_components: list[str] = Field(default_factory=list)
    feature_flags: dict[str, bool]
    production_defaults: dict[str, Any]
    evidence_requirements: dict[str, list[str]]
    promotion_criteria: dict[str, list[str]]
    external_integration_states: dict[str, str]

    @field_validator("status_definitions")
    @classmethod
    def validate_statuses(cls, values: list[str]) -> list[str]:
        expected = {status.value for status in OperationalStatus}
        if set(values) != expected:
            raise ValueError("architecture status_definitions must exactly match OperationalStatus")
        return values


def load_architecture_contract(path: Path = ARCHITECTURE_PATH) -> ArchitectureContract:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Unable to load architecture contract: {path}") from exc
    try:
        contract = ArchitectureContract.model_validate(raw)
    except Exception as exc:
        raise RuntimeError(f"Invalid architecture contract: {path}") from exc
    if contract.production_defaults.get("experimental_authority") is not False:
        raise RuntimeError("Experimental authority must default to false")
    if contract.production_defaults.get("missing_evidence_status") != OperationalStatus.REVIEW_REQUIRED.value:
        raise RuntimeError("Missing evidence must default to REVIEW_REQUIRED")
    return contract


ARCHITECTURE_CONTRACT = load_architecture_contract()
