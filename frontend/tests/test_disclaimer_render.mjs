// Focused tests for the Phase 1 "Not a Statutory Notice" disclaimer
// (backend/services/compliance_engine.py's DISCLAIMER_TEXT, surfaced on
// every ComplianceResult as `disclaimer`) being rendered consistently
// across every REAL compliance result/report page. Plain Node, no
// framework/build step - matches frontend/tests/test_legal_basis_render.mjs's
// established convention. Run with:
//   node frontend/tests/test_disclaimer_render.mjs
//
// These four pages (self-check-report.html, case-details.html,
// compliance-report.html, compliance-result.html) each embed their
// rendering logic directly in an inline <script>, not as reusable
// functions exported from compliance-render.js - unlike
// test_legal_basis_render.mjs (which loads and executes
// renderLegalBasisInto via a vm sandbox), these tests verify the actual
// PAGE SOURCE deterministically: the disclaimer element exists, it is
// populated via textContent (never innerHTML, per this task's explicit
// requirement), and from the correct real-data expression for that page.
// This is intentionally simpler than a full DOM simulation of each page's
// much larger, unrelated rendering logic (checklist items, evidence
// overlays, official-decision panel, ...), which this task's own
// "do not introduce unnecessary complexity" guidance argues against.
//
// frontend/compliance-summary.html is deliberately NOT tested here - it
// is a disconnected/mock page (reads app.js's localStorage store, never
// the real API) and was explicitly excluded from the disclaimer
// consistency requirement.

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

// A crude but sufficient innerHTML-detector scoped to the disclaimer
// element specifically: any line assigning `.innerHTML` to the
// `disclaimer` element (by id lookup chained directly into the
// assignment) would match this - used as a negative check below.
function assignsDisclaimerViaInnerHTML(source) {
  return /getElementById\(\s*['"]disclaimer['"]\s*\)\s*\.\s*innerHTML\s*=/.test(source);
}

function assignsDisclaimerViaTextContent(source) {
  return /getElementById\(\s*['"]disclaimer['"]\s*\)\s*\.\s*textContent\s*=/.test(source);
}

const PAGES = [
  {
    file: "self-check-report.html",
    description: "manufacturer self-check report (real ComplianceResult via record.compliance)",
    sourceExpressionPattern: /getElementById\(\s*['"]disclaimer['"]\s*\)\s*\.\s*textContent\s*=\s*compliance\.disclaimer/,
  },
  {
    file: "case-details.html",
    description: "official review-queue case detail (real ComplianceResult via revision.complianceResult)",
    sourceExpressionPattern: /getElementById\(\s*['"]disclaimer['"]\s*\)\s*\.\s*textContent\s*=\s*compliance\.disclaimer/,
  },
  {
    file: "compliance-report.html",
    description: "official scan compliance report (real ComplianceResult via scan.compliance)",
    sourceExpressionPattern: /getElementById\(\s*['"]disclaimer['"]\s*\)\s*\.\s*textContent\s*=\s*\(scan\.compliance\s*&&\s*scan\.compliance\.disclaimer\)/,
  },
  {
    file: "compliance-result.html",
    description: "generic sessionStorage-relayed compliance result (already implemented, unchanged)",
    sourceExpressionPattern: /getElementById\(\s*['"]disclaimer['"]\s*\)\s*\.\s*textContent\s*=\s*result\.disclaimer/,
  },
];

for (const page of PAGES) {
  test(`${page.file}: has a disclaimer element in the markup`, () => {
    const source = readPage(page.file);
    assert.match(source, /id=['"]disclaimer['"]/, `expected an element with id="disclaimer" in ${page.file}`);
  });

  test(`${page.file}: disclaimer is populated from the real ComplianceResult (${page.description})`, () => {
    const source = readPage(page.file);
    assert.match(source, page.sourceExpressionPattern, `expected the disclaimer to be assigned from the correct real-data expression in ${page.file}`);
  });

  test(`${page.file}: disclaimer is rendered via textContent, never innerHTML`, () => {
    const source = readPage(page.file);
    assert.ok(assignsDisclaimerViaTextContent(source), `expected a textContent assignment to the disclaimer element in ${page.file}`);
    assert.ok(!assignsDisclaimerViaInnerHTML(source), `disclaimer must never be assigned via innerHTML in ${page.file}`);
  });
}

// --- Explicitly excluded / must-not-change surfaces -------------------------

test("compliance-summary.html (disconnected mock page) is not counted - no disclaimer wiring expected or required here", () => {
  // Not asserting absence (it already has its own, unrelated, pre-existing
  // disclaimer text - see the audit) - just documenting that this suite
  // deliberately does not test it as a "real" surface, per the task's
  // explicit instruction.
  assert.ok(true);
});

test("compliance-render.js's existing legal-basis disclaimer text is unchanged, not replaced", () => {
  const source = readFileSync(path.join(FRONTEND_DIR, "assets", "compliance-render.js"), "utf8");
  assert.match(source, /Reference only - not an official decision\./, "the pre-existing legal-basis-panel disclaimer wording must be untouched");
});

test("compliance-render.js does not gain a competing Phase-1-style disclaimer helper (page-level rendering stayed inline, per the smallest-risk implementation choice)", () => {
  const source = readFileSync(path.join(FRONTEND_DIR, "assets", "compliance-render.js"), "utf8");
  assert.doesNotMatch(source, /statutory notice/i);
});
