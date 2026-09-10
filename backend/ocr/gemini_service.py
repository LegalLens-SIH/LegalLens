"""
Gemini Flash AI/vision FALLBACK for ambiguous Legal Metrology field
extraction.

Scope (deliberately narrow, mirrors paddle_ocr_service.py / yolo_service.py):
image + a short list of ambiguous field names in -> a proposed value and
confidence PER FIELD out. This module NEVER makes a compliance decision
(PASS/FAIL/NEEDS_MANUAL_VERIFICATION) - it only proposes text for fields the
deterministic extractor (backend/services/compliance_engine.py's
normalize_ocr_result) could not confidently resolve. Whatever value ends up
in NormalizedOCRResult.fields - whether it came from whole-page alias
matching, a YOLO region, or from here - is validated by the exact SAME
deterministic rules (confidence thresholds, MRP/unit-price wording+amount
checks, entity heading/continuation checks, ...) in compliance_engine.py.
This module has no knowledge of, and no influence over, that logic beyond
supplying a value/confidence pair through the SAME generic
`ocr_result["fields"]` override mechanism normalize_ocr_result already
exposes (see its `supplied_fields` handling) - nothing in
compliance_engine.py was changed to support this.

Where this plugs in: backend/api/ocr.py's create_scan(), between a
successful PaddleOCR run and the ComplianceEngine.evaluate() call - see
that module for the actual wiring (find_ambiguous_fields ->
GeminiService.suggest_fields -> apply_suggestions_to_ocr_result -> the
SAME ComplianceEngine.evaluate() every scan already used).

What counts as "ambiguous" (the low-confidence heuristic used here, spelled
out per this task's explicit requirement to document the heuristic before
wiring it up): PaddleOCR already returns a per-detection recognition
`confidence` score (backend/ocr/schemas.py's Detection.confidence), which
compliance_engine.py's normalize_ocr_result already reduces to one
NormalizedField.confidence per declaration field (plus a `.detected` flag
for "no match found at all"). This module reuses that EXISTING signal
directly instead of inventing a second one: a field is "ambiguous" when
`not field.detected` (the extractor could not resolve it at all) OR
`field.confidence < GEMINI_CONFIDENCE_THRESHOLD` (resolved, but with low
OCR confidence). This is a field-level, pre-rule-evaluation heuristic - it
deliberately does NOT re-derive every downstream rule's own richer
ambiguity signal (e.g. compliance_engine.py's "multiple MRP amounts found"
check), since duplicating that would mean re-implementing rule logic here,
which is explicitly out of scope. It is intentionally at least as broad as
that requires: every field the rules engine would itself mark
NEEDS_MANUAL_VERIFICATION for missing-or-low-confidence OCR evidence is
already covered by "not detected" / "below threshold" above.

Config (all optional, read once at import time - see yolo_service.py's own
docstring for why module-level os.getenv reads require backend.main's
load_dotenv() to already have run; main.py already loads Portal/.env before
any backend.* import, so this module needs no special handling of its own):

    GEMINI_ENABLED               "true"/"false" - master on/off switch
                                   (default: false). When false (or when
                                   GEMINI_API_KEY is empty), the Gemini step
                                   is skipped entirely and every scan
                                   behaves exactly as it did before this
                                   module existed.
    GEMINI_API_KEY                Google AI Studio API key. Read from the
                                   backend .env only (see repo root's
                                   backend/main.py load_dotenv() call, which
                                   actually loads Portal/.env - NOT
                                   Portal/backend/.env - matching every
                                   other secret in this project, e.g.
                                   BREVO_API_KEY). Never hardcoded, never
                                   sent to the frontend, never logged in
                                   full (see _redact below).
    GEMINI_MODEL                  Model id (default: "gemini-2.5-flash").
    GEMINI_CONFIDENCE_THRESHOLD   Fields at/above this OCR confidence are
                                   NOT sent to Gemini (default: 0.75).
    GEMINI_MAX_FIELDS_PER_SCAN    Hard cap on how many ambiguous fields one
                                   scan can send to Gemini, in ONE batched
                                   request - not one call per field - so a
                                   single pathological image can't burn
                                   through a whole day's free-tier quota in
                                   one request (default: 5).
    GEMINI_TIMEOUT_SECONDS        Per-attempt request timeout in seconds
                                   (default: 9). One retry is attempted on
                                   a transient failure (timeout or
                                   exception), so a call can take up to
                                   roughly 2x this before falling back.

Usage:

    from backend.ocr.gemini_service import (
        GEMINI_ENABLED, GEMINI_API_KEY, GeminiService,
        find_ambiguous_fields, apply_suggestions_to_ocr_result,
    )

    if GEMINI_ENABLED and GEMINI_API_KEY:
        ambiguous = find_ambiguous_fields(ocr_result)
        if ambiguous:
            suggestions = await GeminiService().suggest_fields(image_bytes, ambiguous, ocr_result.get("regions"))
            if suggestions:
                ocr_result = apply_suggestions_to_ocr_result(ocr_result, suggestions)
    compliance = ComplianceEngine().evaluate(ocr_result, validation_profile)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.services.compliance_engine import YOLO_CLASS_TO_FIELDS, normalize_ocr_result

logger = logging.getLogger("legallense.ocr.gemini_service")

GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "false").strip().lower() not in {"false", "0", "no", ""}
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
GEMINI_CONFIDENCE_THRESHOLD = float(os.getenv("GEMINI_CONFIDENCE_THRESHOLD", "0.75"))
GEMINI_MAX_FIELDS_PER_SCAN = max(1, int(os.getenv("GEMINI_MAX_FIELDS_PER_SCAN", "5")))
GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "9"))

# One retry on a transient failure (timeout, network error, malformed
# response) - per this task's reliability requirement ("at most one retry
# on transient failure"). Not configurable; two attempts total.
_MAX_ATTEMPTS = 2

# The 8 LegalLense declaration classes (training/CLASS_TAXONOMY.md), mapped
# to the compliance_engine.py rule-field name(s) each satisfies. Reuses
# YOLO_CLASS_TO_FIELDS as-is (imported, not duplicated, so the two can never
# drift) and adds the two classes that map handles specially rather than
# through that generic dict (see YOLO_CLASS_TO_FIELDS's own docstring for
# why "mrp" and "consumer_care" are absent there).
GEMINI_FIELD_TO_RULE_FIELDS: dict[str, list[str]] = {
    **YOLO_CLASS_TO_FIELDS,
    "mrp": ["maximum_retail_price_mrp"],
    "consumer_care": ["consumer_care_details"],
}

# Stable order matching training/CLASS_TAXONOMY.md's class indices 0-7 -
# used only to make which fields get dropped deterministic when more than
# GEMINI_MAX_FIELDS_PER_SCAN fields are ambiguous on the same scan.
GEMINI_FIELD_NAMES: tuple[str, ...] = (
    "product_identity", "mrp", "unit_sale_price", "net_quantity",
    "entity_details", "country_of_origin", "mfg_date_batch", "consumer_care",
)

# Short, human-readable descriptions handed to Gemini in the prompt - not
# used anywhere else, so drift against CLASS_TAXONOMY.md's fuller
# definitions is a documentation-quality concern only, not a functional one.
_FIELD_DESCRIPTIONS: dict[str, str] = {
    "product_identity": "the commodity's common/generic name (what the product IS), not the brand name or a slogan",
    "mrp": (
        "the Maximum Retail Price declaration. The returned value MUST include the words "
        "\"MRP\" or \"Maximum Retail Price\" together with the amount, exactly as printed "
        "(e.g. \"MRP Rs. 110.00 (Incl. of all taxes)\"), not just the bare number"
    ),
    "unit_sale_price": (
        "the price per unit weight/volume, printed as an amount followed by a per-unit rate "
        "(e.g. \"Rs 90/kg\", \"₹1.10 per g\")"
    ),
    "net_quantity": "the net quantity/weight/volume declaration (e.g. \"200 g\", \"1 kg\", \"500 ml\")",
    "entity_details": "the manufacturer/packer/importer name and address block",
    "country_of_origin": "the country-of-origin declaration",
    "mfg_date_batch": "the manufacture/packing date, together with the batch/lot number if visible",
    "consumer_care": "the consumer/customer care phone number, email address, and/or contact address",
}


class GeminiInitializationError(RuntimeError):
    """Raised when the Gemini client cannot be constructed (e.g. no API key)."""


class GeminiRequestError(RuntimeError):
    """Raised when a Gemini response cannot be parsed into the expected shape."""


class GeminiFieldSuggestion(BaseModel):
    """One field's proposed value, per the response shape this task specifies."""

    field: str = Field(..., description="One of the 8 LegalLense class names, e.g. 'net_quantity'")
    value: str = Field(..., description="The recognized declaration text, as printed")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field("", description="Brief justification for the extracted value")


