from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RulesetValidationReport(BaseModel):
    ruleset_identifier: str | None = None
    ruleset_version: str | None = None
    ruleset_file_hash: str | None = None
    rule_count: int = 0
    effective_date: str | None = None
    citations: list[str] = Field(default_factory=list)
    test_count: int = 0
    pass_count: int = 0
    fail_count: int = 0
    unresolved_or_blocked: list[str] = Field(default_factory=list)
    generation_timestamp: str
    status: str = "requires_verified_legal_sources"
    note: str = "Legal source verification is not available in this environment; the rules corpus is explicitly marked as requiring verified legal source data before any legal citation claim is made."

    @classmethod
    def from_ruleset(cls, *, ruleset_path: str | Path | None = None, ruleset_identifier: str | None = None, ruleset_version: str | None = None, effective_date: str | None = None, citations: list[str] | None = None, test_count: int = 0, pass_count: int = 0, fail_count: int = 0, unresolved_or_blocked: list[str] | None = None) -> "RulesetValidationReport":
        path = Path(ruleset_path) if ruleset_path is not None else None
        digest = None
        if path is not None and path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls(
            ruleset_identifier=ruleset_identifier,
            ruleset_version=ruleset_version,
            ruleset_file_hash=digest,
            rule_count=0,
            effective_date=effective_date,
            citations=citations or [],
            test_count=test_count,
            pass_count=pass_count,
            fail_count=fail_count,
            unresolved_or_blocked=unresolved_or_blocked or ["Legal source verification pending"],
            generation_timestamp=datetime.now(timezone.utc).isoformat(),
        )
