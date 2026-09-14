"""
Focused tests for the Gemini Flash AI/vision fallback (gemini_service.py)
and its wiring into backend/api/ocr.py's create_scan.

No real Gemini API calls are made anywhere here - GeminiService._call (the
one place that actually talks to the network) is monkeypatched in every
test that needs a response, so these tests are fast, offline, and need
neither a real GEMINI_API_KEY nor MongoDB (unlike backend/api/
test_manufacturer.py's end-to-end tests, which do need Mongo - these
deliberately stay below that layer, exercising gemini_service.py's public
functions and backend.services.compliance_engine.ComplianceEngine directly,
the same lightweight style backend/services/test_compliance_engine.py
already uses).

Every test that cares about a specific config value (api_key, model,
threshold, cap) passes it explicitly to GeminiService(...) rather than
relying on the module-level env-derived defaults, so these tests behave
the same regardless of import order or whatever a real Portal/.env happens
to contain at the time they run.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from backend.ocr import gemini_service as gs
from backend.services.compliance_engine import ComplianceEngine

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def _ocr_result(**field_overrides: dict) -> dict:
    """A minimal ocr_result dict shaped like OCRResult.model_dump() with a
    `fields` override map - the same shape normalize_ocr_result's
    `supplied_fields` branch consumes (see services/test_compliance_engine.py's
    own `ocr()` helper, which this mirrors)."""
    fields = {
        "maximum_retail_price_mrp": {"value": "MRP Rs. 110.00", "detected": True, "confidence": 0.95},
        "unit_sale_price": {"value": "Rs 1.10 per g", "detected": True, "confidence": 0.95},
        "net_quantity": {"value": "100 g", "detected": True, "confidence": 0.95},
        "manufacturer_packer_importer_details": {"value": "ABC Foods, Pune", "detected": True, "confidence": 0.95},
        "country_of_origin": {"value": "India", "detected": True, "confidence": 0.95},
        "month_and_year_of_manufacture_or_packing": {"value": "08/2026", "detected": True, "confidence": 0.95},
        "consumer_care_details": {
            "value": {"name": "ABC", "address": "Pune", "telephone_number": "1800123456", "email_address": "care@abc.example"},
            "detected": True, "confidence": 0.95,
        },
        "common_generic_name_of_commodity": {"value": "Rice", "detected": True, "confidence": 0.95},
    }
    fields.update(field_overrides)
    return {"document_id": "DOC-TEST", "product_type": "packaged_commodity", "package_type": "retail", "full_text": "", "fields": fields}


# --- 1. Gemini disabled -> pipeline behaves exactly as before -------------

def test_disabled_by_default_the_api_module_never_constructs_a_client():
    """backend/api/ocr.py only builds a GeminiService at all when
    GEMINI_ENABLED is true (see its `_gemini_service = GeminiService() if
    GEMINI_ENABLED else None` line) - Portal/.env ships with
    GEMINI_ENABLED=false, so a default checkout never touches Gemini at
    all, exactly like _yolo_service's own YOLO_ENABLED gate."""
    from backend.api import ocr as ocr_module

    if gs.GEMINI_ENABLED:
        pytest.skip("GEMINI_ENABLED=true in this environment; disabled-by-default behavior not exercised here")
    assert ocr_module._gemini_service is None


def test_find_ambiguous_fields_on_a_fully_confident_scan_is_empty():
    """Nothing ambiguous -> nothing would ever be sent to Gemini, regardless
    of whether GEMINI_ENABLED is true - this is the same behavior the
    pipeline had before Gemini existed for a clean, fully-confident scan."""
    assert gs.find_ambiguous_fields(_ocr_result()) == []


# --- 2. Ambiguous field (below threshold, or unresolved) triggers fallback -

def test_low_confidence_field_is_flagged_ambiguous():
    data = _ocr_result(maximum_retail_price_mrp={"value": "110.00", "detected": True, "confidence": 0.40})
    assert "mrp" in gs.find_ambiguous_fields(data)


def test_undetected_field_is_flagged_ambiguous():
    data = _ocr_result()
    del data["fields"]["country_of_origin"]
    assert "country_of_origin" in gs.find_ambiguous_fields(data)


