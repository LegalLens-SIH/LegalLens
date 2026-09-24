"""
SSRF-safe URL validation for Phase 2A e-commerce listing verification
(backend/services/listing_retrieval.py).

Scope (deliberately narrow): this module answers exactly one question -
"is it safe to make an outbound HTTP(S) request to this URL right now?" -
and nothing else. It has no knowledge of listing extraction, comparison,
or compliance.

What is blocked:
  - any scheme other than http/https (file://, ftp://, javascript:,
    data:, ...)
  - URLs with no hostname, or with embedded userinfo (http://x@evil/, a
    classic host-confusion vector)
  - "localhost" and *.localhost by name
  - any hostname/IP literal that resolves to a loopback, private,
    link-local, reserved, unspecified, or multicast address (IPv4 or
    IPv6) - this covers 127.0.0.1, 0.0.0.0, the RFC1918 ranges, IPv6
    loopback/ULA/link-local, and cloud metadata endpoints
    (169.254.169.254 is link-local) all through ONE general rule rather
    than a maintained blocklist of specific addresses
  - IPv4-mapped IPv6 addresses (::ffff:127.0.0.1) that embed an unsafe
    IPv4 address

Real DNS resolution is used (never a string-only hostname check) so a
hostname that merely LOOKS public but resolves to a private address is
still caught - a bare string comparison against "localhost" cannot catch
that. Every redirect hop encountered during retrieval must be re-validated
through this same function (see listing_retrieval.py) - a URL is only ever
as safe as the LAST hop actually connected to, not just the first one the
user typed.

Known, documented limitation: DNS is resolved here for validation, and
then resolved AGAIN by httpx a short time later when the connection is
actually made (listing_retrieval.py). A sufficiently well-timed DNS
rebinding attack could theoretically swap the record in that gap. Fully
closing this would require pinning the validated IP into the actual
socket connection (a custom transport), which this MVP does not
implement - see the PHASE 2 IMPLEMENTATION REPORT's Known Limitations.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeURLError(ValueError):
    """Raised when a URL fails scheme/format/SSRF safety validation.

    `reason_code` is a stable, machine-readable identifier (never the
    free-form message) that callers map to a ListingRetrievalStatus value.
    """

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SafeURL:
    url: str
    scheme: str
    hostname: str
    port: int | None
    resolved_ips: tuple[str, ...]


def _is_unsafe_ip(ip: IPAddress) -> bool:
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_unsafe_ip(mapped):
        return True
    sixtofour = getattr(ip, "sixtofour", None)
    if sixtofour is not None and _is_unsafe_ip(sixtofour):
        return True
    return False


def validate_public_url(url: str, *, resolver: Callable | None = None) -> SafeURL:
    """Validate a URL is safe to request. Raises UnsafeURLError on any
    failure - never returns a partially-validated result.

    `resolver` defaults to a FRESH lookup of socket.getaddrinfo at call
    time (not bound once at function-definition time) - this matters for
    tests: monkeypatching socket.getaddrinfo (e.g. in backend/services/
    test_listing_retrieval.py, which calls this function indirectly via
    ListingFetcher and never passes `resolver` itself) only works if the
    attribute is re-read on every call, not captured as a mutable default
    argument once at import time.
    """
    resolver = resolver if resolver is not None else socket.getaddrinfo
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("invalid_url", "URL is empty")
    url = url.strip()

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError("invalid_url", f"URL could not be parsed: {exc}") from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            "unsupported_scheme",
            f"Unsupported URL scheme '{scheme or '(none)'}'. Only http/https are accepted.",
        )

    if parts.username or parts.password:
        raise UnsafeURLError("invalid_url", "URLs with embedded credentials are not accepted")

    try:
        hostname = parts.hostname
    except ValueError as exc:
        raise UnsafeURLError("invalid_url", f"URL host could not be parsed: {exc}") from exc
    if not hostname:
        raise UnsafeURLError("invalid_url", "URL has no hostname")

    hostname_lower = hostname.lower()
    if hostname_lower in {"localhost", "localhost.localdomain"} or hostname_lower.endswith(".localhost"):
        raise UnsafeURLError("ssrf_blocked", "Requests to localhost are not permitted")

    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("invalid_url", f"URL port could not be parsed: {exc}") from exc

    try:
        literal_ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_unsafe_ip(literal_ip):
            raise UnsafeURLError(
                "ssrf_blocked",
                f"Requests to {hostname} are not permitted (private/internal network address)",
            )
        resolved = (str(literal_ip),)
    else:
        try:
            addr_info = resolver(hostname, None)
        except socket.gaierror as exc:
            raise UnsafeURLError("dns_failure", f"Could not resolve hostname '{hostname}': {exc}") from exc
        except UnicodeError as exc:
            raise UnsafeURLError("invalid_url", f"Hostname '{hostname}' is not valid: {exc}") from exc

        resolved_set: list[str] = []
        for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                candidate = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_unsafe_ip(candidate):
                raise UnsafeURLError(
                    "ssrf_blocked",
                    f"Hostname '{hostname}' resolves to a private/internal network address "
                    f"({ip_str}); request blocked",
                )
            resolved_set.append(str(candidate))
        if not resolved_set:
            raise UnsafeURLError("dns_failure", f"Could not resolve hostname '{hostname}' to any address")
        resolved = tuple(resolved_set)

    return SafeURL(url=url, scheme=scheme, hostname=hostname, port=port, resolved_ips=resolved)
