"""Offline GTIN validation and supporting-only GS1 provider comparison."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Protocol


class GS1Provider(Protocol):
    """Provider adapters own transport, credentials, timeouts, and bounded retries."""

    provider_name: str

    def availability(self) -> dict[str, Any]: ...

    def verify_gtin(self, gtin: str) -> dict[str, Any]: ...


class RetryingGS1Provider:
    """Bound retries to transient transport failures; auth and response errors are never retried."""

    def __init__(self, provider: GS1Provider, max_retries: int = 2, backoff_seconds: float = 0.05):
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 5:
            raise ValueError("GS1 max_retries must be between 0 and 5")
        if not 0 <= backoff_seconds <= 1:
            raise ValueError("GS1 retry backoff must be between 0 and 1 second")
        self._provider = provider
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.provider_name = provider.provider_name
        self.provider_kind = getattr(provider, "provider_kind", "registry_provider")

    def availability(self) -> dict[str, Any]:
        return self._provider.availability()

    def verify_gtin(self, gtin: str) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                return self._provider.verify_gtin(gtin)
            except (TimeoutError, ConnectionError):
                if attempt >= self.max_retries:
                    raise
                if self.backoff_seconds:
                    time.sleep(self.backoff_seconds * (attempt + 1))
        raise RuntimeError("GS1 retry loop ended unexpectedly")


_GTIN_LENGTHS = {8, 12, 13, 14}
_SYMBOL_LENGTHS = {"EAN_8": 8, "EAN_13": 13, "UPC_A": 12, "UPC_E": 8, "ITF_14": 14}
_FIELDS = ("brand", "description", "net_content")


def expected_check_digit(body: str) -> str:
    if not isinstance(body, str) or not body.isascii() or not body.isdigit() or len(body) not in {7, 11, 12, 13}:
        raise ValueError("GTIN body must contain 7, 11, 12, or 13 digits")
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
    return str((-total) % 10)


def validate_gtin(gtin: str) -> dict[str, Any]:
    if not isinstance(gtin, str) or not gtin.isascii() or len(gtin) not in _GTIN_LENGTHS or not gtin.isdigit():
        return {
            "gtin": gtin if isinstance(gtin, str) else None,
            "length": len(gtin) if isinstance(gtin, str) else 0,
            "check_digit": gtin[-1] if isinstance(gtin, str) and gtin.isdigit() and gtin else None,
            "expected_check_digit": None,
            "valid": False,
            "status": "INVALID_GTIN",
            "error": "GTIN must contain exactly 8, 12, 13, or 14 numeric digits.",
        }
    expected = expected_check_digit(gtin[:-1])
    valid = gtin[-1] == expected
    return {
        "gtin": gtin,
        "length": len(gtin),
        "check_digit": gtin[-1],
        "expected_check_digit": expected,
        "valid": valid,
        "status": "VALID_GTIN" if valid else "INVALID_GTIN",
        "error": None if valid else "GTIN check digit does not match the calculated value.",
    }


def _symbology(value: str | None) -> str | None:
    if value is None:
        return None
    key = re.sub(r"[\s-]+", "_", str(value).strip().upper())
    return {"EAN8": "EAN_8", "EAN13": "EAN_13", "UPCA": "UPC_A", "UPCE": "UPC_E", "ITF": "ITF_14", "ITF14": "ITF_14"}.get(key, key)


def _expand_upc_e(value: str) -> str | None:
    if len(value) != 8 or not value.isascii() or not value.isdigit() or value[0] not in "01":
        return None
    number_system, data, check = value[0], value[1:7], value[7]
    last = data[5]
    if last in "012":
        body = number_system + data[:2] + last + "0000" + data[2:5]
    elif last == "3":
        body = number_system + data[:3] + "00000" + data[3:5]
    elif last == "4":
        body = number_system + data[:4] + "00000" + data[4]
    else:
        body = number_system + data[:5] + "0000" + last
    expanded = body + check
    return expanded if validate_gtin(expanded)["valid"] else None


def normalize_gtin(raw_value: str, symbology: str | None = None) -> dict[str, Any]:
    symbol = _symbology(symbology)
    result: dict[str, Any] = {
        "raw_value": raw_value if isinstance(raw_value, str) else None,
        "gtin": None,
        "length": 0,
        "check_digit": None,
        "expected_check_digit": None,
        "valid": False,
        "status": "INVALID_GTIN",
        "error": None,
        "symbology": symbol,
    }
    if not isinstance(raw_value, str) or not raw_value.strip():
        result["error"] = "Barcode identifier is empty."
        return result
    digits = re.sub(r"[\s-]", "", raw_value)
    if not digits.isascii() or not digits.isdigit():
        result.update(status="MALFORMED_IDENTIFIER", error="Only digits, spaces, and hyphens are accepted in a GTIN.")
        return result
    if symbol is not None and symbol not in _SYMBOL_LENGTHS:
        result.update(status="UNSUPPORTED_IDENTIFIER", length=len(digits), error=f"Symbology {symbol} is not a supported GTIN carrier.")
        return result
    if symbol == "UPC_E":
        expanded = _expand_upc_e(digits)
        if expanded is None:
            result.update(length=len(digits), status="INVALID_GTIN", error="UPC-E value is malformed or has an invalid check digit.")
            return result
        digits = expanded
    elif symbol and len(digits) != _SYMBOL_LENGTHS[symbol]:
        result.update(length=len(digits), status="INVALID_GTIN", error="Identifier length does not match its barcode symbology.")
        return result
    if len(digits) not in _GTIN_LENGTHS:
        result.update(length=len(digits), status="UNSUPPORTED_IDENTIFIER", error="Only GTIN-8, GTIN-12, GTIN-13, and GTIN-14 are supported.")
        return result
    result.update(validate_gtin(digits))
    result["raw_value"] = raw_value
    result["symbology"] = symbol
    return result


def _field_value(field: Any) -> Any:
    return field.get("value") if isinstance(field, dict) else field


def _ocr_identity(extraction: dict[str, Any] | None) -> dict[str, Any]:
    extraction = extraction if isinstance(extraction, dict) else {}
    product = extraction.get("product") or {}
    return {
        "brand": _field_value(product.get("brand")),
        "description": _field_value(product.get("name")),
        "net_content": _field_value(extraction.get("quantity")),
    }


def _ocr_region_ids(extraction: dict[str, Any] | None, field: str) -> list[str]:
    extraction = extraction if isinstance(extraction, dict) else {}
    if field == "brand":
        source = (extraction.get("product") or {}).get("brand") or {}
    elif field == "description":
        source = (extraction.get("product") or {}).get("name") or {}
    else:
        source = extraction.get("quantity") or {}
    return source.get("evidence_region_ids", []) if isinstance(source, dict) else []


def _comparison_text(value: Any, field: str) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))
    if field == "net_content":
        normalized = re.sub(r"(?<=\d)\s+(?=[a-z])", "", normalized)
    return normalized or None


class GS1VerificationService:
    def __init__(self, provider: GS1Provider | None = None, *, max_retries: int = 2):
        configured_retries = getattr(provider, "max_retries", max_retries)
        self.provider = RetryingGS1Provider(provider, max_retries=configured_retries) if provider is not None and not isinstance(provider, RetryingGS1Provider) else provider

    def availability(self) -> dict[str, Any]:
        if self.provider is None:
            return {"status": "NOT_CONFIGURED", "provider": None, "available": False, "local_validation_available": True}
        try:
            response = self.provider.availability()
        except Exception:
            response = {}
        return {
            "status": response.get("status", "PROVIDER_UNAVAILABLE") if isinstance(response, dict) else "PROVIDER_UNAVAILABLE",
            "provider": getattr(self.provider, "provider_name", None),
            "available": isinstance(response, dict) and response.get("available") is True,
            "local_validation_available": True,
        }

    def _verify_external(self, gtin: str, cache: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, Any]]:
        if gtin in cache:
            return cache[gtin]
        name = getattr(self.provider, "provider_name", None)
        try:
            response = self.provider.verify_gtin(gtin)  # type: ignore[union-attr]
        except PermissionError:
            external, identity = {"status": "AUTHENTICATION_FAILURE", "provider": name, "verified": False, "reason": "Provider authentication failed."}, {}
        except TimeoutError:
            external, identity = {"status": "TIMEOUT", "provider": name, "verified": False, "reason": "Provider verification timed out."}, {}
        except Exception:
            external, identity = {"status": "PROVIDER_UNAVAILABLE", "provider": name, "verified": False, "reason": "Provider verification failed."}, {}
        else:
            if getattr(self.provider, "provider_kind", None) == "digital_link_resolver":
                allowed_statuses = {"RESOLVED", "NOT_FOUND", "INVALID_DIGITAL_LINK", "MALFORMED_RESPONSE", "HTTP_ERROR", "UNAVAILABLE", "TIMEOUT", "RESPONSE_TOO_LARGE", "AUTHENTICATION_FAILURE"}
                status = response.get("status") if isinstance(response, dict) else None
                resources = response.get("resources", []) if isinstance(response, dict) else None
                valid_resources = isinstance(resources, list) and len(resources) <= 100 and all(
                    isinstance(item, dict) and isinstance(item.get("href"), str) for item in resources
                )
                if not isinstance(response, dict) or status not in allowed_statuses or not valid_resources:
                    external = {"status": "MALFORMED_RESPONSE", "provider": name, "verified": False,
                                "reason": "Resolver adapter returned an invalid status or resource list."}
                else:
                    external = {
                        key: response.get(key)
                        for key in ("status", "provider", "provider_kind", "source", "environment", "queried_gtin14", "queried_at", "fixture_status", "reason", "resources")
                        if response.get(key) is not None
                    }
                    external["provider"] = name
                    external["verified"] = False
                    external["reason"] = response.get("reason")
                identity = {}
            else:
                values = response.get("product_identity") if isinstance(response, dict) else None
                if not isinstance(response, dict) or response.get("verified") is not True or not isinstance(values, dict):
                    external, identity = {"status": "MALFORMED_RESPONSE", "provider": name, "verified": False, "reason": "Provider returned no verifiable product identity."}, {}
                elif any(values.get(field) is not None and not isinstance(values.get(field), str) for field in _FIELDS):
                    external, identity = {"status": "MALFORMED_RESPONSE", "provider": name, "verified": False, "reason": "Provider identity fields were malformed."}, {}
                else:
                    external = {
                        "status": "VERIFIED", "provider": name, "verified": True, "reason": None,
                        "verified_at": response.get("verified_at") if isinstance(response.get("verified_at"), str) else None,
                    }
                    identity = {field: values.get(field) for field in _FIELDS}
        cache[gtin] = external, identity
        return external, identity

    def verify(
        self,
        barcode_decoding: dict[str, Any] | None,
        *,
        structured_extraction: dict[str, Any] | None = None,
        scan_id: str | None = None,
        image_id: str | None = None,
        report_id: str | None = None,
    ) -> dict[str, Any]:
        decoding = barcode_decoding if isinstance(barcode_decoding, dict) else {}
        detections = decoding.get("detections") if isinstance(decoding.get("detections"), list) else []
        ocr_values = _ocr_identity(structured_extraction)
        cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        has_valid_gtin = any(
            isinstance(detection, dict)
            and normalize_gtin(detection.get("raw_value", detection.get("code", "")), detection.get("symbology"))["valid"]
            for detection in detections
        )
        if self.provider is None:
            provider_availability = self.availability()
        elif has_valid_gtin:
            provider_availability = self.availability()
        else:
            provider_availability = {
                "status": "NOT_QUERIED",
                "provider": getattr(self.provider, "provider_name", None),
                "available": False,
                "local_validation_available": True,
            }
        barcodes = []
        for index, detection in enumerate(detections):
            if not isinstance(detection, dict):
                continue
            raw_value = detection.get("raw_value", detection.get("code", ""))
            normalized = normalize_gtin(raw_value, detection.get("symbology"))
            barcode_id = str(detection.get("barcode_id") or f"BC-{uuid.uuid4().hex[:12].upper()}")
            evidence_id = ":".join(value for value in (scan_id, report_id, barcode_id, "gs1") if value)
            external = {"status": "NOT_ATTEMPTED", "provider": None, "verified": False,
                        "reason": "External lookup requires a supported GTIN."}
            identity = {field: None for field in _FIELDS}
            if normalized["valid"] and self.provider is not None:
                if provider_availability["available"]:
                    external, identity = self._verify_external(normalized["gtin"], cache)
                else:
                    external = {"status": provider_availability["status"], "provider": provider_availability["provider"],
                                "verified": False, "reason": "Provider is not available; local validation remains available."}
            elif normalized["valid"]:
                external = {"status": "NOT_CONFIGURED", "provider": None, "verified": False,
                            "reason": "No approved GS1 provider is configured; no external product record was queried."}
            comparisons = []
            if normalized["valid"]:
                for field in _FIELDS:
                    ocr_value, provider_value = ocr_values.get(field), identity.get(field)
                    left, right = _comparison_text(ocr_value, field), _comparison_text(provider_value, field)
                    if not external["verified"] or right is None or left is None:
                        state, explanation = "NOT_AVAILABLE", "A verified value is unavailable for comparison."
                    elif left == right:
                        state, explanation = "CONSISTENT", "OCR and provider values match after exact case, whitespace, and punctuation normalization."
                    else:
                        state, explanation = "MISMATCH", "OCR and provider values differ; this supplementary evidence requires human review."
                    comparisons.append({
                        "field": field, "ocr_value": ocr_value, "gs1_value": provider_value,
                        "state": state, "explanation": explanation,
                        "evidence_reference": {"evidence_id": evidence_id, "ocr_region_ids": _ocr_region_ids(structured_extraction, field)},
                    })
            barcodes.append({
                "barcode_id": barcode_id,
                "barcode_type": detection.get("symbology"),
                "raw_value": raw_value,
                "gtin": normalized["gtin"],
                "gtin_length": normalized["length"],
                "check_digit": normalized["check_digit"],
                "expected_check_digit": normalized["expected_check_digit"],
                "check_digit_valid": normalized["valid"],
                "validation_state": normalized["status"],
                "validation_error": normalized["error"],
                "bbox": detection.get("bbox"),
                "polygon": detection.get("polygon"),
                "confidence": detection.get("confidence"),
                "decoder": detection.get("source") or decoding.get("decoder"),
                "source_image_id": image_id,
                "source_image_sha256": detection.get("source_image_sha256") or decoding.get("source_image_sha256"),
                "external_verification": external,
                "product_identity": identity,
                "ocr_comparison": comparisons,
                "evidence": {"evidence_id": evidence_id, "source_image_id": image_id, "report_id": report_id,
                             "bbox": detection.get("bbox"), "crop_url": None, "crop_sha256": None},
            })
        mismatched = any(item["state"] == "MISMATCH" for barcode in barcodes for item in barcode["ocr_comparison"])
        if mismatched:
            status = "REVIEW_REQUIRED"
        elif any(barcode["check_digit_valid"] for barcode in barcodes):
            status = "EXTERNAL_VERIFIED_SUPPORTING" if any(barcode["external_verification"]["verified"] for barcode in barcodes) else "LOCAL_VALIDATION_ONLY"
        elif barcodes:
            status = "IDENTIFIER_REVIEW_REQUIRED"
        elif decoding.get("status") == "unavailable":
            status = "DECODER_UNAVAILABLE"
        else:
            status = "NO_BARCODE_DETECTED"
        return {
            "verification_id": f"GS1-{uuid.uuid4().hex[:16].upper()}",
            "scan_id": scan_id, "image_id": image_id, "report_id": report_id,
            "status": status, "provider_state": provider_availability, "barcodes": barcodes,
            "authoritative": False, "used_for_compliance": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "Barcode and GS1 data are supporting product-identity evidence only. Local check-digit validation does not verify a product against a GS1 registry or determine legal compliance.",
        }


def crop_barcode_image(source_image: bytes, bbox: list[float] | None) -> bytes | None:
    if not isinstance(source_image, (bytes, bytearray)) or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        from PIL import Image

        with Image.open(BytesIO(source_image)) as image:
            image.load()
            width, height = image.size
            x1, y1, x2, y2 = (float(value) for value in bbox)
            if not all(value == value and abs(value) != float("inf") for value in (x1, y1, x2, y2)):
                return None
            if x2 <= x1 or y2 <= y1:
                return None
            pad_x, pad_y = max(2, int((x2 - x1) * 0.04)), max(2, int((y2 - y1) * 0.12))
            bounds = (max(0, int(x1) - pad_x), max(0, int(y1) - pad_y), min(width, int(x2 + pad_x)), min(height, int(y2 + pad_y)))
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                return None
            output = BytesIO()
            image.crop(bounds).convert("RGB").save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception:
        return None