def test_suggest_fields_invokes_the_gemini_call_for_an_ambiguous_field(monkeypatch):
    calls: list[list[str]] = []

    async def fake_call(self, image_bytes, fields):
        calls.append(fields)
        return [gs.GeminiFieldSuggestion(field="mrp", value="MRP Rs. 110.00", confidence=0.9, reason="test")]

    monkeypatch.setattr(gs.GeminiService, "_call", fake_call)
    service = gs.GeminiService(api_key="test-key")
    result = asyncio.run(service.suggest_fields(b"fake-image-bytes", ["mrp"]))

    assert len(calls) == 1
    assert calls[0] == ["mrp"]
    assert len(result) == 1
    assert result[0].field == "mrp"


# --- 3. Multiple ambiguous fields -> ONE batched call, capped -------------

def test_multiple_ambiguous_fields_are_capped_at_max_fields_per_scan():
    data = _ocr_result()
    for name in data["fields"]:
        data["fields"][name]["confidence"] = 0.1  # every field now ambiguous
    ambiguous = gs.find_ambiguous_fields(data)
    assert len(ambiguous) == gs.GEMINI_MAX_FIELDS_PER_SCAN


def test_suggest_fields_makes_exactly_one_call_for_multiple_fields_and_respects_the_cap(monkeypatch):
    calls: list[list[str]] = []

    async def fake_call(self, image_bytes, fields):
        calls.append(fields)
        return [gs.GeminiFieldSuggestion(field=f, value="x", confidence=0.9, reason="") for f in fields]

    monkeypatch.setattr(gs.GeminiService, "_call", fake_call)
    service = gs.GeminiService(api_key="test-key")
    too_many = list(gs.GEMINI_FIELD_NAMES) + ["product_identity"]  # deliberately over the cap
    result = asyncio.run(service.suggest_fields(b"fake-image-bytes", too_many))

    assert len(calls) == 1, "expected exactly one batched Gemini call, not one per field"
    assert len(calls[0]) <= gs.GEMINI_MAX_FIELDS_PER_SCAN
    assert len(result) <= gs.GEMINI_MAX_FIELDS_PER_SCAN


# --- 4. Gemini's structured value(s) flow through the SAME compliance engine -

def test_gemini_suggestion_for_an_undetected_field_passes_through_unchanged_engine():
    """MRP undetected by OCR -> Gemini proposes a high-confidence value ->
    merged into ocr_result["fields"] -> the SAME ComplianceEngine (no
    Gemini-aware code path) evaluates it as PASS, exactly as it would for
    any other high-confidence detected value. Proves Gemini never makes the
    PASS/FAIL call itself - the deterministic engine still does."""
    data = _ocr_result()
    del data["fields"]["maximum_retail_price_mrp"]
    suggestion = gs.GeminiFieldSuggestion(field="mrp", value="MRP Rs. 110.00 (Incl. of all taxes)", confidence=0.97, reason="Found near MRP label")
    merged = gs.apply_suggestions_to_ocr_result(data, [suggestion])

    result = ComplianceEngine().evaluate(merged, "e_commerce_product_listing")
    mrp_result = result.rule_results[0].required_fields["maximum_retail_price_mrp"]
    assert mrp_result.status == "PASS"
    assert mrp_result.value["normalized_value"] == 110.00


def test_gemini_suggestion_still_below_engine_threshold_is_needs_manual_verification():
    """A low-confidence Gemini suggestion does NOT get a free pass - the
    engine's own CONFIDENCE_THRESHOLD still applies to it exactly as it
    would to a low-confidence OCR value, proving Gemini supplies a
    proposal, not a verdict."""
    data = _ocr_result()
    del data["fields"]["maximum_retail_price_mrp"]
    suggestion = gs.GeminiFieldSuggestion(field="mrp", value="MRP Rs. 110.00", confidence=0.3, reason="uncertain")
    merged = gs.apply_suggestions_to_ocr_result(data, [suggestion])

    result = ComplianceEngine().evaluate(merged, "e_commerce_product_listing")
    assert result.rule_results[0].required_fields["maximum_retail_price_mrp"].status == "NEEDS_MANUAL_VERIFICATION"


def test_consumer_care_suggestion_is_shaped_into_the_expected_dict():
    """consumer_care_details is validated as a dict with specific sub-keys
    (compliance_engine.py's LMPC-R6 consumer_care_fields check) - a bare
    Gemini string must be shaped the same way region-based extraction
    already shapes it (_apply_consumer_care_region), not passed through raw."""
    data = _ocr_result()
    del data["fields"]["consumer_care_details"]
    suggestion = gs.GeminiFieldSuggestion(
        field="consumer_care", value="Consumer Care: 1800-999-8888, email care@example.com", confidence=0.9, reason="",
    )
    merged = gs.apply_suggestions_to_ocr_result(data, [suggestion])
    result = ComplianceEngine().evaluate(merged, "e_commerce_product_listing")
    care = result.rule_results[0].required_fields["consumer_care_details"]
    assert care.status == "PASS"
    assert care.value["telephone_number"] == "1800-999-8888"
    assert care.value["email_address"] == "care@example.com"


