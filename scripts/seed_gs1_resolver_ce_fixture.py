from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from backend.services.gs1_resolver_provider import _validate_local_base_url


PORTAL_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PORTAL_ROOT / "dev" / "gs1_resolver_ce_fixture.json"
MAX_RESPONSE_BYTES = 64 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the synthetic LegalLense Digital Link Resolver CE fixture.")
    parser.add_argument("--apply", action="store_true", help="POST the fixture to the local Resolver CE instance.")
    args = parser.parse_args()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload = fixture["payload"]
    print("Source: LegalLense synthetic GS1 Resolver CE fixture")
    print("Environment: development; status: synthetic")
    print(f"Anchor: {payload['anchor']}")
    print(f"Resource: {payload['links'][0]['href']}")
    if not args.apply:
        print("Dry run only. Start the resolver and rerun with --apply to seed via documented POST /api/new.")
        return 0

    token = os.getenv("GS1_RESOLVER_SESSION_TOKEN", "")
    if not token:
        parser.error("GS1_RESOLVER_SESSION_TOKEN is required when --apply is used; no request was sent.")
    if len(token) < 32 or token.strip().lower() in {"secret", "change_me", "change_me_to_a_long_random_string"}:
        parser.error("Use a locally configured random token of at least 32 characters; no request was sent.")
    base_url = _validate_local_base_url(os.getenv("GS1_RESOLVER_BASE_URL", "http://localhost:8080"))
    try:
        with httpx.Client(timeout=httpx.Timeout(3.0), follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST",
                base_url + "/api/new",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            ) as response:
                declared_size = response.headers.get("content-length")
                if declared_size:
                    try:
                        declared_size_int = int(declared_size)
                    except ValueError:
                        print("Resolver seed response had an invalid Content-Length; fixture status is unknown.")
                        return 2
                    if declared_size_int > MAX_RESPONSE_BYTES:
                        print("Resolver seed response exceeded the size limit; fixture status is unknown.")
                        return 2
                response_body = bytearray()
                for chunk in response.iter_bytes():
                    response_body.extend(chunk)
                    if len(response_body) > MAX_RESPONSE_BYTES:
                        print("Resolver seed response exceeded the size limit; fixture status is unknown.")
                        return 2
                status_code = response.status_code
    except httpx.HTTPError as exc:
        print(f"Resolver unavailable ({type(exc).__name__}); fixture was not confirmed seeded.")
        return 2
    if status_code not in {200, 201}:
        print(f"Resolver fixture seed failed with HTTP {status_code}; response body omitted.")
        return 2
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        print("Resolver seed response was not JSON; fixture status is unknown.")
        return 2
    try:
        result = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"Resolver returned malformed JSON (HTTP {status_code}); fixture status is unknown.")
        return 2
    expected_entry = payload["anchor"].strip("/").replace("/", "_")
    result_metadata = result.get("result") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("entry") != expected_entry
        or not isinstance(result_metadata, dict)
        or result_metadata.get("response_status") not in {200, 201}
    ):
        print(f"Resolver response did not match the documented data-entry result envelope (HTTP {status_code}).")
        return 2
    print(f"Resolver accepted the synthetic fixture (HTTP {status_code}, entry {expected_entry}).")
    print("No product ownership, authenticity, or official GS1 registry claim is made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
