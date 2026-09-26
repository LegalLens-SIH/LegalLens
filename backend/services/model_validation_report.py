from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ModelValidationReport(BaseModel):
    model_identifier: str | None = None
    model_version: str | None = None
    model_hash: str | None = None
    dataset_identifier: str | None = None
    evaluation_date: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    test_counts: dict[str, int] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    status: str = "not_evaluated"
    note: str = "No verified benchmark dataset or evaluation result is available in this environment."

    @classmethod
    def from_available(cls, *, model_identifier: str | None = None, model_version: str | None = None, model_hash: str | None = None, dataset_identifier: str | None = None, metrics: dict[str, Any] | None = None, thresholds: dict[str, Any] | None = None, test_counts: dict[str, int] | None = None, failures: list[str] | None = None, environment: dict[str, Any] | None = None, reproducibility: dict[str, Any] | None = None) -> "ModelValidationReport":
        if not any([model_identifier, model_version, model_hash, dataset_identifier, metrics, thresholds, test_counts, failures, environment]):
            return cls(evaluation_date=datetime.now(timezone.utc).isoformat())
        return cls(
            model_identifier=model_identifier,
            model_version=model_version,
            model_hash=model_hash,
            dataset_identifier=dataset_identifier,
            evaluation_date=datetime.now(timezone.utc).isoformat(),
            metrics=metrics or {},
            thresholds=thresholds or {},
            test_counts=test_counts or {},
            failures=failures or [],
            environment=environment or {},
            reproducibility=reproducibility or {},
            status="evaluated" if metrics or test_counts else "partial",
        )