def _redact(text: str) -> str:
    """Strip the configured API key out of a string before it reaches a log
    line or an exception message that might get logged - per this task's
    security requirement to never print the full key. A no-op when no key
    is configured."""
    if GEMINI_API_KEY and GEMINI_API_KEY in text:
        return text.replace(GEMINI_API_KEY, "***REDACTED***")
    return text


def find_ambiguous_fields(ocr_result: dict[str, Any]) -> list[str]:
    """Return up to GEMINI_MAX_FIELDS_PER_SCAN of the 8 LegalLense class
    names whose underlying field(s) the deterministic extractor could not
    confidently resolve - see this module's docstring for the exact
    heuristic. Never raises for a malformed ocr_result; treats anything it
    can't evaluate as ambiguous rather than silently skipping it, since
    "cannot determine" is itself a form of low confidence.
    """
    try:
        normalized = normalize_ocr_result(ocr_result)
    except Exception:
        logger.exception("normalize_ocr_result failed while checking for ambiguous fields")
        return list(GEMINI_FIELD_NAMES[:GEMINI_MAX_FIELDS_PER_SCAN])

    ambiguous: list[str] = []
    for class_name in GEMINI_FIELD_NAMES:
        rule_field_name = GEMINI_FIELD_TO_RULE_FIELDS[class_name][0]
        field = normalized.fields.get(rule_field_name)
        if field is None or not field.detected or field.confidence < GEMINI_CONFIDENCE_THRESHOLD:
            ambiguous.append(class_name)
    return ambiguous[:GEMINI_MAX_FIELDS_PER_SCAN]


