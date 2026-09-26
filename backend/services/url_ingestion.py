from __future__ import annotations

import json
import re
from typing import Any

from backend.database import get_database
from backend.services.audit import append_audit_event


_JSON_LD_RE = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)


def _normalize_jsonld_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("name", "@value", "value", "price", "priceCurrency"):
            if key in value:
                return value.get(key)
        if "brand" in value and isinstance(value["brand"], dict):
            return _normalize_jsonld_value(value["brand"])
        if "offers" in value and isinstance(value["offers"], dict):
            return _normalize_jsonld_value(value["offers"])
        return value
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_jsonld_value(item)
            if normalized is not None:
                return normalized
    return value


def extract_jsonld_metadata(html: str) -> dict[str, Any]:
    if not isinstance(html, str) or not html.strip():
        return {}

    matches = []
    for m in _JSON_LD_RE.findall(html):
        candidate = m.strip()
        if candidate:
            matches.append(candidate)

    if not matches:
        return {}

    for chunk in matches:
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                flat: dict[str, Any] = {}
                for key, value in data.items():
                    if key == "brand" and isinstance(value, dict):
                        flat[key] = _normalize_jsonld_value(value)
                    elif key == "offers" and isinstance(value, dict):
                        offers = value.copy()
                        price = offers.get("price")
                        if price is not None:
                            flat["price"] = _normalize_jsonld_value(price)
                        if offers.get("priceCurrency") is not None:
                            flat["priceCurrency"] = offers.get("priceCurrency")
                        flat["offers"] = offers
                    else:
                        flat[key] = _normalize_jsonld_value(value)
                if "price" not in flat and "offers" in flat and isinstance(flat["offers"], dict):
                    flat["price"] = _normalize_jsonld_value(flat["offers"].get("price"))
                try:
                    db = get_database()
                    append_audit_event(
                        db,
                        event_type="url_metadata_extract",
                        entity_type="listing_url",
                        entity_id="url_metadata_extract",
                        actor_role="system",
                        action="metadata_extracted",
                        details={"source_fields": sorted(flat.keys())},
                    )
                except Exception:
                    pass
                return flat
            if isinstance(data, list):
                for elem in data:
                    if isinstance(elem, dict):
                        return extract_jsonld_metadata(json.dumps(elem))
        except json.JSONDecodeError:
            continue
    return {}


def normalize_listing_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    items: dict[str, Any] = {}
    if not isinstance(metadata, dict):
        return items

    name = metadata.get("name") or metadata.get("itemName") or metadata.get("title")
    brand = metadata.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    offers = metadata.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
    else:
        price = metadata.get("price")

    items["product_name"] = str(name).strip() if isinstance(name, str) and name.strip() else ""
    items["brand"] = str(brand).strip() if isinstance(brand, str) and brand.strip() else ""
    items["price"] = str(price).strip() if isinstance(price, str) and price.strip() else ""
    return {k: v for k, v in items.items() if v}