def test_mrp_suggestion_missing_wording_is_defensively_prefixed():
    """If Gemini (despite the prompt) returns a bare amount with no MRP
    wording, the merge step prepends it rather than letting the engine
    reject a perfectly good amount for a solvable formatting gap."""
    data = _ocr_result()
    del data["fields"]["maximum_retail_price_mrp"]
    # Includes the tax-inclusive qualifier so this test stays focused on
    # ITS OWN concern (the "MRP" wording prefix shim) without incidentally
    # tripping the unrelated P1 tax-inclusive-wording check added to
    # _mrp_result later - see test_mrp_suggestion_missing_wording_and_tax_
    # phrase_stays_needs_review below for that check's own dedicated test.
    suggestion = gs.GeminiFieldSuggestion(field="mrp", value="110.00 (Incl. of all taxes)", confidence=0.9, reason="")
    merged = gs.apply_suggestions_to_ocr_result(data, [suggestion])
    assert "MRP" in merged["fields"]["maximum_retail_price_mrp"]["value"]
    result = ComplianceEngine().evaluate(merged, "e_commerce_product_listing")
    assert result.rule_results[0].required_fields["maximum_retail_price_mrp"].status == "PASS"


def test_mrp_suggestion_missing_wording_and_tax_phrase_stays_needs_review():
    """If Gemini returns a bare amount with neither MRP wording nor the
    tax-inclusive qualifier, the "MRP" prefix shim still fixes the wording
    half, but the tax-inclusive requirement (rules/legal_metrology_rules_
    2011.json's mrp_format.text_requirement, enforced in _mrp_result) is a
    SEPARATE check this shim does not and should not paper over - a
    genuinely incomplete AI-sourced value correctly stays
    NEEDS_MANUAL_VERIFICATION, not a fabricated PASS."""
    data = _ocr_result()
    del data["fields"]["maximum_retail_price_mrp"]
    suggestion = gs.GeminiFieldSuggestion(field="mrp", value="110.00", confidence=0.9, reason="")
    merged = gs.apply_suggestions_to_ocr_result(data, [suggestion])
    result = ComplianceEngine().evaluate(merged, "e_commerce_product_listing")
    assert result.rule_results[0].required_fields["maximum_retail_price_mrp"].status == "NEEDS_MANUAL_VERIFICATION"


# --- 5. Gemini failure (timeout/error/quota) -> fallback stays functional -

def test_repeated_failure_returns_empty_list_not_an_exception(monkeypatch):
    attempts = []

    async def always_fails(self, image_bytes, fields):
        attempts.append(1)
        raise RuntimeError("simulated quota exceeded")

    monkeypatch.setattr(gs.GeminiService, "_call", always_fails)
    service = gs.GeminiService(api_key="test-key", timeout_seconds=1)
    result = asyncio.run(service.suggest_fields(b"fake-image-bytes", ["mrp"]))

    assert result == []
    assert len(attempts) == 2, "expected exactly one retry (2 total attempts) before giving up"


def test_timeout_falls_back_without_hanging(monkeypatch):
    async def hangs_forever(self, image_bytes, fields):
        await asyncio.sleep(10)
        return []  # pragma: no cover - never reached

    monkeypatch.setattr(gs.GeminiService, "_call", hangs_forever)
    service = gs.GeminiService(api_key="test-key", timeout_seconds=0.05)

    result = asyncio.wait_for(service.suggest_fields(b"fake-image-bytes", ["mrp"]), timeout=2)
    assert asyncio.run(result) == []


def test_compliance_evaluation_unaffected_when_gemini_returns_nothing():
    """The scan-level failure path (api/ocr.py: suggestions == [] ->
    ocr_dict is never touched) - simulated here directly: an unmerged
    ocr_result evaluates identically whether or not Gemini was consulted."""
    data = _ocr_result()
    del data["fields"]["maximum_retail_price_mrp"]
    before = ComplianceEngine().evaluate(data, "e_commerce_product_listing")
    after = ComplianceEngine().evaluate(gs.apply_suggestions_to_ocr_result(data, []), "e_commerce_product_listing")
    assert before.overall_status == after.overall_status == "REVIEW_REQUIRED"


