from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.services.audit import append_audit_event


def _canonical_json(value: Any) -> str:
    def normalize(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return normalize(obj.model_dump(mode="json"))
        if hasattr(obj, "dict"):
            return normalize(obj.dict())
        if isinstance(obj, dict):
            return {str(key): normalize(val) for key, val in sorted(obj.items(), key=lambda item: str(item[0]))}
        if isinstance(obj, (list, tuple)):
            return [normalize(item) for item in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_value(value: Any) -> str:
    if isinstance(value, bytes):
        return sha256_bytes(value)
    if isinstance(value, str):
        return sha256_bytes(value.encode("utf-8"))
    return sha256_bytes(_canonical_json(value).encode("utf-8"))


def merkle_root(values: list[str]) -> str:
    if not values:
        return sha256_bytes(b"")
    level = [item.encode("utf-8") for item in values]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level: list[bytes] = []
        for idx in range(0, len(level), 2):
            next_level.append(hashlib.sha256(level[idx] + level[idx + 1]).digest())
        level = next_level
    return level[0].hex()


class EvidenceArtifact(BaseModel):
    artifact_id: str
    artifact_type: str
    sha256: str
    size: int
    source: str = "derived"
    derived_from: list[str] = Field(default_factory=list)
    model_version: str | None = None
    ruleset_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidencePackage(BaseModel):
    package_id: str
    evidence_id: str
    package_version: str = "1.0.0"
    created_at: str
    artifacts: list[EvidenceArtifact] = Field(default_factory=list)
    ruleset_digest: str | None = None
    model_digests: dict[str, str] = Field(default_factory=dict)
    audit_refs: list[str] = Field(default_factory=list)
    merkle_root: str

    @classmethod
    def from_artifacts(
        cls,
        *,
        evidence_id: str,
        artifacts: list[EvidenceArtifact],
        ruleset_digest: str | None = None,
        model_digests: dict[str, str] | None = None,
        audit_refs: list[str] | None = None,
    ) -> "EvidencePackage":
        ordered = sorted(artifacts, key=lambda item: item.artifact_id)
        root = merkle_root([artifact.sha256 for artifact in ordered])
        return cls(
            package_id=f"evidence-{sha256_bytes(root.encode('utf-8'))[:16]}",
            evidence_id=evidence_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            artifacts=ordered,
            ruleset_digest=ruleset_digest,
            model_digests=model_digests or {},
            audit_refs=audit_refs or [],
            merkle_root=root,
        )

    def verify(self, artifact_payloads: dict[str, Any] | None = None) -> dict[str, Any]:
        changed: list[str] = []
        for artifact in self.artifacts:
            expected = artifact.sha256
            if artifact_payloads is None:
                continue
            payload = artifact_payloads.get(artifact.artifact_id)
            if payload is None:
                changed.append(artifact.artifact_id)
                continue
            if isinstance(payload, dict):
                if "sha256" in payload:
                    actual = str(payload["sha256"])
                elif "value" in payload:
                    actual = sha256_value(payload["value"])
                else:
                    actual = sha256_value(payload)
            else:
                actual = sha256_value(payload)
            if actual != expected:
                changed.append(artifact.artifact_id)
        recomputed_root = merkle_root([artifact.sha256 for artifact in sorted(self.artifacts, key=lambda item: item.artifact_id)])
        return {"valid": not changed and recomputed_root == self.merkle_root, "changed_artifacts": changed, "recomputed_root": recomputed_root}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _artifact_entry(
    *,
    artifact_id: str,
    artifact_type: str,
    value: Any,
    source: str,
    derived_from: list[str] | None = None,
    model_version: str | None = None,
    ruleset_version: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceArtifact:
    payload = b"" if value is None else value if isinstance(value, (bytes, bytearray)) else _canonical_json(value).encode("utf-8")
    return EvidenceArtifact(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        sha256=sha256_bytes(payload),
        size=len(payload),
        source=source,
        derived_from=derived_from or [],
        model_version=model_version,
        ruleset_version=ruleset_version,
        metadata=metadata or {},
    )


def build_evidence_package(
    *,
    evidence_id: str,
    raw_artifacts: dict[str, Any],
    ruleset_digest: str | None = None,
    model_digests: dict[str, str] | None = None,
    audit_refs: list[str] | None = None,
) -> EvidencePackage:
    artifacts: list[EvidenceArtifact] = []
    for artifact_id, payload in sorted(raw_artifacts.items()):
        artifact_type = payload.get("artifact_type") if isinstance(payload, dict) else "unknown"
        value = payload.get("value") if isinstance(payload, dict) else payload
        source = payload.get("source", "derived") if isinstance(payload, dict) else "derived"
        derived_from = payload.get("derived_from", []) if isinstance(payload, dict) else []
        model_version = payload.get("model_version") if isinstance(payload, dict) else None
        ruleset_version = payload.get("ruleset_version") if isinstance(payload, dict) else None
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        artifacts.append(_artifact_entry(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            value=value,
            source=source,
            derived_from=derived_from,
            model_version=model_version,
            ruleset_version=ruleset_version,
            metadata=metadata,
        ))
    return EvidencePackage.from_artifacts(
        evidence_id=evidence_id,
        artifacts=artifacts,
        ruleset_digest=ruleset_digest,
        model_digests=model_digests or {},
        audit_refs=audit_refs or [],
    )


def record_evidence_package(db, package: EvidencePackage, *, entity_id: str, entity_type: str = "evidence") -> dict[str, Any]:
    if db.evidence_packages.find_one({"package_id": package.package_id}):
        raise ValueError(f"Evidence package already exists: {package.package_id}")
    document = package.to_dict()
    db.evidence_packages.insert_one(document)
    return append_audit_event(
        db,
        event_type="evidence_package_created",
        entity_type=entity_type,
        entity_id=entity_id,
        actor_role="system",
        action="evidence_package_created",
        details={
            "package_id": package.package_id,
            "evidence_id": package.evidence_id,
            "merkle_root": package.merkle_root,
            "artifact_count": len(package.artifacts),
        },
    )


def assemble_evidence_package_from_scan(
    *,
    evidence_id: str,
    ocr_result: dict[str, Any],
    compliance_result: dict[str, Any] | None = None,
    audit_refs: list[str] | None = None,
    model_digests: dict[str, str] | None = None,
    ruleset_digest: str | None = None,
    scan_evidence: dict[str, Any] | None = None,
    source_images: list[dict[str, Any]] | None = None,
) -> EvidencePackage:
    raw_artifacts: dict[str, Any] = {
        "ocr_output": {"artifact_type": "ocr_output", "value": ocr_result, "source": "ocr", "metadata": {"document_id": ocr_result.get("document_id")}},
        "compliance_findings": {"artifact_type": "compliance_findings", "value": compliance_result or {}, "source": "compliance", "metadata": {"overall_status": (compliance_result or {}).get("overall_status") if isinstance(compliance_result, dict) else None}},
    }
    if isinstance(ocr_result, dict):
        normalized = ocr_result.get("normalized_result") or ocr_result.get("normalized_ocr_result")
        if normalized is not None:
            raw_artifacts["normalized_ocr_result"] = {"artifact_type": "normalized_ocr_result", "value": normalized, "source": "normalized", "derived_from": ["ocr_output"]}

    image_artifact_ids: list[str] = []
    for index, source_image in enumerate(source_images or []):
        contents = source_image.get("contents")
        if not isinstance(contents, (bytes, bytearray)):
            raise ValueError(f"Source image {index} must contain original bytes")
        image_artifact_id = f"source_image_{index}"
        actual_sha256 = sha256_bytes(bytes(contents))
        declared_sha256 = source_image.get("sha256")
        if declared_sha256 is not None and declared_sha256 != actual_sha256:
            raise ValueError(f"Source image {index} SHA-256 does not match its original bytes")
        image_artifact_ids.append(image_artifact_id)
        raw_artifacts[image_artifact_id] = {
            "artifact_type": "source_image",
            "value": bytes(contents),
            "source": "original_upload",
            "metadata": {
                "file_name": source_image.get("file_name"),
                "mime_type": source_image.get("mime_type"),
                "source_image_sha256": actual_sha256,
                "side": source_image.get("side"),
            },
        }
        image_authenticity = source_image.get("authenticity")
        if image_authenticity is not None:
            raw_artifacts[f"authenticity_{index}"] = {
                "artifact_type": "image_integrity_evidence",
                "value": image_authenticity,
                "source": "integrity_analysis",
                "derived_from": [image_artifact_id],
            }

    for key, artifact_type, source in (
        ("sourceImageSha256", "source_image_hash", "integrity"),
        ("sourceMimeType", "source_image_metadata", "upload"),
        ("authenticity", "image_integrity_evidence", "integrity_analysis"),
        ("fontHeightMeasurements", "physical_measurement_evidence", "measurement"),
        ("measurementReview", "measurement_review", "measurement"),
        ("structuredExtraction", "structured_extraction", "extraction"),
        ("secondaryOcr", "secondary_ocr_evidence", "secondary_ocr"),
        ("captureAssessments", "capture_quality_evidence", "capture_quality"),
        ("pdpDetection", "experimental_pdp_evidence", "experimental_detector"),
        ("pdpDetections", "experimental_pdp_evidence", "experimental_detector"),
        ("barcodeCalibration", "barcode_calibration_evidence", "calibration"),
        ("pdpGeometry", "pdp_geometry_evidence", "geometry"),
        ("panelHashes", "panel_image_hashes", "integrity"),
    ):
        if isinstance(scan_evidence, dict) and key in scan_evidence:
            derived_from = image_artifact_ids if key in {"authenticity", "sourceImageSha256", "sourceMimeType", "panelHashes", "captureAssessments"} else ["ocr_output"]
            raw_artifacts[key] = {
                "artifact_type": artifact_type,
                "value": scan_evidence[key],
                "source": source,
                "derived_from": derived_from,
            }

    return build_evidence_package(
        evidence_id=evidence_id,
        raw_artifacts=raw_artifacts,
        ruleset_digest=ruleset_digest,
        model_digests=model_digests or {},
        audit_refs=audit_refs or [],
    )
