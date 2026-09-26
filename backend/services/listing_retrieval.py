"""
Safe, minimal HTTP retrieval for Phase 2A e-commerce listing verification.

Fetches ONE publicly-accessible HTML page for a manually-supplied product
URL, and nothing else: no JavaScript execution, no browser automation, no
following of arbitrary links beyond the page itself and its redirect
chain. Every request - and every redirect hop - is validated by
backend/services/url_safety.py before being connected to; see that
module's docstring for the exact SSRF protections and their one
documented residual limitation (DNS-rebinding window between validation
and connection).

Uses httpx (already a project dependency - see backend/ocr/gemini_service.py
and backend/requirements.txt) rather than introducing a new HTTP library,
per this feature's "minimal, well-supported HTTP client" requirement.

`ListingFetcher.fetch` is the ONLY place an actual network call happens -
mirroring GeminiService's `_call`-isolation pattern (backend/ocr/
gemini_service.py) - so tests can exercise the redirect/timeout/size/
content-type handling deterministically via httpx.MockTransport, without a
real network call and without needing a real (SSRF-blocked) localhost
target. See backend/services/test_listing_retrieval.py.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.database import get_database
from backend.models.listing import ListingRetrievalStatus
from backend.services.audit import append_audit_event
from backend.services.url_safety import UnsafeURLError, validate_public_url

logger = logging.getLogger("legallense.services.listing_retrieval")

CONNECT_TIMEOUT_SECONDS = 6.0
READ_TIMEOUT_SECONDS = 8.0
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB - a listing page, never a binary download
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
USER_AGENT = "LegalLenseListingVerifier/1.0 (+official review tool; read-only, non-indexing)"

_REASON_TO_STATUS: dict[str, ListingRetrievalStatus] = {
    "invalid_url": "INVALID_URL",
    "unsupported_scheme": "UNSUPPORTED_SCHEME",
    "ssrf_blocked": "SSRF_BLOCKED",
    "dns_failure": "DNS_FAILURE",
}


@dataclass
class RetrievalOutcome:
    status: ListingRetrievalStatus
    requested_url: str
    final_url: Optional[str] = None
    html: Optional[str] = None
    content_type: Optional[str] = None
    http_status: Optional[int] = None
    error_message: Optional[str] = None
    body_sha256: Optional[str] = None


def _charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"')
    return "utf-8"


class ListingFetcher:
    """Thin, injectable HTTP fetcher. `transport` is exposed purely for
    tests (httpx.MockTransport) - production code never passes it, so
    real requests always use httpx's normal transport."""

    def __init__(self, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._transport = transport

    def fetch(self, url: str) -> RetrievalOutcome:
        try:
            safe = validate_public_url(url)
        except UnsafeURLError as exc:
            status = _REASON_TO_STATUS.get(exc.reason_code, "INVALID_URL")
            logger.info("Listing URL rejected (%s): %s", status, exc)
            try:
                db = get_database()
                append_audit_event(
                    db,
                    event_type="url_fetch",
                    entity_type="listing_url",
                    entity_id=url,
                    actor_role="system",
                    action="fetch_blocked",
                    details={"requested_url": url, "reason_code": exc.reason_code, "status": status},
                )
            except Exception:
                pass
            return RetrievalOutcome(status=status, requested_url=url, error_message=str(exc))

        current_url = safe.url
        client_kwargs: dict = dict(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=READ_TIMEOUT_SECONDS,
                pool=READ_TIMEOUT_SECONDS,
            ),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            with httpx.Client(**client_kwargs) as client:
                redirects_followed = 0
                while True:
                    try:
                        with client.stream("GET", current_url) as response:
                            if response.is_redirect:
                                redirects_followed += 1
                                if redirects_followed > MAX_REDIRECTS:
                                    return RetrievalOutcome(
                                        status="TOO_MANY_REDIRECTS", requested_url=url, final_url=current_url,
                                        error_message=f"Exceeded {MAX_REDIRECTS} redirects",
                                    )
                                location = response.headers.get("location")
                                if not location:
                                    return RetrievalOutcome(
                                        status="CONNECTION_FAILURE", requested_url=url, final_url=current_url,
                                        error_message="Redirect response had no Location header",
                                    )
                                next_url = str(httpx.URL(current_url).join(location))
                                try:
                                    safe = validate_public_url(next_url)
                                except UnsafeURLError as exc:
                                    mapped = _REASON_TO_STATUS.get(exc.reason_code, "REDIRECT_BLOCKED")
                                    blocked_status: ListingRetrievalStatus = (
                                        "REDIRECT_BLOCKED" if mapped == "SSRF_BLOCKED" else mapped
                                    )
                                    logger.info(
                                        "Listing redirect target rejected (%s): %s -> %s",
                                        blocked_status, current_url, next_url,
                                    )
                                    return RetrievalOutcome(
                                        status=blocked_status, requested_url=url, final_url=current_url,
                                        error_message=str(exc),
                                    )
                                current_url = safe.url
                                continue

                            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()

                            if response.status_code >= 400:
                                return RetrievalOutcome(
                                    status="HTTP_ERROR", requested_url=url, final_url=current_url,
                                    http_status=response.status_code, content_type=content_type,
                                    error_message=f"HTTP {response.status_code}",
                                )

                            if content_type not in ALLOWED_CONTENT_TYPES:
                                return RetrievalOutcome(
                                    status="UNSUPPORTED_CONTENT_TYPE", requested_url=url, final_url=current_url,
                                    http_status=response.status_code, content_type=content_type,
                                    error_message=f"Missing or unsupported content type '{content_type or '(missing)'}'",
                                )

                            declared_length = response.headers.get("content-length")
                            if declared_length is not None:
                                try:
                                    if int(declared_length) > MAX_RESPONSE_BYTES:
                                        return RetrievalOutcome(
                                            status="RESPONSE_TOO_LARGE", requested_url=url, final_url=current_url,
                                            http_status=response.status_code,
                                            error_message=f"Response exceeds {MAX_RESPONSE_BYTES} byte limit",
                                        )
                                except ValueError:
                                    pass

                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) > MAX_RESPONSE_BYTES:
                                    return RetrievalOutcome(
                                        status="RESPONSE_TOO_LARGE", requested_url=url, final_url=current_url,
                                        http_status=response.status_code,
                                        error_message=f"Response exceeds {MAX_RESPONSE_BYTES} byte limit",
                                    )

                            if not body:
                                return RetrievalOutcome(
                                    status="EMPTY_PAGE", requested_url=url, final_url=current_url,
                                    http_status=response.status_code, content_type=content_type,
                                )

                            charset = _charset_from_content_type(content_type) if content_type else "utf-8"
                            try:
                                html_text = bytes(body).decode(charset, errors="replace")
                            except LookupError:
                                html_text = bytes(body).decode("utf-8", errors="replace")

                            result = RetrievalOutcome(
                                status="SUCCESS", requested_url=url, final_url=current_url,
                                html=html_text, content_type=content_type, http_status=response.status_code,
                                body_sha256=hashlib.sha256(body).hexdigest(),
                            )
                            try:
                                db = get_database()
                                append_audit_event(
                                    db,
                                    event_type="url_fetch",
                                    entity_type="listing_url",
                                    entity_id=current_url,
                                    actor_role="system",
                                    action="fetch_completed",
                                    details={
                                        "requested_url": url,
                                        "final_url": current_url,
                                        "status": result.status,
                                        "http_status": result.http_status,
                                        "content_type": result.content_type,
                                    },
                                )
                            except Exception:
                                pass
                            return result
                    except httpx.TimeoutException as exc:
                        return RetrievalOutcome(
                            status="TIMEOUT", requested_url=url, final_url=current_url, error_message=str(exc),
                        )
                    except httpx.ConnectError as exc:
                        return RetrievalOutcome(
                            status="CONNECTION_FAILURE", requested_url=url, final_url=current_url,
                            error_message=str(exc),
                        )
                    except httpx.HTTPError as exc:
                        return RetrievalOutcome(
                            status="CONNECTION_FAILURE", requested_url=url, final_url=current_url,
                            error_message=str(exc),
                        )
        except Exception as exc:  # pragma: no cover - defensive catch-all; every expected failure is handled above
            logger.exception("Unexpected error retrieving listing URL %s", url)
            return RetrievalOutcome(status="UNKNOWN_ERROR", requested_url=url, error_message=str(exc))
