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

test("displays a disclaimer distinguishing this from a legal compliance determination", () => {
  assert.match(SOURCE, /id=["']listing-verification-disclaimer["']/);
  assert.match(SOURCE, /does not determine legal compliance/i);
});

test("calls LLOcr.verifyListing with the scan id and entered URL", () => {
  assert.match(SOURCE, /LLOcr\.verifyListing\(\s*scan\.id\s*,\s*url\s*\)/);
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
    /LL\.escapeHtml\(e\.field\.replace/,
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
