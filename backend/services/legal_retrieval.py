"""
Legal-basis retrieval: given an existing ComplianceEngine finding's rule_id
(and optional field), return the verified legal provision(s) that support
it, or an explicit "not available" result.

Scope (deliberately narrow, same spirit as compliance_engine.py's own
scoping comments): this module NEVER computes, overrides, or implies a
compliance decision. It only reads the corpus built from
backend/legal_corpus/legal_metrology_packaged_commodities_2011.json and
looks up an exact (rule_id, field) match.

This is deterministic-only BY DECISION, not merely by not-yet-having-gotten-
to-it. Phase 8 (specs/001-legal-rag/tasks.md) evaluated semantic retrieval
(BAAI/bge-small-en-v1.5 and BAAI/bge-m3) against a 36-query benchmark built
from this corpus and DECLINED it: both models missed the spec's own
precision bar (SC-002, >=90% top-1; actual 78-81%) and wrong-rule ceiling
(SC-005, <5%; actual 15.6% for both models - e.g. confusing LMPC-R6's and
LMPC-R24's near-duplicate "manufacturer name/address" and "net quantity"
clauses), with no similarity threshold able to separate a confident-correct
match from a confident-wrong one at this corpus's size. Critically, every
real caller in this system (frontend/assets/legal-basis-client.js ->
backend/api/legal_basis.py) already supplies an exact rule_id+field from an
existing ComplianceEngine finding - there is no freeform-query use case for
semantic search to fill here. Full benchmark methodology and results are
not separately filed; this decision and its evidence are recorded here and
in specs/001-legal-rag/tasks.md's Phase 8 section.

Reconsider semantic retrieval only if: (a) corpus coverage grows well
beyond LMPC-R6/LMPC-R24 such that rule/field vocabulary genuinely
diversifies (reducing the near-duplicate-clause collisions the benchmark
found), or (b) a real freeform-query use case is introduced that an exact
rule_id/field key cannot serve - re-run the same benchmark methodology
against the larger/changed corpus before deciding, rather than assuming
more scale alone fixes the wrong-rule-rate problem found here.

The single most important invariant enforced here: when no exact match
exists, `get_legal_basis` returns `status="not_available"` with empty
`provisions` - never a guess, never a partial/low-confidence answer
presented as confident. See backend/models/legal.py's LegalBasisResult
docstring.
"""
from __future__ import annotations

import json
import hashlib
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from backend.models.legal import LegalBasisResult, LegalCorpusStatus, LegalProvision, LegalSourceDocument

_CORPUS_PATH = Path(__file__).resolve().parents[1] / "legal_corpus" / "legal_metrology_packaged_commodities_2011.json"

_REQUIRED_SOURCE_FIELDS = ("source_id", "title", "citation", "version", "acquired_at", "verification_note", "verification_status")
_REQUIRED_PROVISION_FIELDS = ("provision_id", "source_id", "rule_sub_rule_clause", "text", "effective_status", "is_currently_effective", "linked_rule_id", "linked_field")


class LegalCorpusError(ValueError):
    """Raised when the corpus file is missing a required field - a loud,
    load-time failure rather than a silent skip, so a malformed corpus can
    never quietly serve incomplete/unsourced data (constitution Principle III)."""


def _validate_date_range(document: dict, *, label: str) -> None:
    parsed: dict[str, date] = {}
    for key in ("effective_from", "effective_until"):
        value = document.get(key)
        if value is None:
            continue
        try:
            parsed[key] = value if isinstance(value, date) else date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise LegalCorpusError(f"{label} has an invalid {key} date") from exc
    start = parsed.get("effective_from")
    end = parsed.get("effective_until")
    if start is not None and end is not None and start > end:
        raise LegalCorpusError(f"{label} has an effective date range ending before it starts")


def _validate_source(source: dict) -> None:
    if not isinstance(source, dict):
        raise LegalCorpusError("Each legal source must be an object")
    for field_name in _REQUIRED_SOURCE_FIELDS:
        if not source.get(field_name):
            raise LegalCorpusError(f"Legal source '{source.get('source_id', '<unknown>')}' is missing required field '{field_name}'")
    try:
        LegalSourceDocument(**source)
    except Exception as exc:
        raise LegalCorpusError(f"Legal source '{source.get('source_id', '<unknown>')}' has invalid metadata: {exc}") from exc
    _validate_date_range(source, label=f"Legal source '{source['source_id']}'")
    if source.get("source_url"):
        parsed_url = urlsplit(str(source["source_url"]))
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.fragment:
            raise LegalCorpusError(f"Legal source '{source['source_id']}' has an unsafe or malformed source_url")


