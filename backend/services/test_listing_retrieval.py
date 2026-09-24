"""
Tests for backend/services/listing_retrieval.py.

Uses httpx.MockTransport exclusively - no real network calls, and no
localhost/127.0.0.1 target (which url_safety.py would correctly reject
before any HTTP call is even attempted, making it useless for testing
retrieval-layer behavior like redirects/timeouts/size limits). MockTransport
intercepts at the transport layer, after url_safety.py's validation has
already passed for the "https://shop.example/..." style URLs used here.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from backend.services.listing_retrieval import MAX_REDIRECTS, MAX_RESPONSE_BYTES, ListingFetcher


def _resolver(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.fixture(autouse=True)
def _patch_dns(monkeypatch):
    # Every test URL here is a "shop.example" style hostname - patch
    # url_safety's real DNS resolution to a fixed public IP so validation
    # passes deterministically without a real DNS lookup.
    monkeypatch.setattr(socket, "getaddrinfo", _resolver)


def _fetcher_for(handler) -> ListingFetcher:
    return ListingFetcher(transport=httpx.MockTransport(handler))


def test_successful_html_fetch_returns_html_and_final_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text="<html><body>hi</body></html>")

    result = _fetcher_for(handler).fetch("https://shop.example/product/1")
    assert result.status == "SUCCESS"
    assert result.final_url == "https://shop.example/product/1"
    assert "hi" in result.html


def test_follows_a_safe_redirect_chain():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "https://shop.example/new"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>final</html>")

    result = _fetcher_for(handler).fetch("https://shop.example/old")
    assert result.status == "SUCCESS"
    assert result.final_url == "https://shop.example/new"


def test_too_many_redirects_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.strip("/").split("/")[-1] or 0)
        return httpx.Response(302, headers={"location": f"https://shop.example/{n + 1}"})

    result = _fetcher_for(handler).fetch("https://shop.example/0")
    assert result.status == "TOO_MANY_REDIRECTS"


def test_redirect_count_boundary_is_exactly_max_redirects():
    """Exactly MAX_REDIRECTS hops should succeed; MAX_REDIRECTS + 1 should not."""
    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.strip("/").split("/")[-1] or 0)
        if n >= MAX_REDIRECTS:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>done</html>")
        return httpx.Response(302, headers={"location": f"https://shop.example/{n + 1}"})

    result = _fetcher_for(handler).fetch("https://shop.example/0")
    assert result.status == "SUCCESS"


def test_redirect_to_a_private_address_is_blocked(monkeypatch):
    def selective_resolver(host, *_args, **_kwargs):
        if host == "internal.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        return _resolver()

    monkeypatch.setattr(socket, "getaddrinfo", selective_resolver)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "shop.example":
            return httpx.Response(302, headers={"location": "https://internal.example/secret"})
        raise AssertionError("must never actually connect to the redirect target")

    result = _fetcher_for(handler).fetch("https://shop.example/redirect-me")
    assert result.status == "REDIRECT_BLOCKED"


def test_redirect_with_no_location_header_is_a_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    result = _fetcher_for(handler).fetch("https://shop.example/broken-redirect")
    assert result.status == "CONNECTION_FAILURE"


def test_timeout_is_reported_distinctly():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = _fetcher_for(handler).fetch("https://shop.example/slow")
    assert result.status == "TIMEOUT"


def test_connection_error_is_reported_distinctly():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = _fetcher_for(handler).fetch("https://shop.example/down")
    assert result.status == "CONNECTION_FAILURE"


def test_http_error_status_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, text="not found")

    result = _fetcher_for(handler).fetch("https://shop.example/missing")
    assert result.status == "HTTP_ERROR"
    assert result.http_status == 404


def test_unsupported_content_type_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")

    result = _fetcher_for(handler).fetch("https://shop.example/brochure.pdf")
    assert result.status == "UNSUPPORTED_CONTENT_TYPE"


def test_oversized_response_by_content_length_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": str(MAX_RESPONSE_BYTES + 1)},
            text="x" * 100,
        )

    result = _fetcher_for(handler).fetch("https://shop.example/huge")
    assert result.status == "RESPONSE_TOO_LARGE"


def test_oversized_response_without_content_length_header_is_still_capped():
    def handler(request: httpx.Request) -> httpx.Response:
        # No content-length header - must be caught while streaming, not
        # just by trusting a (possibly absent/lying) declared length.
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * (MAX_RESPONSE_BYTES + 1000))

    result = _fetcher_for(handler).fetch("https://shop.example/huge-streamed")
    assert result.status == "RESPONSE_TOO_LARGE"


def test_empty_response_body_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"")

    result = _fetcher_for(handler).fetch("https://shop.example/empty")
    assert result.status == "EMPTY_PAGE"


def test_ssrf_blocked_before_any_network_call_is_attempted():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never reach the transport for an SSRF-blocked URL")

    result = _fetcher_for(handler).fetch("http://127.0.0.1:8000/admin")
    assert result.status == "SSRF_BLOCKED"


def test_unsupported_scheme_blocked_before_any_network_call():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never reach the transport for an unsupported scheme")

    result = _fetcher_for(handler).fetch("file:///etc/passwd")
    assert result.status == "UNSUPPORTED_SCHEME"
