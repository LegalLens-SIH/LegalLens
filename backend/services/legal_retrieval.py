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
from pathlib import Path
from typing import Optional

from backend.models.legal import LegalBasisResult, LegalProvision, LegalSourceDocument

_CORPUS_PATH = Path(__file__).resolve().parents[1] / "legal_corpus" / "legal_metrology_packaged_commodities_2011.json"

_REQUIRED_SOURCE_FIELDS = ("source_id", "title", "citation", "version", "acquired_at", "verification_note")
_REQUIRED_PROVISION_FIELDS = ("provision_id", "source_id", "rule_sub_rule_clause", "text", "effective_status", "is_currently_effective", "linked_rule_id", "linked_field")


class LegalCorpusError(ValueError):
    """Raised when the corpus file is missing a required field - a loud,
    load-time failure rather than a silent skip, so a malformed corpus can
    never quietly serve incomplete/unsourced data (constitution Principle III)."""


def _validate_source(source: dict) -> None:
    for field_name in _REQUIRED_SOURCE_FIELDS:
        if not source.get(field_name):
            raise LegalCorpusError(f"Legal source '{source.get('source_id', '<unknown>')}' is missing required field '{field_name}'")


def _validate_provision(provision: dict) -> None:
    for field_name in _REQUIRED_PROVISION_FIELDS:
        if provision.get(field_name) in (None, ""):
            raise LegalCorpusError(f"Legal provision '{provision.get('provision_id', '<unknown>')}' is missing required field '{field_name}'")


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

    sources_by_id: dict[str, LegalSourceDocument] = {}
    for source in raw.get("sources", []):
        _validate_source(source)
        sources_by_id[source["source_id"]] = LegalSourceDocument(**source)

    provisions = raw.get("provisions", [])
    for provision in provisions:
        _validate_provision(provision)
        if provision["source_id"] not in sources_by_id:
            raise LegalCorpusError(f"Legal provision '{provision['provision_id']}' references unknown source_id '{provision['source_id']}'")

    return sources_by_id, provisions


# Loaded once at import time (mirrors compliance_engine.py's own ruleset-loading
# pattern) - the corpus is small and static; reloading it per request would be
# pure overhead with no benefit.
_SOURCES_BY_ID, _PROVISIONS = load_corpus()


def lookup_by_rule_id(rule_id: str, field: Optional[str] = None) -> list[LegalProvision]:
    """Exact deterministic match against the loaded corpus - the primary
    retrieval mechanism (spec FR-009). Returns every currently-effective
    provision linked to this (rule_id, field) pair; empty list if none."""
    matches = []
    for provision in _PROVISIONS:
        if provision["linked_rule_id"] != rule_id:
            continue
        if field is not None and provision["linked_field"] != field:
            continue
        if not provision["is_currently_effective"]:
            continue
        source = _SOURCES_BY_ID[provision["source_id"]]
        matches.append(LegalProvision(
            provision_id=provision["provision_id"],
            rule_sub_rule_clause=provision["rule_sub_rule_clause"],
            text=provision["text"],
            effective_status=provision["effective_status"],
            is_currently_effective=provision["is_currently_effective"],
            source=source,
        ))
    return matches


def get_legal_basis(rule_id: str, field: Optional[str] = None) -> LegalBasisResult:
    """The single entry point this feature's API layer calls. Never raises
    for an unmatched (rule_id, field) - that is the expected, first-class
    "not_available" outcome (spec User Story 3), not an error."""
    matches = lookup_by_rule_id(rule_id, field)
    if not matches:
        return LegalBasisResult(rule_id=rule_id, field=field, status="not_available", retrieval_method=None, provisions=[])
    return LegalBasisResult(rule_id=rule_id, field=field, status="found", retrieval_method="deterministic_link", provisions=matches)