def _validate_provision(provision: dict) -> None:
    if not isinstance(provision, dict):
        raise LegalCorpusError("Each legal provision must be an object")
    for field_name in _REQUIRED_PROVISION_FIELDS:
        if provision.get(field_name) in (None, ""):
            raise LegalCorpusError(f"Legal provision '{provision.get('provision_id', '<unknown>')}' is missing required field '{field_name}'")
    _validate_date_range(provision, label=f"Legal provision '{provision['provision_id']}'")
    if provision.get("effective_status") not in {"original", "amended", "superseded"}:
        raise LegalCorpusError(f"Legal provision '{provision['provision_id']}' has an invalid effective_status")
    if not isinstance(provision.get("is_currently_effective"), bool):
        raise LegalCorpusError(f"Legal provision '{provision['provision_id']}' must declare is_currently_effective")
    if provision.get("effective_status") == "superseded" and provision["is_currently_effective"]:
        raise LegalCorpusError(f"Superseded provision '{provision['provision_id']}' cannot be marked currently effective")


def load_corpus(path: Path = _CORPUS_PATH) -> tuple[dict[str, LegalSourceDocument], list[dict]]:
    """Load and validate the legal corpus file. Returns (sources_by_id, raw_provisions).

    Every source and every provision is validated against its required
    fields before being trusted - see _REQUIRED_SOURCE_FIELDS/
    _REQUIRED_PROVISION_FIELDS and LegalCorpusError above. A provision
    referencing a source_id that doesn't exist in `sources` is also
    rejected: "text must trace to a source_id that exists" (data-model.md).
    """
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise LegalCorpusError("Legal corpus root must be a JSON object")
    schema_version = raw.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise LegalCorpusError("Legal corpus schema_version must be a positive integer")

    sources_by_id: dict[str, LegalSourceDocument] = {}
    raw_sources = raw.get("sources", [])
    if not isinstance(raw_sources, list):
        raise LegalCorpusError("Legal corpus sources must be a list")
    for source in raw_sources:
        _validate_source(source)
        if source["source_id"] in sources_by_id:
            raise LegalCorpusError(f"Duplicate legal source_id '{source['source_id']}'")
        sources_by_id[source["source_id"]] = LegalSourceDocument(**source)
    for source in sources_by_id.values():
        if source.amends_source_id and source.amends_source_id not in sources_by_id:
            raise LegalCorpusError(f"Legal source '{source.source_id}' amends unknown source_id '{source.amends_source_id}'")

    provisions = raw.get("provisions", [])
    if not isinstance(provisions, list):
        raise LegalCorpusError("Legal corpus provisions must be a list")
    provision_ids: set[str] = set()
    raw_provisions_by_id: dict[str, dict] = {}
    for provision in provisions:
        _validate_provision(provision)
        if provision["provision_id"] in provision_ids:
            raise LegalCorpusError(f"Duplicate legal provision_id '{provision['provision_id']}'")
        provision_ids.add(provision["provision_id"])
        raw_provisions_by_id[provision["provision_id"]] = provision
        if provision["source_id"] not in sources_by_id:
            raise LegalCorpusError(f"Legal provision '{provision['provision_id']}' references unknown source_id '{provision['source_id']}'")
    for provision in provisions:
        supersedes = provision.get("supersedes_provision_id")
        if supersedes and supersedes not in raw_provisions_by_id:
            raise LegalCorpusError(f"Legal provision '{provision['provision_id']}' supersedes unknown provision_id '{supersedes}'")

    return sources_by_id, provisions


# Loaded once at import time (mirrors compliance_engine.py's own ruleset-loading
# pattern) - the corpus is small and static; reloading it per request would be
# pure overhead with no benefit.
_SOURCES_BY_ID, _PROVISIONS = load_corpus()


