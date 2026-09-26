from __future__ import annotations

from backend.services.url_ingestion import extract_jsonld_metadata, normalize_listing_metadata


HTML = '''
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Sample pack","brand":{"@type":"Brand","name":"Acme"},"offers":{"@type":"Offer","price":"₹599","priceCurrency":"INR"}}
</script>
</head></html>
'''


def test_extract_jsonld_metadata_reads_product_fields():
    metadata = extract_jsonld_metadata(HTML)
    assert metadata["name"] == "Sample pack"
    assert metadata["brand"] == "Acme"
    assert metadata["price"] == "₹599"


def test_normalize_listing_metadata_returns_clean_dictionary():
    metadata = {"name": "Sample pack", "brand": " Acme ", "offers": {"price": "₹599"}}
    normalized = normalize_listing_metadata(metadata)
    assert normalized["product_name"] == "Sample pack"
    assert normalized["brand"] == "Acme"
    assert normalized["price"] == "₹599"
