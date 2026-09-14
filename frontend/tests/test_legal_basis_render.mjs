// Focused tests for the Phase 7 (T032-T035) frontend Legal Basis display
// (specs/001-legal-rag). Plain Node, no framework/build step - matches this
// repo's plain-static-HTML frontend (no package.json, no bundler). Run with:
//   node frontend/tests/test_legal_basis_render.mjs
//
// Exercises frontend/assets/compliance-render.js's flattenChecks() and
// renderLegalBasisInto(), and frontend/assets/legal-basis-client.js's get(),
// against a minimal hand-rolled DOM shim (no jsdom dependency available/
// needed for these two pure-rendering functions).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ASSETS_DIR = path.join(__dirname, "..", "assets");

// --- Minimal DOM shim --------------------------------------------------
// Just enough for compliance-render.js/legal-basis-client.js: element
// creation, className, textContent, innerHTML (stored verbatim, not
// parsed - these tests assert on the *data* passed into rendering, not on
// re-parsing HTML strings), and appendChild/children for structural checks.

class FakeElement {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this._className = "";
    this._innerHTML = "";
    this._textContent = "";
    this.style = {};
  }
  get className() { return this._className; }
  set className(v) { this._className = v; }
  set textContent(v) { this._textContent = String(v); this.children = []; this._innerHTML = ""; }
  get textContent() {
    if (this._innerHTML || this.children.length) {
      return this.children.map((c) => c.textContent).join("") || this._textContent;
    }
    return this._textContent;
  }
  set innerHTML(v) { this._innerHTML = String(v); this.children = []; }
  get innerHTML() { return this._innerHTML; }
  appendChild(child) { this.children.push(child); return child; }
  querySelector() { return null; }
}

function makeFakeDocument() {
  return {
    createElement: (tag) => new FakeElement(tag),
    getElementById: () => null,
    head: new FakeElement("head"),
  };
}

function loadComplianceRender() {
  const code = readFileSync(path.join(ASSETS_DIR, "compliance-render.js"), "utf8");
  const fakeWindow = {};
  fakeWindow.document = makeFakeDocument();
  fakeWindow.window = fakeWindow;
  vm.createContext(fakeWindow);
  vm.runInContext(code, fakeWindow, { filename: "compliance-render.js" });
  return fakeWindow.LLComplianceRender;
}

function loadLegalBasisClient(fetchImpl) {
  const code = readFileSync(path.join(ASSETS_DIR, "legal-basis-client.js"), "utf8");
  const fakeWindow = { location: { hostname: "localhost" } };
  fakeWindow.window = fakeWindow;
  fakeWindow.fetch = fetchImpl;
  vm.createContext(fakeWindow);
  vm.runInContext(code, fakeWindow, { filename: "legal-basis-client.js" });
  return fakeWindow.LLLegalBasis;
}

// --- Fixtures ------------------------------------------------------------

const FOUND_RESULT = {
  rule_id: "LMPC-R6-MANDATORY-DECLARATIONS",
  field: "maximum_retail_price_mrp",
  status: "found",
  retrieval_method: "deterministic_link",
  provisions: [
    {
      provision_id: "LMPC-2011-R6-1-E",
      rule_sub_rule_clause: "Rule 6(1)(e)",
      text: "the retail sale price of the package...",
      effective_status: "original",
      is_currently_effective: true,
      source: {
        source_id: "LMPC-2011",
        title: "Legal Metrology (Packaged Commodities) Rules, 2011",
        citation: "G.S.R. 629(E)",
        version: "as amended up to 2017",
        acquired_at: "2026-01-01",
        is_amendment: false,
        amends_source_id: null,
        verification_note: "verified against official gazette text",
      },
    },
  ],
};

const NOT_AVAILABLE_RESULT = { rule_id: "LMPC-R6-MANDATORY-DECLARATIONS", field: "unit_sale_price", status: "not_available", retrieval_method: null, provisions: [] };

