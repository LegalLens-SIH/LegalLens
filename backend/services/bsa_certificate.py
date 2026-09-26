from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from backend.services.evidence_package import EvidencePackage


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _manual_pdf_document(lines: list[str], *, title: str = "Evidence Certificate") -> bytes:
    content_lines = [
        "BT",
        "/F1 18 Tf",
        "72 760 Td",
        f"({ _pdf_escape(title) }) Tj",
        "ET",
    ]
    y = 730
    for line in lines:
        content_lines.extend([
            "BT",
            "/F1 10 Tf",
            f"72 {y} Td",
            f"({_pdf_escape(line)}) Tj",
            "ET",
        ])
        y -= 18
    stream = "\n".join(content_lines).encode("utf-8")
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream",
    ]
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{idx} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("latin-1")
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    return pdf


def generate_bsa_certificate(
    evidence_package: EvidencePackage,
    *,
    case_id: str | None = None,
    signatory: str | None = None,
    reviewer: str | None = None,
    timestamp_ref: str | None = None,
    certificate_ref: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    lines = [
        f"Case ID: {case_id or evidence_package.evidence_id}",
        f"Evidence Package ID: {evidence_package.package_id}",
        f"Evidence ID: {evidence_package.evidence_id}",
        f"Merkle Root: {evidence_package.merkle_root}",
        f"Artifact Count: {len(evidence_package.artifacts)}",
        f"Ruleset Digest: {evidence_package.ruleset_digest or 'not available'}",
        f"Model Digests: {', '.join(f'{name}={digest}' for name, digest in sorted(evidence_package.model_digests.items())) or 'not available'}",
        f"Timestamp Ref: {timestamp_ref or 'not available'}",
        f"Certificate Ref: {certificate_ref or 'not available'}",
        f"Reviewer: {reviewer or 'not provided'}",
        f"Signatory: {signatory or 'not provided'}",
        f"Generated: {generated_at}",
        "This document is a generated evidence certificate for traceability and review.",
        "It is not a government-issued certificate and does not guarantee legal admissibility.",
    ]
    for artifact in evidence_package.artifacts:
        lines.append(f"- {artifact.artifact_id}: {artifact.artifact_type} sha256={artifact.sha256}")
    pdf_bytes = _manual_pdf_document(lines)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    return {
        "certificate_id": f"cert-{digest[:16]}",
        "certificate_ref": certificate_ref or f"cert-{digest[:16]}",
        "evidence_package_id": evidence_package.package_id,
        "merkle_root": evidence_package.merkle_root,
        "pdf_sha256": digest,
        "generated_at": generated_at,
        "pdf_bytes": pdf_bytes,
        "verification_instructions": "Recompute the certificate PDF hash and compare against the certificate document hash stored in the evidence record.",
    }