def get_corpus_status(path: Path = _CORPUS_PATH) -> LegalCorpusStatus:
    """Return source-backed corpus schema and explicitly blocked coverage."""
    load_corpus(path)
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    unresolved = raw.get("unresolved_fields", [])
    blocked = raw.get("blocked_fields", [])
    if not isinstance(unresolved, list) or any(not isinstance(field, str) or not field for field in unresolved):
        raise LegalCorpusError("Legal corpus unresolved_fields must be a list of non-empty strings")
    if not isinstance(blocked, list) or any(not isinstance(field, str) or not field for field in blocked):
        raise LegalCorpusError("Legal corpus blocked_fields must be a list of non-empty strings")
    if len(set(unresolved)) != len(unresolved) or len(set(blocked)) != len(blocked):
        raise LegalCorpusError("Legal corpus unresolved_fields and blocked_fields must not contain duplicates")
    if set(unresolved).intersection(blocked):
        raise LegalCorpusError("A legal field cannot be both unresolved and blocked")
    sources, provisions = load_corpus(path)
    verification_counts: dict[str, int] = {}
    for source in sources.values():
        state = source.verification_status or "unresolved"
        verification_counts[state] = verification_counts.get(state, 0) + 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return LegalCorpusStatus(
        corpus_id=str(raw.get("corpus_id", "unknown")),
        schema_version=raw.get("schema_version", 1),
        unresolved_fields=unresolved,
        blocked_fields=blocked,
        validation_status="valid",
        source_count=len(sources),
        provision_count=len(provisions),
        source_verification_counts=verification_counts,
        corpus_sha256=digest,
    )


_CORPUS_STATUS = get_corpus_status()


def lookup_by_rule_id(rule_id: str, field: Optional[str] = None) -> list[LegalProvision]:
    """Exact deterministic match against the loaded corpus - the primary
    retrieval mechanism (spec FR-009). Returns every currently-effective
    provision linked to this (rule_id, field) pair; empty list if none."""
    matches = []
    today = date.today()
    for provision in _PROVISIONS:
        if provision["linked_rule_id"] != rule_id:
            continue
        if field is not None and provision["linked_field"] != field:
            continue
        if not provision["is_currently_effective"]:
            continue
        provision_start = date.fromisoformat(str(provision["effective_from"])) if provision.get("effective_from") else None
        provision_end = date.fromisoformat(str(provision["effective_until"])) if provision.get("effective_until") else None
        if (provision_start is not None and today < provision_start) or (provision_end is not None and today > provision_end):
            continue
        source = _SOURCES_BY_ID[provision["source_id"]]
        if source.verification_status in {"unresolved", "blocked"}:
            continue
        if source.effective_from is not None and today < source.effective_from:
            continue
        if source.effective_until is not None and today > source.effective_until:
            continue
        matches.append(LegalProvision(
            provision_id=provision["provision_id"],
            rule_sub_rule_clause=provision["rule_sub_rule_clause"],
            text=provision["text"],
            effective_status=provision["effective_status"],
            is_currently_effective=provision["is_currently_effective"],
            source=source,
            effective_from=provision.get("effective_from"),
            effective_until=provision.get("effective_until"),
        ))
    return sorted(matches, key=lambda provision: (provision.provision_id, provision.source.source_id))


def get_legal_basis(rule_id: str, field: Optional[str] = None) -> LegalBasisResult:
    """The single entry point this feature's API layer calls. Never raises
    for an unmatched (rule_id, field) - that is the expected, first-class
    "not_available" outcome (spec User Story 3), not an error."""
    if field is not None and field in _CORPUS_STATUS.blocked_fields:
        return LegalBasisResult(
            rule_id=rule_id, field=field, status="not_available", retrieval_method=None,
            provisions=[], availability_reason="legal_source_blocked",
            corpus_id=_CORPUS_STATUS.corpus_id, corpus_schema_version=_CORPUS_STATUS.schema_version, corpus_sha256=_CORPUS_STATUS.corpus_sha256,
        )
    if field is not None and field in _CORPUS_STATUS.unresolved_fields:
        return LegalBasisResult(
            rule_id=rule_id, field=field, status="not_available", retrieval_method=None,
            provisions=[], availability_reason="legal_source_unresolved",
            corpus_id=_CORPUS_STATUS.corpus_id, corpus_schema_version=_CORPUS_STATUS.schema_version, corpus_sha256=_CORPUS_STATUS.corpus_sha256,
        )
    matches = lookup_by_rule_id(rule_id, field)
    if not matches:
        return LegalBasisResult(
            rule_id=rule_id, field=field, status="not_available", retrieval_method=None,
            provisions=[], availability_reason="not_in_verified_corpus",
            corpus_id=_CORPUS_STATUS.corpus_id, corpus_schema_version=_CORPUS_STATUS.schema_version, corpus_sha256=_CORPUS_STATUS.corpus_sha256,
        )
    return LegalBasisResult(rule_id=rule_id, field=field, status="found", retrieval_method="deterministic_link", provisions=matches,
                            corpus_id=_CORPUS_STATUS.corpus_id, corpus_schema_version=_CORPUS_STATUS.schema_version, corpus_sha256=_CORPUS_STATUS.corpus_sha256)
