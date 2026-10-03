// Focused tests for the Phase 2A e-commerce listing verification UI added
// to frontend/compliance-report.html. Follows this project's established
// convention (see test_disclaimer_render.mjs) of source-pattern checks
// against the raw page text rather than a full DOM simulation of this
// page's much larger, unrelated rendering logic - proportionate to what
// this task's own "do not introduce unnecessary complexity" guidance
// argues for.
//
// Run with: node frontend/tests/test_listing_verification_render.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.join(__dirname, "..");

function readPage(name) {
  return readFileSync(path.join(FRONTEND_DIR, name), "utf8");
}

const SOURCE = readPage("compliance-report.html");
const OCR_CLIENT = readPage(path.join("assets", "ocr-client.js"));

test("has a URL input, verify button, and result containers", () => {
  assert.match(SOURCE, /id=["']listing-url-input["']/);
  assert.match(SOURCE, /id=["']listing-verify-btn["']/);
  assert.match(SOURCE, /id=["']listing-verification-result["']/);
  assert.match(SOURCE, /id=["']listing-verification-loading["']/);
});

test("the listing verification section is structurally separate from the physical compliance checklist", () => {
  const listingSectionMatch = SOURCE.match(/<section[^>]*id=["']listing-verification-card["'][\s\S]*?<\/section>/);
  assert.ok(listingSectionMatch, "expected a dedicated <section id=\"listing-verification-card\">");
  const section = listingSectionMatch[0];
  assert.doesNotMatch(section, /id=["']report-findings["']/, "must not contain the physical compliance checklist");
  assert.doesNotMatch(section, /id=["']report-score-badge["']/, "must not contain the physical compliance score badge");
});

test("findings, evidence, external services, listing verification, and report actions are independent sections", () => {
  assert.match(SOURCE, /id=["']report-findings["']/);
  assert.match(SOURCE, /id=["']source-evidence-viewer["']/);
  assert.match(SOURCE, /Evidence ID' \+ \(f\.regionIds\.length === 1/);
  const experimentalStart = SOURCE.lastIndexOf('<details', SOURCE.indexOf('id="experimental-evidence-card"'));
  const experimentalEnd = SOURCE.indexOf('</details>', experimentalStart);
  const externalStart = SOURCE.lastIndexOf('<section', SOURCE.indexOf('id="external-services-card"'));
  const listingStart = SOURCE.lastIndexOf('<section', SOURCE.indexOf('id="listing-verification-card"'));
  const actionsStart = SOURCE.lastIndexOf('<section', SOURCE.indexOf('id="report-actions"'));
  assert.ok(experimentalStart >= 0 && experimentalEnd > experimentalStart);
  assert.ok(externalStart > experimentalEnd && listingStart > externalStart && actionsStart > listingStart);
  const experimental = SOURCE.slice(experimentalStart, experimentalEnd + '</details>'.length);
  assert.doesNotMatch(experimental, /id=["'](?:external-services-card|listing-verification-card|report-actions)["']/);
  assert.match(SOURCE.slice(externalStart, listingStart), /id=["']external-services-content["']/);
  assert.match(SOURCE.slice(listingStart, actionsStart), /id=["']listing-verification-result["']/);
  assert.match(SOURCE, /externalStatusContainer = document\.getElementById\('external-services-content'\)/);
  assert.doesNotMatch(SOURCE, /externalStatusContainer = document\.getElementById\('experimental-evidence-content'\)/);
});

test("main report actions expose PDF and DOC exports outside collapsible review content", () => {
  const actions = SOURCE.match(/<section[^>]*id=["']report-actions["'][\s\S]*?<\/section>/);
  assert.ok(actions, "expected a standalone Report Actions section");
  assert.match(actions[0], /id=["']export-pdf-btn["']/);
  assert.match(actions[0], /id=["']export-doc-btn["']/);
  assert.match(actions[0], /class="no-print/);
  assert.doesNotMatch(actions[0], /<details|id=["']experimental-evidence-card["']/);
  assert.match(SOURCE, /document\.getElementById\('export-pdf-btn'\)\.addEventListener/);
  assert.match(SOURCE, /document\.getElementById\('export-doc-btn'\)\.addEventListener/);
  assert.match(SOURCE, /populateExportDetails\(officialName\)[\s\S]*?scan\.compliance && scan\.compliance\.rule_results/);
});

test("exports use only the selected report's image, OCR, identity, and compliance", () => {
  assert.match(SOURCE, /sourceImageUrl: activeSource && activeSource\.url/);
  assert.match(SOURCE, /reportId: activeReport\.reportId/);
  assert.match(SOURCE, /detections: activeReport\.ocr && activeReport\.ocr\.detections \|\| \[\]/);
  assert.match(SOURCE, /compliance: activeReport\.compliance/);
  assert.match(SOURCE, /document\.getElementById\('export-image'\)\.src = sourceImage\.src/);
  assert.match(SOURCE, /link\.download = 'LegalLens-' \+ \(scan\.reportId \|\| scan\.id/);
  assert.doesNotMatch(SOURCE.match(/function populateExportDetails\(officialName\) \{[\s\S]*?\n\s*\}/)[0], /listingVerification|experimental-evidence/);
});

test("listing client sends report_id without changing backend URL safety", () => {
  assert.match(OCR_CLIENT, /function verifyListing\(scanId, url, baseUrl, reportId\)/);
  assert.match(OCR_CLIENT, /JSON\.stringify\(\{ url: url, report_id: reportId \|\| undefined \}\)/);
  assert.match(OCR_CLIENT, /function getListingVerification\(scanId, baseUrl, reportId\)/);
  assert.match(OCR_CLIENT, /endpoint \+= "\?report_id=" \+ encodeURIComponent\(reportId\)/);
});

test("displays a disclaimer distinguishing this from a legal compliance determination", () => {
  assert.match(SOURCE, /id=["']listing-verification-disclaimer["']/);
  assert.match(SOURCE, /does not determine legal compliance/i);
});

test("calls LLOcr.verifyListing with the selected report identity and entered URL", () => {
  assert.match(SOURCE, /LLOcr\.verifyListing\(\s*scan\.id\s*,\s*url\s*,\s*undefined\s*,\s*scan\.reportId\s*\)/);
  assert.match(SOURCE, /listingVerification: activeReport\.listingVerification \|\| null/);
  assert.match(SOURCE, /if \(requestedReportId && !activeReport\)[\s\S]*?report-actions[\s\S]*?return;/);
});

test("empty, malformed, and obvious private URLs are rejected before the API call", () => {
  const emptyCheck = SOURCE.indexOf("if (!url)");
  const malformedCheck = SOURCE.indexOf("var validationError = listingUrlError(url)");
  const verifyCall = SOURCE.indexOf("LLOcr.verifyListing(scan.id, url");
  assert.ok(emptyCheck >= 0 && emptyCheck < verifyCall);
  assert.ok(malformedCheck >= 0 && malformedCheck < verifyCall);
  assert.match(SOURCE, /parsed\.protocol !== 'http:' && parsed\.protocol !== 'https:'/);
  assert.match(SOURCE, /host === 'localhost' \|\| host\.endsWith\('\.localhost'\)/);
  assert.match(SOURCE, /first === 127[\s\S]*?first === 192 && second === 168/);
  assert.doesNotMatch(SOURCE, /example-marketplace\.com/);
});

test("listing failures remain supplementary and do not modify compliance", () => {
  assert.match(SOURCE, /function renderListingFailure\(message\)/);
  assert.match(SOURCE, /Listing verification unavailable \/ failed/);
  assert.match(SOURCE, /SUPPLEMENTARY EVIDENCE · Retrieval SUCCESS/);
  assert.match(SOURCE, /verifyBtn\.disabled = true[\s\S]*?\.finally\(function \(\)/);
  const listingStart = SOURCE.indexOf("// --- Phase 2A: E-commerce Listing Verification");
  const listingEnd = SOURCE.indexOf("})();", listingStart);
  assert.ok(listingStart >= 0 && listingEnd > listingStart);
  assert.doesNotMatch(SOURCE.slice(listingStart, listingEnd), /scan\.compliance\s*=/);
});

test("every dynamic listing/comparison value rendered into innerHTML is escaped via LL.escapeHtml", () => {
  const renderFnMatch = SOURCE.match(/function renderListingResult\(result\) \{[\s\S]*?\n {16}\}/);
  assert.ok(renderFnMatch, "expected to find renderListingResult's function body");
  const body = renderFnMatch[0];
  // Every one of these fields is attacker-influenced (comes from a
  // retrieved third-party web page) and must never reach innerHTML raw.
  const expectedPatterns = [
    /LL\.escapeHtml\(listing\.retrieval_status/,
    /LL\.escapeHtml\(listing\.error_message/,
    /LL\.escapeHtml\(c\.label\)/,
    /LL\.escapeHtml\(c\.physical_value/,
    /LL\.escapeHtml\(c\.listing_value/,
    /LL\.escapeHtml\(c\.explanation\)/,
    /LL\.escapeHtml\(String\(e\.field \|\| 'field'\)\.replace/,
    /LL\.escapeHtml\(e\.extracted_value\)/,
    /LL\.escapeHtml\(e\.extraction_source\)/,
    /LL\.escapeHtml\(listing\.final_url \|\| listing\.source_url\)/,
    /LL\.escapeHtml\(listing\.product_name\)/,
    /LL\.escapeHtml\(result\.price_note\)/,
  ];
  for (const pattern of expectedPatterns) {
    assert.match(body, pattern, `expected to find ${pattern} wrapping a dynamic value in LL.escapeHtml(...) before reaching innerHTML`);
  }
});

test("never uses innerHTML to inject the raw retrieved URL without escaping (href attribute breakout guard)", () => {
  assert.doesNotMatch(SOURCE, /href="'\s*\+\s*listing\.final_url/, "final_url must not be concatenated into an href unescaped");
  assert.doesNotMatch(SOURCE, /href="'\s*\+\s*listing\.source_url/, "source_url must not be concatenated into an href unescaped");
});

test("renders a persisted scan.listingVerification result on page load without requiring a fresh request", () => {
  assert.match(SOURCE, /if \(scan\.listingVerification\)\s*\{\s*\n\s*renderListingResult\(scan\.listingVerification\);/);
});

test("Phase 1 statutory disclaimer element is still present and unrelated to the listing disclaimer", () => {
  assert.match(SOURCE, /id=["']disclaimer["']/);
  const disclaimerIndex = SOURCE.indexOf('id="disclaimer"');
  const listingDisclaimerIndex = SOURCE.indexOf('id="listing-verification-disclaimer"');
  assert.notEqual(disclaimerIndex, -1);
  assert.notEqual(listingDisclaimerIndex, -1);
  assert.notEqual(disclaimerIndex, listingDisclaimerIndex);
});

test("inline report scripts remain syntactically valid", () => {
  const scripts = [...SOURCE.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((body) => body.trim());
  assert.ok(scripts.length > 0);
  for (const body of scripts) new Function(body);
});