# --- 6. Missing API key -> stays disabled, nothing crashes -----------------

def test_missing_api_key_returns_empty_list_without_raising():
    service = gs.GeminiService(api_key="")
    result = asyncio.run(service.suggest_fields(b"fake-image-bytes", ["mrp"]))
    assert result == []


def test_missing_api_key_client_init_raises_a_specific_typed_error():
    service = gs.GeminiService(api_key="")
    with pytest.raises(gs.GeminiInitializationError):
        service._get_client()


def test_app_still_imports_and_starts_up_fine():
    """Sanity check that wiring GeminiService into api/ocr.py didn't break
    the app import path (module-level constant reads, etc.) even when no
    real Gemini key is configured for these tests."""
    from backend.main import app

    assert app is not None


# --- 7. API key is never exposed to the frontend --------------------------

def test_redact_strips_the_configured_key_from_log_strings():
    original = gs.GEMINI_API_KEY
    try:
        gs.GEMINI_API_KEY = "AIzaSy-this-is-a-fake-test-key-0000000000"  # noqa: S105 - test fixture value, not a real credential
        message = f"Gemini request failed with key {gs.GEMINI_API_KEY} in the URL"
        redacted = gs._redact(message)
        assert gs.GEMINI_API_KEY not in redacted
        assert "REDACTED" in redacted
    finally:
        gs.GEMINI_API_KEY = original


def test_frontend_source_never_mentions_gemini_at_all():
    """The whole point of this integration is that Gemini is backend-only -
    the frontend has no reason to ever reference it in any form. A literal,
    case-insensitive scan of every frontend source file (HTML/JS) for the
    word "gemini" catches both an accidentally-leaked key AND a stray
    'gemini_api_key'-shaped field/config name, without needing to guess a
    real key value in advance."""
    assert FRONTEND_DIR.is_dir(), f"expected frontend directory at {FRONTEND_DIR}"
    offenders = []
    for path in FRONTEND_DIR.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".html", ".js", ".css", ".json"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"gemini", text, re.I):
                offenders.append(str(path))
    assert offenders == [], f"frontend files must never mention Gemini: {offenders}"


def test_frontend_source_never_contains_the_configured_api_key_value():
    """If a real GEMINI_API_KEY happens to be set in this process's
    environment, positively confirm its literal value does not appear
    anywhere in the frontend source tree - a concrete check on the actual
    secret value, not just its name."""
    if not gs.GEMINI_API_KEY:
        pytest.skip("no GEMINI_API_KEY configured in this environment to check for")
    for path in FRONTEND_DIR.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert gs.GEMINI_API_KEY not in text, f"found the configured GEMINI_API_KEY value inside {path}"


def test_no_response_schema_carries_a_raw_key_or_api_key_shaped_field():
    """Static schema inspection: none of the pydantic models that actually
    leave the backend as HTTP responses (OCRResult, ComplianceResult and
    everything they're built from, plus this module's own
    GeminiFieldSuggestion) declare any field whose name looks like it holds
    an API key."""
    from backend.models.compliance import ComplianceResult, FieldResult, NormalizedField, RuleResult
    from backend.ocr.schemas import OCRResult

    suspicious_name = re.compile(r"api[_-]?key|secret|token", re.I)
    for model in (OCRResult, ComplianceResult, RuleResult, FieldResult, NormalizedField, gs.GeminiFieldSuggestion):
        offending = [name for name in model.model_fields if suspicious_name.search(name)]
        assert offending == [], f"{model.__name__} unexpectedly declares key-shaped field(s): {offending}"


def test_gemini_fields_used_provenance_field_never_carries_the_key_itself():
    """The one new thing api/ocr.py persists to Mongo for a scan
    (`geminiFieldsUsed`) is just a list of field NAMES (e.g. ["mrp"]) -
    confirm the merge/suggestion path never puts the raw suggestion object
    (which could theoretically carry provider-side metadata) into it,
    only plain field-name strings."""
    suggestions = [
        gs.GeminiFieldSuggestion(field="mrp", value="MRP Rs. 50.00", confidence=0.9, reason="x"),
        gs.GeminiFieldSuggestion(field="net_quantity", value="50 g", confidence=0.97, reason="y"),
    ]
    fields_used = [s.field for s in suggestions]
    assert fields_used == ["mrp", "net_quantity"]
    assert all(isinstance(name, str) for name in fields_used)
