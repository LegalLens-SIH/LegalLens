"""Tests for backend/services/listing_extraction.py."""

from __future__ import annotations

from backend.services.listing_extraction import extract_listing_data
from backend.services.listing_retrieval import RetrievalOutcome


def _success(html: str, url: str = "https://shop.example/p/1") -> RetrievalOutcome:
    return RetrievalOutcome(status="SUCCESS", requested_url=url, final_url=url, html=html, content_type="text/html", http_status=200)


JSON_LD_HTML = """
<html><head><title>Handwash 500ml - Shop</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "Herbal Handwash 500ml",
  "brand": {"@type": "Brand", "name": "CleanCo"},
  "manufacturer": "CleanCo Industries Pvt Ltd",
  "countryOfOrigin": "India",
  "offers": {"@type": "Offer", "price": "85.00", "priceCurrency": "INR"},
  "additionalProperty": [
    {"@type": "PropertyValue", "name": "Net Quantity", "value": "500 ml"},
    {"@type": "PropertyValue", "name": "MRP", "value": "100"},
    {"@type": "PropertyValue", "name": "Consumer Care", "value": "1800-000-0000"}
  ]
}
</script></head>
<body><p>Some marketing copy.</p></body></html>
"""


def test_extracts_full_product_data_from_json_ld():
    listing = extract_listing_data("https://shop.example/p/1", _success(JSON_LD_HTML))
    assert listing.retrieval_status == "SUCCESS"
    assert listing.product_name == "Herbal Handwash 500ml"
    assert listing.brand == "CleanCo"
    assert listing.manufacturer == "CleanCo Industries Pvt Ltd"
    assert listing.country_of_origin == "India"
    assert listing.net_quantity == "500 ml"
    assert listing.mrp == "100"
    assert listing.consumer_care == "1800-000-0000"
    assert listing.price == "INR 85.00"
    assert not listing.unavailable_fields.__contains__("product_name")


def test_json_ld_fields_carry_provenance_evidence():
    listing = extract_listing_data("https://shop.example/p/1", _success(JSON_LD_HTML))
    fields_with_evidence = {e.field for e in listing.evidence}
    assert "product_name" in fields_with_evidence
    assert "net_quantity" in fields_with_evidence
    for evidence in listing.evidence:
        assert evidence.extraction_source in ("json-ld", "meta-tag", "text-label", "page-title")


def test_malformed_json_ld_is_not_fatal_and_is_warned_about():
    html = '<html><head><script type="application/ld+json">{not valid json</script></head><body>text that is long enough to not be flagged as js-only page content here</body></html>'
    listing = extract_listing_data("https://shop.example/p/1", _success(html))
    assert listing.retrieval_status == "SUCCESS"
    assert any("could not be parsed" in w for w in listing.extraction_warnings)


def test_multiple_products_in_json_ld_uses_first_and_warns():
    html = """
    <html><head>
    <script type="application/ld+json">[
      {"@type": "Product", "name": "First Product"},
      {"@type": "Product", "name": "Second Product"}
    ]</script>
    </head><body>placeholder text long enough for extraction purposes here</body></html>
    """
    listing = extract_listing_data("https://shop.example/p/1", _success(html))
    assert listing.product_name == "First Product"
    assert any("Found 2 Product entries" in w for w in listing.extraction_warnings)


META_TAG_HTML = """
<html><head><title>Fallback Product</title>
<meta property="og:title" content="Herbal Facewash" />
<meta property="og:brand" content="CleanCo" />
<meta property="product:price:amount" content="120" />
<meta property="product:price:currency" content="INR" />
</head><body>Just some body copy without labeled declarations at all really.</body></html>
"""


def test_falls_back_to_meta_tags_when_no_json_ld():
    listing = extract_listing_data("https://shop.example/p/2", _success(META_TAG_HTML))
    assert listing.product_name == "Herbal Facewash"
    assert listing.brand == "CleanCo"
    assert listing.price == "INR 120"
    assert "country_of_origin" in listing.unavailable_fields


