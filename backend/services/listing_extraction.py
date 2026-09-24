"""
Conservative listing-page data extraction for Phase 2A.

Scope: turn one already-retrieved HTML page (backend/services/
listing_retrieval.py's RetrievalOutcome) into structured ListingData
(backend/models/listing.py), using ONLY explicitly-available signals:

  1. JSON-LD structured data (schema.org Product) - the most reliable
     source, preferred whenever present.
  2. Open Graph / product meta tags - a secondary, still-structured
     fallback.
  3. Conservative, label-ANCHORED text patterns (e.g. a line that starts
     with "Country of Origin:") - never a bare keyword search, and never
     free-text inference. A value is only ever set when an explicit label
     is matched; anything else is left unavailable and recorded as such.

Every extracted value carries a ListingFieldEvidence entry naming its
exact source, so nothing is ever presented without a traceable origin.
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any, Optional

from backend.models.listing import ListingData, ListingFieldEvidence
from backend.services.listing_retrieval import RetrievalOutcome

logger = logging.getLogger("legallense.services.listing_extraction")

_MAX_CONTEXT_CHARS = 160
_MIN_MEANINGFUL_TEXT_CHARS = 40


def _truncate(text: str, limit: int = _MAX_CONTEXT_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class _ListingHTMLParser(HTMLParser):
    """Extracts <title>, meta tags, JSON-LD <script> bodies, and visible
    text nodes (script/style excluded) - a plain stdlib tokenizer, never
    an XML/DOM parser, so it never resolves external entities and never
    executes anything on this untrusted page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: Optional[str] = None
        self.meta: dict[str, str] = {}
        self.json_ld_raw: list[str] = []
        self.text_lines: list[str] = []
        self._in_title = False
        self._in_script = False
        self._in_style = False
        self._capture_json_ld = False
        self._json_ld_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "script":
            self._in_script = True
            self._capture_json_ld = attrs_dict.get("type", "").strip().lower() == "application/ld+json"
            self._json_ld_buffer = []
        elif tag == "style":
            self._in_style = True
        elif tag == "meta":
            key = (attrs_dict.get("property") or attrs_dict.get("name") or "").strip().lower()
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            if self._capture_json_ld and self._json_ld_buffer:
                self.json_ld_raw.append("".join(self._json_ld_buffer))
            self._in_script = False
            self._capture_json_ld = False
            self._json_ld_buffer = []
        elif tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data
        elif self._capture_json_ld:
            self._json_ld_buffer.append(data)
        elif self._in_script or self._in_style:
            return
        else:
            stripped = data.strip()
            if stripped:
                self.text_lines.append(stripped)


def _iter_json_ld_nodes(data: Any) -> list[dict]:
    nodes: list[dict] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            nodes.append(node)
            graph = node.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(data)
    return nodes


def _is_product_node(node: dict) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return any(isinstance(t, str) and "product" in t.lower() for t in node_type)
    return isinstance(node_type, str) and "product" in node_type.lower()


