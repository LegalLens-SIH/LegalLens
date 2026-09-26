from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.services.audit import append_audit_event


class SignoffError(ValueError):
    pass


def validate_signoff(
    *,
    reviewer_identity: dict[str, Any],
    evidence_package_id: str,
    evidence_merkle_root: str,
    ruleset_digest: str | None,
    model_digests: dict[str, str] | None,
    timestamp_reference: str | None,
    certificate_reference: str | None,
    review_decision: str,
    stored_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    if not reviewer_identity or not reviewer_identity.get("_id"):
        raise SignoffError("Authorized reviewer identity is required.")
    if not evidence_package_id:
        raise SignoffError("Evidence package ID is required.")
    if not evidence_merkle_root:
        raise SignoffError("Evidence Merkle root is required.")
    stored_root = (stored_evidence or {}).get("merkle_root") if isinstance(stored_evidence, dict) else None
    if stored_root and stored_root != evidence_merkle_root:
        raise SignoffError("Sign-off evidence hash/Merkle root does not match the stored evidence package.")
    if not review_decision:
        raise SignoffError("Review decision is required.")
    return {
        "authorized_reviewer": reviewer_identity.get("email") or reviewer_identity.get("fullName") or str(reviewer_identity.get("_id")),
        "evidence_package_id": evidence_package_id,
        "evidence_merkle_root": evidence_merkle_root,
        "ruleset_digest": ruleset_digest,
        "model_digests": model_digests or {},
        "timestamp_reference": timestamp_reference,
        "certificate_reference": certificate_reference,
        "review_decision": review_decision,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }


def record_signoff(db, *, package_id: str, reviewer: dict[str, Any], signoff: dict[str, Any]) -> dict[str, Any]:
    record = {"package_id": package_id, **signoff, "createdAt": datetime.now(timezone.utc)}
    db.signoffs.insert_one(record)
    return append_audit_event(
        db,
        event_type="legal_compliance_signoff",
        entity_type="evidence_package",
        entity_id=str(package_id),
        user_id=reviewer.get("_id"),
        actor_role=reviewer.get("role"),
        action="signoff_recorded",
        details={"review_decision": signoff.get("review_decision"), "evidence_merkle_root": signoff.get("evidence_merkle_root")},
    )