TEXT_LABEL_HTML = """
<html><head><title>Some Product</title></head>
<body>
<div>Country of Origin: India</div>
<div>Net Quantity: 250 g</div>
<div>Manufactured by: ABC Foods Pvt Ltd, Mumbai</div>
<div>Consumer Care: 1800-111-2222</div>
<div>MRP: Rs. 45.00</div>
</body></html>
"""


def test_extracts_from_label_anchored_text_when_no_structured_data():
    listing = extract_listing_data("https://shop.example/p/3", _success(TEXT_LABEL_HTML))
    assert listing.country_of_origin == "India"
    assert listing.net_quantity == "250 g"
    assert listing.manufacturer == "ABC Foods Pvt Ltd, Mumbai"
    assert listing.consumer_care == "1800-111-2222"
    assert listing.mrp == "45.00"
    for evidence in listing.evidence:
        if evidence.field in ("country_of_origin", "net_quantity", "manufacturer", "consumer_care", "mrp"):
            assert evidence.extraction_source == "text-label"


def test_generic_prose_is_never_treated_as_a_declaration():
    """A page with no explicit label anywhere must never have fields
    guessed from surrounding prose, even if the words appear nearby."""
    html = """
    <html><head><title>Generic Product Page</title></head>
    <body><p>This wonderful product is made with love and comes from a
    trusted origin, in the finest quantity possible for your family.</p></body></html>
    """
    listing = extract_listing_data("https://shop.example/p/4", _success(html))
    assert listing.country_of_origin is None
    assert listing.net_quantity is None
    assert listing.manufacturer is None


def test_net_quantity_label_with_unparseable_value_is_left_unavailable_with_warning():
    html = """
    <html><head><title>Product</title></head>
    <body><div>Net Quantity: see packaging</div>
    <p>Extra filler text so the page is not classified as JS-only content.</p></body></html>
    """
    listing = extract_listing_data("https://shop.example/p/5", _success(html))
    assert listing.net_quantity is None
    assert any("could not be confidently parsed" in w for w in listing.extraction_warnings)


def test_product_name_falls_back_to_page_title_with_warning():
    html = "<html><head><title>Herbal Soap Bar 100g</title></head><body>Some unrelated marketing copy that has no labels.</body></html>"
    listing = extract_listing_data("https://shop.example/p/6", _success(html))
    assert listing.product_name == "Herbal Soap Bar 100g"
    assert any(e.extraction_source == "page-title" for e in listing.evidence)
    assert any("derived from the page" in w for w in listing.extraction_warnings)


def test_js_only_page_with_no_extractable_content_is_marked_unavailable():
    html = '<html><head><title></title></head><body><div id="app"></div><script src="bundle.js"></script></body></html>'
    listing = extract_listing_data("https://shop.example/spa-page", _success(html))
    assert listing.retrieval_status == "LISTING_UNAVAILABLE"
    assert listing.error_message is not None


def test_non_success_retrieval_outcome_passes_through_status_unchanged():
    outcome = RetrievalOutcome(status="HTTP_ERROR", requested_url="https://shop.example/404", http_status=404, error_message="HTTP 404")
    listing = extract_listing_data("https://shop.example/404", outcome)
    assert listing.retrieval_status == "HTTP_ERROR"
    assert listing.error_message == "HTTP 404"
    assert listing.product_name is None


def test_unavailable_fields_lists_every_field_never_found():
    html = "<html><head><title>T</title></head><body>Country of Origin: India and some extra padding text here for length.</body></html>"
    listing = extract_listing_data("https://shop.example/p/7", _success(html))
    assert "country_of_origin" not in listing.unavailable_fields
    assert "net_quantity" in listing.unavailable_fields
    assert "mrp" in listing.unavailable_fields
