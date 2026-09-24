"""Tests for backend/services/url_safety.py - SSRF/scheme/format validation."""

from __future__ import annotations

import socket

import pytest

from backend.services.url_safety import UnsafeURLError, validate_public_url


def _fake_resolver(ip: str):
    def resolver(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]

    return resolver


def test_accepts_https_url_resolving_to_a_public_ip():
    safe = validate_public_url("https://example.com/product/123", resolver=_fake_resolver("93.184.216.34"))
    assert safe.scheme == "https"
    assert safe.hostname == "example.com"
    assert safe.resolved_ips == ("93.184.216.34",)


def test_accepts_http_url():
    safe = validate_public_url("http://example.com/", resolver=_fake_resolver("93.184.216.34"))
    assert safe.scheme == "http"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/file",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "ws://example.com/socket",
])
def test_rejects_unsupported_schemes(url):
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url(url)
    assert exc_info.value.reason_code == "unsupported_scheme"


def test_rejects_empty_url():
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("")
    assert exc_info.value.reason_code == "invalid_url"


def test_rejects_url_with_no_hostname():
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("https:///path")
    assert exc_info.value.reason_code == "invalid_url"


def test_rejects_embedded_credentials():
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("https://user:pass@example.com/")
    assert exc_info.value.reason_code == "invalid_url"


@pytest.mark.parametrize("url", ["http://localhost/", "http://localhost:8000/api", "http://sub.localhost/"])
def test_rejects_localhost_by_name(url):
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url(url)
    assert exc_info.value.reason_code == "ssrf_blocked"


@pytest.mark.parametrize("ip", [
    "127.0.0.1",
    "127.5.5.5",
    "0.0.0.0",
    "10.0.0.5",
    "172.16.0.5",
    "192.168.1.1",
    "169.254.169.254",  # cloud metadata endpoint - covered by link-local
    "169.254.0.1",
])
def test_rejects_literal_unsafe_ipv4_addresses(ip):
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url(f"http://{ip}/")
    assert exc_info.value.reason_code == "ssrf_blocked"


@pytest.mark.parametrize("ip", ["::1", "fc00::1", "fe80::1"])
def test_rejects_literal_unsafe_ipv6_addresses(ip):
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url(f"http://[{ip}]/")
    assert exc_info.value.reason_code == "ssrf_blocked"


def test_rejects_ipv4_mapped_ipv6_loopback():
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("http://[::ffff:127.0.0.1]/")
    assert exc_info.value.reason_code == "ssrf_blocked"


def test_rejects_hostname_that_resolves_to_a_private_address():
    """A hostname string can look completely public while resolving to a
    private/internal IP - this must be caught via real DNS resolution, not
    a string check on the hostname alone (DNS rebinding defense)."""
    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("http://looks-public.example.com/", resolver=_fake_resolver("169.254.169.254"))
    assert exc_info.value.reason_code == "ssrf_blocked"


def test_dns_failure_is_reported_distinctly():
    def failing_resolver(host, port):
        raise socket.gaierror("Name or service not known")

    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("http://does-not-resolve.invalid/", resolver=failing_resolver)
    assert exc_info.value.reason_code == "dns_failure"


def test_hostname_with_one_safe_and_one_unsafe_address_is_blocked():
    """If ANY resolved address is unsafe, the whole request is blocked -
    a multi-A-record host cannot be used to "smuggle" a private address
    behind a public-looking one."""
    def resolver(host, port):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]

    with pytest.raises(UnsafeURLError) as exc_info:
        validate_public_url("http://mixed.example.com/", resolver=resolver)
    assert exc_info.value.reason_code == "ssrf_blocked"