def _as_name(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if isinstance(value, list) and value:
        return _as_name(value[0])
    return None


# Label vocabulary for schema.org Product.additionalProperty (PropertyValue)
# entries - retailers commonly encode India-specific declarations this way
# since schema.org Product has no first-class property for most of them.
_ADDITIONAL_PROPERTY_FIELD_MAP: dict[str, str] = {
    "country of origin": "country_of_origin",
    "made in": "country_of_origin",
    "net quantity": "net_quantity",
    "net weight": "net_quantity",
    "net wt": "net_quantity",
    "manufacturer": "manufacturer",
    "manufactured by": "manufacturer",
    "packed by": "packer_importer",
    "imported by": "packer_importer",
    "importer": "packer_importer",
    "consumer care": "consumer_care",
    "customer care": "consumer_care",
    "mrp": "mrp",
    "maximum retail price": "mrp",
}

_LABEL_PATTERNS: dict[str, re.Pattern] = {
    "country_of_origin": re.compile(r"^\s*(?:country\s*of\s*origin|made\s*in|product\s*of)\s*[:\-]\s*(.{2,80})$", re.I),
    "net_quantity": re.compile(r"^\s*net\s*(?:quantity|weight|qty|wt\.?)\s*[:\-]\s*(.{1,60})$", re.I),
    "manufacturer": re.compile(r"^\s*(?:manufactured\s*by|manufacturer|marketed\s*by)\s*[:\-]\s*(.{2,200})$", re.I),
    "packer_importer": re.compile(r"^\s*(?:packed\s*by|imported\s*by|importer)\s*[:\-]\s*(.{2,200})$", re.I),
    "consumer_care": re.compile(r"^\s*(?:consumer\s*care|customer\s*care)\s*[:\-]\s*(.{2,200})$", re.I),
    "mrp": re.compile(r"^\s*(?:m\.?\s*r\.?\s*p\.?|maximum\s*retail\s*price)\s*[:\-]?\s*(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I),
}

_QUANTITY_SHAPE = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(?:kgs?|kilograms?|gms?|grams?|g|mls?|millilitres?|milliliters?|litres?|liters?|l)\b",
    re.I,
)


def _extract_from_json_ld(raw_blocks: list[str], listing: ListingData) -> None:
    product_nodes: list[dict] = []
    for raw in raw_blocks:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            listing.extraction_warnings.append("A JSON-LD <script> block was present but could not be parsed as valid JSON; ignored.")
            continue
        for node in _iter_json_ld_nodes(parsed):
            if _is_product_node(node):
                product_nodes.append(node)

    if not product_nodes:
        return
    if len(product_nodes) > 1:
        listing.extraction_warnings.append(
            f"Found {len(product_nodes)} Product entries in structured data; using the first one only."
        )
    node = product_nodes[0]

    def set_field(field: str, value: Optional[str], source_context: str) -> None:
        if value and getattr(listing, field) is None:
            setattr(listing, field, value)
            listing.evidence.append(ListingFieldEvidence(
                field=field, extracted_value=value, extraction_source="json-ld",
                source_context=_truncate(source_context),
            ))

    set_field("product_name", _as_name(node.get("name")), f"@type Product name: {node.get('name')!r}")
    set_field("brand", _as_name(node.get("brand")), f"Product brand: {node.get('brand')!r}")
    set_field("manufacturer", _as_name(node.get("manufacturer")), f"Product manufacturer: {node.get('manufacturer')!r}")
    set_field("country_of_origin", _as_name(node.get("countryOfOrigin")), f"Product countryOfOrigin: {node.get('countryOfOrigin')!r}")

    offers = node.get("offers")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        price = offers.get("price") or offers.get("lowPrice")
        currency = offers.get("priceCurrency") or ""
        if price is not None:
            set_field("price", f"{currency} {price}".strip(), f"offers.price: {offers.get('price')!r}")

    additional_properties = node.get("additionalProperty")
    if isinstance(additional_properties, dict):
        additional_properties = [additional_properties]
    if isinstance(additional_properties, list):
        for entry in additional_properties:
            if not isinstance(entry, dict):
                continue
            prop_name = str(entry.get("name") or "").strip().lower()
            prop_value = entry.get("value")
            mapped_field = _ADDITIONAL_PROPERTY_FIELD_MAP.get(prop_name)
            if mapped_field and isinstance(prop_value, (str, int, float)) and str(prop_value).strip():
                set_field(mapped_field, str(prop_value).strip(), f"additionalProperty {entry.get('name')!r}: {prop_value!r}")


def _extract_from_meta(meta: dict[str, str], listing: ListingData) -> None:
    def set_field(field: str, key: str) -> None:
        value = meta.get(key)
        if value and value.strip() and getattr(listing, field) is None:
            cleaned = value.strip()
            setattr(listing, field, cleaned)
            listing.evidence.append(ListingFieldEvidence(
                field=field, extracted_value=cleaned, extraction_source="meta-tag",
                source_context=f"<meta property/name=\"{key}\" content=\"{_truncate(cleaned, 80)}\">",
            ))

    set_field("product_name", "og:title")
    set_field("brand", "og:brand")
    set_field("brand", "product:brand")
    if listing.price is None:
        for key in ("product:price:amount", "og:price:amount"):
            value = meta.get(key)
            if value and value.strip():
                currency = meta.get("product:price:currency") or meta.get("og:price:currency") or ""
                cleaned = f"{currency} {value.strip()}".strip()
                listing.price = cleaned
                listing.evidence.append(ListingFieldEvidence(
                    field="price", extracted_value=cleaned, extraction_source="meta-tag",
                    source_context=f"<meta property=\"{key}\" content=\"{value.strip()}\">",
                ))
                break


def _extract_from_text(lines: list[str], listing: ListingData) -> None:
    for raw_line in lines:
        for field, pattern in _LABEL_PATTERNS.items():
            if getattr(listing, field) is not None:
                continue
            match = pattern.match(raw_line)
            if not match:
                continue
            value = match.group(1).strip().rstrip(".,;")
            if not value:
                continue
            if field == "net_quantity" and not _QUANTITY_SHAPE.search(value):
                listing.extraction_warnings.append(
                    f"A 'Net Quantity' label was found on the listing page but its value "
                    f"('{_truncate(value, 40)}') could not be confidently parsed as a quantity; left unavailable."
                )
                continue
            setattr(listing, field, value)
            listing.evidence.append(ListingFieldEvidence(
                field=field, extracted_value=value, extraction_source="text-label",
                source_context=_truncate(raw_line),
            ))


_LISTING_FIELDS = (
    "product_name", "brand", "price", "mrp", "net_quantity",
    "country_of_origin", "manufacturer", "packer_importer", "consumer_care",
)


def extract_listing_data(source_url: str, outcome: RetrievalOutcome) -> ListingData:
    """Build ListingData from a RetrievalOutcome. Never raises - any
    internal parsing failure degrades to an honest UNKNOWN_ERROR/
    extraction_warnings entry rather than propagating."""
    if outcome.status != "SUCCESS":
        return ListingData(
            source_url=source_url, final_url=outcome.final_url,
            retrieval_status=outcome.status, error_message=outcome.error_message,
        )

    html = outcome.html or ""
    parser = _ListingHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        logger.exception("HTML parsing failed for listing URL %s", source_url)
        return ListingData(
            source_url=source_url, final_url=outcome.final_url, retrieval_status="UNKNOWN_ERROR",
            error_message=f"HTML parsing failed: {exc}",
        )

    page_title = (parser.title or "").strip() or None
    has_json_ld = bool(parser.json_ld_raw)
    has_meta_signal = bool(parser.meta.get("og:title") or parser.meta.get("product:price:amount") or parser.meta.get("og:price:amount"))
    visible_text_len = sum(len(line) for line in parser.text_lines)

    if not has_json_ld and not has_meta_signal and visible_text_len < _MIN_MEANINGFUL_TEXT_CHARS:
        return ListingData(
            source_url=source_url, final_url=outcome.final_url, retrieval_status="LISTING_UNAVAILABLE",
            page_title=page_title,
            error_message=(
                "The page was retrieved successfully but returned no extractable text or "
                "structured product data - likely a JavaScript-rendered listing page that "
                "this read-only retriever cannot execute."
            ),
        )

    listing = ListingData(source_url=source_url, final_url=outcome.final_url, retrieval_status="SUCCESS", page_title=page_title)

    _extract_from_json_ld(parser.json_ld_raw, listing)
    _extract_from_meta(parser.meta, listing)
    _extract_from_text(parser.text_lines, listing)

    if listing.product_name is None and page_title:
        listing.product_name = page_title
        listing.evidence.append(ListingFieldEvidence(
            field="product_name", extracted_value=page_title, extraction_source="page-title",
            source_context=_truncate(page_title),
        ))
        listing.extraction_warnings.append(
            "product_name was derived from the page <title> tag (no structured product name was "
            "found); this is not confirmed to be the exact product name."
        )

    listing.unavailable_fields = [field for field in _LISTING_FIELDS if getattr(listing, field) is None]
    return listing