const COMPLIANCE = {
  overall_status: "NON_COMPLIANT",
  compliance_score: 62,
  rule_results: [
    {
      rule_id: "LMPC-R6-MANDATORY-DECLARATIONS",
      rule_name: "Mandatory Declarations",
      status: "PARTIAL",
      required_fields: {
        maximum_retail_price_mrp: { status: "PASS", value: "Rs. 99", confidence: 0.95, explanation: "Found MRP." },
        unit_sale_price: { status: "NOT_DETECTED", value: null, confidence: null, explanation: "Not found." },
      },
    },
  ],
};

// --- 1. Legal basis renders when available --------------------------------

test("legal basis renders when available", () => {
  const R = loadComplianceRender();
  const container = new FakeElement("div");
  R.renderLegalBasisInto(container, FOUND_RESULT);
  assert.match(container.className, /ll-legal-basis\b/);
  const list = container.children.find((c) => c.tagName === "UL");
  assert.ok(list, "expected a provisions list to be rendered");
  assert.equal(list.children.length, 1);
});

// --- 2. Rule/citation/source information renders correctly ----------------

test("rule/sub-rule clause, verified text, and source citation render correctly", () => {
  const R = loadComplianceRender();
  const container = new FakeElement("div");
  R.renderLegalBasisInto(container, FOUND_RESULT);
  const list = container.children.find((c) => c.tagName === "UL");
  const item = list.children[0];
  assert.match(item.innerHTML, /Rule 6\(1\)\(e\)/);
  assert.match(item.innerHTML, /the retail sale price of the package/);
  assert.match(item.innerHTML, /Legal Metrology \(Packaged Commodities\) Rules, 2011/);
  assert.match(item.innerHTML, /G\.S\.R\. 629\(E\)/);
  assert.match(item.innerHTML, /as amended up to 2017/);
});

// --- 3. Missing legal basis renders safely ---------------------------------

test("missing legal basis renders the safe unavailable state, not an error", () => {
  const R = loadComplianceRender();
  const container = new FakeElement("div");
  assert.doesNotThrow(() => R.renderLegalBasisInto(container, NOT_AVAILABLE_RESULT));
  const unavailable = container.children.find((c) => c.className === "ll-legal-basis-unavailable");
  assert.ok(unavailable, "expected the unavailable message element");
  assert.match(unavailable.textContent, /unavailable|not verified/i);
  assert.ok(!container.children.some((c) => c.tagName === "UL"), "no provisions list should render");
});

// --- 4. Unresolved unit_sale_price never shows fabricated legal text ------

test("unresolved unit_sale_price never displays fabricated legal text", () => {
  const R = loadComplianceRender();
  const container = new FakeElement("div");
  R.renderLegalBasisInto(container, NOT_AVAILABLE_RESULT);
  const serialized = JSON.stringify(container);
  // No provision text/citation vocabulary anywhere in the rendered output -
  // proves the renderer did not invent a substitute provision.
  assert.doesNotMatch(serialized, /Rule \d/);
  assert.doesNotMatch(serialized, /retail sale price/i);
});

test("renderLegalBasisInto never invents provisions for a malformed/empty backend response", () => {
  const R = loadComplianceRender();
  for (const bad of [null, undefined, {}, { status: "found" }, { status: "found", provisions: [] }, { status: "found", provisions: "not-an-array" }]) {
    const container = new FakeElement("div");
    R.renderLegalBasisInto(container, bad);
    assert.ok(!container.children.some((c) => c.tagName === "UL"), "malformed input must never render a provisions list");
  }
});

// --- 5. Legal Basis cannot modify compliance status/score ------------------

test("rendering legal basis cannot modify a compliance status/score element elsewhere in the DOM", () => {
  const R = loadComplianceRender();
  const statusChip = new FakeElement("span");
  statusChip.className = "status-chip";
  statusChip.textContent = "NON-COMPLIANT";
  const scoreEl = new FakeElement("p");
  scoreEl.textContent = "62%";
  const before = { status: statusChip.textContent, score: scoreEl.textContent };

  const legalBasisContainer = new FakeElement("div");
  R.renderLegalBasisInto(legalBasisContainer, FOUND_RESULT);

  // renderLegalBasisInto's only parameters are (container, result) - it was
  // never given statusChip/scoreEl, so it has no reference through which it
  // could mutate them. Assert they are byte-for-byte unchanged.
  assert.deepEqual({ status: statusChip.textContent, score: scoreEl.textContent }, before);
});

