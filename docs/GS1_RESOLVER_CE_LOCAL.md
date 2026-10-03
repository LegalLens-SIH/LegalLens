# Local GS1 Digital Link Resolver CE

## Scope and authority

The optional adapter connects only to a trusted loopback instance of the official repository at https://github.com/gs1/GS1_DigitalLink_Resolver_CE. The upstream README identifies it as community-maintained, not maintained by GS1, and cautions that full conformance to the published resolver standard should not be assumed.

Resolver CE maps Digital Link identifiers to configured web resources. It is not a global product registry and does not establish GTIN ownership, product authenticity, legal compliance, Verified by GS1 status, or GS1 India DataKart verification. Resolver results are supporting evidence only. PaddleOCR and ComplianceEngine remain authoritative.

The checkout inspected for this integration is `E:\LegalLense-main\GS1_DigitalLink_Resolver_CE`, branch `master`, commit `bf885fdf4888f0395229478bf6b50342c1f761a8`, README version 3.0.0, Apache-2.0. The upstream `tests/` folder warns not to change its JSON examples or `setup_test.py`; LegalLense fixture data is kept separately under `dev/`.

## Documented protocol used

The upstream README documents the resolver at `http://localhost:8080`, with OpenAPI/Swagger served at that same root. Its `tests/setup_test.py` demonstrates:

- Resolution: `GET /01/{GTIN-14}` with redirects disabled; a configured link is returned as HTTP 307 with a `Location` resource URL.
- Multiple links: HTTP 300 with a JSON `linkset` when the request selects multiple resources.
- Not found: HTTP 404 for an absent identifier/resource.
- Data entry: authenticated `POST /api/new` with the documented v3 object containing `anchor`, `itemDescription`, `defaultLinktype`, and `links`.

The LegalLense adapter uses only the documented Digital Link GET route. It does not call data-entry APIs during scans. The separate manual seed script uses only the documented `POST /api/new` route.

## Clone and startup

The checked-out Resolver CE was built and started locally on 2026-10-03. Its ignored `.env` contains development-only MongoDB credentials and a locally generated bearer token; do not commit or share it. All four Compose containers remained running during validation. The Compose file has no Docker healthchecks, so readiness was verified through its documented OpenAPI root and Digital Link/data-entry routes.

When Docker is available, the upstream README documents:

```powershell
Set-Location E:\LegalLense-main\GS1_DigitalLink_Resolver_CE
Copy-Item .env.example .env
# Edit .env locally: replace sample MongoDB credentials and SESSION_TOKEN.
docker compose up -d --build
```

The README documents `http://localhost:8080` for the proxy and API documentation. The local `docker-compose.yml` reads the ignored `.env` and binds the proxy only to `127.0.0.1`. MongoDB remains on container port `27017` and is published as `127.0.0.1:27018` because an existing host MongoDB already owns port `27017`. The data-entry service has no separately published port; its documented API is behind the localhost proxy and requires the locally configured bearer token. `docker compose config` passed, the documented root returned HTTP 200 with Swagger UI, fixture seeding returned HTTP 201, and resolver requests returned the expected HTTP 307/404.

## LegalLense configuration

Resolver lookups are disabled by default. The backend accepts only an HTTP loopback origin (`localhost`, `127.0.0.1`, or `::1`), with no URL path, credentials, query, or fragment. It applies a bounded timeout, a maximum of two transient connection/timeout retries, and a 128 KiB response limit. It does not follow redirects; a documented 307 resource URL is treated as resolver output and is never fetched by the backend.

Set the following in the environment inherited by the LegalLense backend process to opt in locally:

```text
GS1_RESOLVER_ENABLED=true
GS1_RESOLVER_BASE_URL=http://localhost:8080
GS1_RESOLVER_TIMEOUT_SECONDS=2
GS1_RESOLVER_MAX_RETRIES=1
GS1_RESOLVER_SYNTHETIC_GTINS=10012345678902
```

For the Portal's Windows launcher, set those variables in the same PowerShell process before starting `start-local.cmd`. Keep Resolver CE and LegalLense disabled/unavailable states distinct. The `.env.example` leaves `GS1_RESOLVER_ENABLED=false` and the seeder token blank.

For a local scan lookup, include the synthetic fixture GTIN in `GS1_RESOLVER_SYNTHETIC_GTINS` and explicitly set `GS1_RESOLVER_ENABLED=true` in the backend process environment. Reachability is checked by each bounded lookup, not by a separate health probe. No setting enables Verified by GS1 or DataKart.

## Synthetic fixture

`dev/gs1_resolver_ce_fixture.json` contains the only LegalLense test fixture:

- GTIN: `10012345678902` (valid GTIN-14 check digit)
- Anchor: `/01/10012345678902`
- Resource: `https://synthetic.invalid/legal-lense/gs1-resolver-ce/10012345678902`
- Source/environment/status: `LegalLense synthetic GS1 Resolver CE fixture` / `development` / `synthetic`

The `.invalid` resource domain is reserved for examples; it is not a live product page. To preview the payload without a resolver, run from the Portal root:

```powershell
.\backend\.venv311\Scripts\python.exe -m scripts.seed_gs1_resolver_ce_fixture
```

Seeding is never automatic. With Docker running and the same randomly generated token (at least 32 characters) configured in the ignored Resolver CE `.env`, provide `GS1_RESOLVER_SESSION_TOKEN` only in the local PowerShell process and explicitly add `--apply`. The script rejects the upstream sample token, follows no redirects, ignores proxy environment variables, bounds the response, and validates the documented entry/result envelope without logging the token or body. Seeding applies only the synthetic development fixture above; it does not create a real product or registry record.

## Validation boundary

Focused validation passed: 47 GS1 resolver/provider/seeder tests, 302 OCR/API/architecture/compliance tests, and 28 frontend GS1/export tests. Real requests reached the running Resolver CE from both the direct client and LegalLense's `/api/scans` path; the existing API test seam supplied synthetic barcode/OCR data, while the provider's HTTP request and Resolver CE response were real. The seeded result was persisted as report-scoped supporting evidence. Timeout, connection failure, disabled, not-found, malformed-response, and authority-isolation behavior remains covered by the focused tests above. This verifies only the local Resolver CE integration, not any official GS1 registry, Verified by GS1, or GS1 India DataKart service.