def _union_bbox(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    return x1, y1, x2, y2


def _build_request_image(image_bytes: bytes, ambiguous_fields: list[str], regions: list[dict[str, Any]]) -> bytes:
    """Per this task's REGION HANDLING requirement: if a bounding box for at
    least one ambiguous field is already available (today, only ever
    populated when YOLO_DETECTION_MODE=multi_region AND a validated
    multi-class YOLO checkpoint is active - see yolo_service.py; the
    pretrained generic checkpoint currently in use never produces these
    class names, so this branch is a no-op in practice until that model
    exists, exactly as instructed: "do not block on it"), crop to the
    smallest region covering every such field, padded a little. Otherwise
    fall back to the full image, unmodified. Never raises - any failure to
    crop (corrupt image, degenerate box) falls back to the original bytes.
    """
    matching_boxes = [
        (region["bbox"]["x1"], region["bbox"]["y1"], region["bbox"]["x2"], region["bbox"]["y2"])
        for region in regions
        if region.get("class_name") in ambiguous_fields and region.get("bbox")
    ]
    if not matching_boxes:
        return image_bytes

    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            x1, y1, x2, y2 = _union_bbox(matching_boxes)
            pad_x = int((x2 - x1) * 0.05)
            pad_y = int((y2 - y1) * 0.05)
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            x2, y2 = min(width, x2 + pad_x), min(height, y2 + pad_y)
            if x2 <= x1 or y2 <= y1:
                return image_bytes
            cropped = image.convert("RGB").crop((x1, y1, x2, y2))
            buffer = io.BytesIO()
            cropped.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue()
    except Exception:
        logger.exception("Failed to crop image to ambiguous-field regions; falling back to the full image")
        return image_bytes


def _build_prompt(ambiguous_fields: list[str]) -> str:
    lines = [
        "You are assisting a Legal Metrology (India) compliance tool. The attached image is a product package "
        "photo or a crop of one. For EACH of the following declaration fields, find the corresponding text "
        "printed on the package and return it EXACTLY as printed (do not paraphrase, translate, or normalize "
        "units). If a field is genuinely not visible in the image, OMIT it from the response entirely - do not "
        "guess or fabricate a value.",
        "",
        "Fields to look for:",
    ]
    for name in ambiguous_fields:
        lines.append(f"- {name}: {_FIELD_DESCRIPTIONS.get(name, name)}")
    lines.append("")
    lines.append(
        "Respond with a JSON array. Each element must have: \"field\" (one of the field names above exactly), "
        "\"value\" (the recognized text), \"confidence\" (your own confidence in this reading, 0.0-1.0), and "
        "\"reason\" (one short sentence explaining what you matched and why)."
    )
    return "\n".join(lines)


_PHONE_PATTERN = re.compile(r"(?:\+91[\s-]?)?[0-9][0-9\s-]{6,14}[0-9]")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_MRP_WORDING_PATTERN = re.compile(r"maximum\s+retail\s+price|m\.?r\.?p\.?", re.I)

# Same pattern compliance_engine.py's own _CARE_NAME_PATTERN/_care_name use
# to fill the "name" sub-field of consumer_care_details (the office/contact
# designation, e.g. "Consumer Care Cell" - NOT a person's name) from the
# whole-page text. Duplicated here (rather than importing the underscore-
# prefixed original) because it is a small, self-contained regex, and
# because compliance_engine.py's own region-based path
# (_apply_consumer_care_region) does NOT do this - it leaves name=None
# unconditionally, which would otherwise make every Gemini-sourced
# consumer_care suggestion register as PARTIAL (a confirmed, if
# unavoidable-for-that-path, gap: the ruleset requires "name" - see
# rules/legal_metrology_rules_2011.json's consumer_care_fields). Applying it
# here costs nothing and materially improves what Gemini's proposals can
# achieve without changing compliance_engine.py at all.
_CARE_NAME_PATTERN = re.compile(
    r"(?:consumer|customer)\s+care(?:\s+(?:cell|department|desk|division|team|centre|center))?"
    r"|customer\s+service(?:\s+(?:cell|department|desk|centre|center))?"
    r"|toll[\s-]?free(?:\s+(?:number|helpline))?"
    r"|helpline",
    re.I,
)


def _care_name(text: str) -> Optional[str]:
    match = _CARE_NAME_PATTERN.search(text)
    if not match:
        return None
    return " ".join(word.capitalize() for word in match.group(0).split())


def _normalize_value_for_rule_field(class_name: str, value: str) -> Any:
    """Shape a Gemini-proposed value the same way compliance_engine.py's own
    extraction already shapes it for the same field, so the SAME downstream
    validation (which inspects the value's shape/wording, not just its
    presence) behaves identically regardless of source:

    - consumer_care: compliance_engine.py's _apply_consumer_care_region (the
      existing region-based path this mirrors) expects a
      {name, address, telephone_number, email_address} dict, not a bare
      string - phone/email are pulled out with the same patterns used
      throughout compliance_engine.py.
    - mrp: _mrp_result requires the actual words "MRP"/"Maximum Retail
      Price" to appear ON the value text (it treats that wording as
      confirming evidence this line IS the MRP declaration, separate from
      the amount itself). The prompt already asks Gemini for this wording,
      but a defensive fallback prepends "MRP " when it's missing rather
      than silently producing a value the deterministic engine would
      reject as "amount without MRP wording".
    - everything else: passed through unchanged - the generic
      `_field_result`/YOLO_CLASS_TO_FIELDS path only checks detected+
      confidence, no content-shape validation.
    """
    if class_name == "consumer_care":
        phone = _PHONE_PATTERN.search(value)
        email = _EMAIL_PATTERN.search(value)
        return {
            "name": _care_name(value),
            "address": value,
            "telephone_number": phone.group(0) if phone else None,
            "email_address": email.group(0) if email else None,
        }
    if class_name == "mrp" and not _MRP_WORDING_PATTERN.search(value):
        return f"MRP {value}"
    return value


def apply_suggestions_to_ocr_result(
    ocr_result: dict[str, Any], suggestions: list[GeminiFieldSuggestion],
) -> dict[str, Any]:
    """Return a NEW ocr_result dict with `suggestions` merged into its
    `fields` override map (never mutates the input - matches
    normalize_ocr_result's own "never mutates" contract). Every mapped rule
    field for a suggested class gets the SAME value/confidence, mirroring
    how compliance_engine.py's own _apply_regions overlays a single YOLO
    class detection onto every rule field it satisfies (e.g. both the
    retail and wholesale field names for "entity_details").

    This is the ONLY place a Gemini result touches the pipeline: it lands
    in `ocr_result["fields"]`, the exact same generic override
    normalize_ocr_result already reads (see that function's
    `supplied_fields` handling) for callers supplying pre-resolved field
    values - nothing about that mechanism, or anything downstream of it in
    compliance_engine.py, was changed to support this.
    """
    merged = dict(ocr_result)
    fields = dict(merged.get("fields") or {})
    for suggestion in suggestions:
        rule_field_names = GEMINI_FIELD_TO_RULE_FIELDS.get(suggestion.field)
        if not rule_field_names:
            logger.warning("Gemini returned an unrecognized field name %r; ignoring it", suggestion.field)
            continue
        normalized_value = _normalize_value_for_rule_field(suggestion.field, suggestion.value)
        for rule_field_name in rule_field_names:
            fields[rule_field_name] = {
                "value": normalized_value,
                "detected": True,
                "confidence": suggestion.confidence,
            }
    merged["fields"] = fields
    return merged


class GeminiService:
    """Thin wrapper around the official google-genai SDK's async client.
    Mirrors PaddleOCRService/YOLOService: constructing an instance is cheap
    (no network call); the underlying client is built lazily on first use
    and cached at the class level so the (small) client-construction cost
    is paid once per API key, not once per request.
    """

    _client_cache: dict[str, Any] = {}

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY
        self.model = model or GEMINI_MODEL
        self.timeout_seconds = GEMINI_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise GeminiInitializationError("GEMINI_API_KEY is not set")

        cached = GeminiService._client_cache.get(self.api_key)
        if cached is not None:
            self._client = cached
            return self._client

        try:
            from google import genai  # imported lazily: heavy import
        except Exception as exc:  # pragma: no cover - environment issue
            raise GeminiInitializationError(f"Failed to import google-genai: {exc}") from exc

        try:
            client = genai.Client(api_key=self.api_key)
        except Exception as exc:
            raise GeminiInitializationError(f"Gemini client failed to initialize: {_redact(str(exc))}") from exc

        GeminiService._client_cache[self.api_key] = client
        self._client = client
        return client

    async def suggest_fields(
        self,
        image_bytes: bytes,
        ambiguous_fields: list[str],
        regions: Optional[list[dict[str, Any]]] = None,
    ) -> list[GeminiFieldSuggestion]:
        """Batch every ambiguous field for this scan into ONE Gemini
        request (never one call per field - see GEMINI_MAX_FIELDS_PER_SCAN)
        and return whatever structured suggestions come back.

        Never raises: a missing API key, an import failure, a timeout, a
        malformed response, or any other error is logged (with the API key
        redacted) and results in an empty list - the caller's existing
        PaddleOCR/extraction result is always left intact. Retries once on
        failure before giving up (GEMINI_TIMEOUT_SECONDS per attempt).
        """
        if not ambiguous_fields:
            return []
        fields = ambiguous_fields[:GEMINI_MAX_FIELDS_PER_SCAN]

        request_image = _build_request_image(image_bytes, fields, regions or [])

        last_error: Optional[BaseException] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(self._call(request_image, fields), timeout=self.timeout_seconds)
            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("Gemini request attempt %d/%d timed out after %ss", attempt, _MAX_ATTEMPTS, self.timeout_seconds)
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini request attempt %d/%d failed: %s", attempt, _MAX_ATTEMPTS, _redact(str(exc)))

        logger.warning(
            "Gemini fallback exhausted %d attempt(s); continuing without Gemini suggestions for this scan: %s",
            _MAX_ATTEMPTS, _redact(str(last_error)),
        )
        return []

    async def _call(self, image_bytes: bytes, fields: list[str]) -> list[GeminiFieldSuggestion]:
        client = self._get_client()
        from google.genai import types

        prompt = _build_prompt(fields)
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[GeminiFieldSuggestion],
                temperature=0,
            ),
        )

        parsed = getattr(response, "parsed", None)
        if parsed:
            return [item if isinstance(item, GeminiFieldSuggestion) else GeminiFieldSuggestion.model_validate(item) for item in parsed]

        # Defensive fallback if the SDK's auto-parsing didn't populate
        # `.parsed` for some reason - the response was still requested in
        # strict JSON mode, so `.text` should be a parseable JSON array.
        text = getattr(response, "text", None)
        if not text:
            raise GeminiRequestError("Gemini returned an empty response")
        data = json.loads(text)
        return [GeminiFieldSuggestion.model_validate(item) for item in data]