// --- 6. Official decision UI remains separate -------------------------------

test("rendering legal basis never touches an officialDecision DOM element", () => {
  const R = loadComplianceRender();
  const officialDecisionEl = new FakeElement("div");
  officialDecisionEl.className = "official-decision-panel";
  officialDecisionEl.textContent = "APPROVED";
  const snapshot = officialDecisionEl.textContent;

  const legalBasisContainer = new FakeElement("div");
  R.renderLegalBasisInto(legalBasisContainer, FOUND_RESULT);
  R.renderLegalBasisInto(legalBasisContainer, NOT_AVAILABLE_RESULT);

  assert.equal(officialDecisionEl.textContent, snapshot);
  assert.ok(!legalBasisContainer.className.includes("official"), "legal basis container must never carry official-decision styling");
});

// --- 7. Existing compliance rendering unchanged when legal basis unavailable

test("flattenChecks output is unchanged (only additively gains ruleId) so existing rendering is unaffected", () => {
  const R = loadComplianceRender();
  const checks = R.flattenChecks(COMPLIANCE);
  assert.equal(checks.length, 2);
  const mrp = checks.find((c) => c.key === "maximum_retail_price_mrp");
  const usp = checks.find((c) => c.key === "unit_sale_price");
  assert.ok(mrp && usp);
  // Every field the existing renderers (case-details.html, self-check-report.html)
  // already depend on is still present, unchanged.
  for (const check of [mrp, usp]) {
    assert.ok("label" in check && "status" in check && "value" in check && "confidence" in check && "explanation" in check && "aiAssisted" in check);
  }
  // The only addition is ruleId - additive, does not remove/rename anything.
  assert.equal(mrp.ruleId, "LMPC-R6-MANDATORY-DECLARATIONS");
  assert.equal(usp.ruleId, "LMPC-R6-MANDATORY-DECLARATIONS");
});

// --- legal-basis-client.js: fails safe on every error mode -----------------

// Results come out of a separate vm context (a different realm), so
// assert.deepEqual/deepStrictEqual's prototype-identity check spuriously
// fails on structurally-identical plain objects/arrays; round-tripping
// through JSON normalizes both sides to this realm's Object/Array first.
function toPlain(value) { return JSON.parse(JSON.stringify(value)); }

test("LLLegalBasis.get resolves to not_available (never rejects) on a network error", async () => {
  const client = loadLegalBasisClient(() => Promise.reject(new Error("network down")));
  const result = await client.get("LMPC-R6-MANDATORY-DECLARATIONS", "unit_sale_price");
  assert.deepEqual(toPlain(result), { status: "not_available", retrieval_method: null, provisions: [] });
});

test("LLLegalBasis.get resolves to not_available on a non-2xx response", async () => {
  const client = loadLegalBasisClient(() => Promise.resolve({ ok: false, status: 422, json: () => Promise.resolve({ detail: "Unknown rule_id" }) }));
  const result = await client.get("NOT-A-REAL-RULE-ID");
  assert.equal(result.status, "not_available");
  assert.deepEqual(toPlain(result.provisions), []);
});

test("LLLegalBasis.get resolves to not_available on malformed JSON", async () => {
  const client = loadLegalBasisClient(() => Promise.resolve({ ok: true, json: () => Promise.reject(new Error("bad json")) }));
  const result = await client.get("LMPC-R6-MANDATORY-DECLARATIONS");
  assert.equal(result.status, "not_available");
});

test("LLLegalBasis.get passes through a well-formed found response unchanged", async () => {
  const client = loadLegalBasisClient(() => Promise.resolve({ ok: true, json: () => Promise.resolve(FOUND_RESULT) }));
  const result = await client.get("LMPC-R6-MANDATORY-DECLARATIONS", "maximum_retail_price_mrp");
  assert.deepEqual(result, FOUND_RESULT);
});
