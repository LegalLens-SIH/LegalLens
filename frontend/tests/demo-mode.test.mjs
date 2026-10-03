import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function loadScripts() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const appJs = fs.readFileSync(path.join(root, 'assets', 'app.js'), 'utf8');
  const demoJs = fs.readFileSync(path.join(root, 'assets', 'demo-data.js'), 'utf8');

  const storage = {};
  const document = {
    readyState: 'complete',
    body: { appendChild() {}, insertBefore() {}, firstChild: null },
    head: { appendChild() {} },
    documentElement: { classList: { add() {} } },
    getElementById() { return null; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
    createElement() {
      return {
        setAttribute() {},
        classList: { add() {}, remove() {}, toggle() {} },
        querySelectorAll() { return []; },
        querySelector() { return null; },
        appendChild() {},
        addEventListener() {},
        style: {},
        textContent: '',
        innerHTML: ''
      };
    }
  };

  const context = {
    window: {},
    document,
    localStorage: {
      getItem(key) { return storage[key] ?? null; },
      setItem(key, value) { storage[key] = String(value); },
      removeItem(key) { delete storage[key]; }
    },
    sessionStorage: {
      getItem(key) { return storage[key] ?? null; },
      setItem(key, value) { storage[key] = String(value); },
      removeItem(key) { delete storage[key]; }
    },
    URLSearchParams,
    requestAnimationFrame(fn) { fn(); },
    fetch() { return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); },
    console,
    navigator: { userAgent: 'node' },
    setTimeout,
    clearTimeout,
    Date,
    Math,
    Blob,
    URL,
    FormData,
    location: { pathname: '/official-dashboard.html', search: '', hostname: 'localhost', href: 'http://localhost/official-dashboard.html' }
  };

  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(demoJs, context);
  vm.runInContext(appJs, context);
  return context;
}

test('demo mode exposes official and manufacturer demo sessions', () => {
  const context = loadScripts();
  assert.equal(typeof context.LL.startDemoMode, 'function');
  assert.equal(typeof context.LL.isDemoMode, 'function');
  const official = context.LL.getDemoSessionByRole('official');
  const manufacturer = context.LL.getDemoSessionByRole('manufacturer');
  assert.equal(official.role, 'enforcement_official');
  assert.equal(official.demoMode, true);
  assert.equal(manufacturer.role, 'manufacturer');
  assert.equal(manufacturer.demoMode, true);
});

test('demo sessions stored in localStorage stay disabled until an explicit opt-in is enabled', () => {
  const context = loadScripts();
  const demoOfficial = { role: 'enforcement_official', demoMode: true, name: 'Demo Official', email: 'demo.official@example.com' };
  context.localStorage.setItem('legallense_demo_session_v1', JSON.stringify(demoOfficial));

  assert.equal(context.LL.getSession(), null);
  assert.equal(context.LL.getAuthState(), null);
  assert.equal(context.LL.isDemoMode(), false);
  assert.ok(Array.isArray(context.LL.getScans()));
  assert.equal(context.LL.getScans().length, 0);

  context.LL.startDemoMode('official');
  const auth = context.LL.getAuthState();

  assert.equal(auth.authenticated, true);
  assert.equal(auth.role, 'official');
  assert.equal(auth.demoMode, true);
  assert.equal(auth.session.role, 'enforcement_official');
  assert.equal(auth.session.demoMode, true);
  assert.equal(auth.session.name, 'Demo Official');
  assert.equal(auth.session.email, 'demo.official@example.com');
  assert.equal(context.LL.requireAuthenticated(), true);
});

test('canonical status takes precedence over absent score or legacy percentages', () => {
  const context = loadScripts();
  assert.equal(context.LL.complianceStatus({ status: 'manual_review', score: null, compliance: { overall_status: 'REVIEW_REQUIRED' } }).label, 'Manual Verification');
  assert.equal(context.LL.complianceStatus({ status: 'flagged', score: null, compliance: { overall_status: 'NON_COMPLIANT' } }).label, 'Non-Compliant');
  assert.equal(context.LL.complianceStatus({ status: 'compliant', score: null, compliance: { overall_status: 'PASS' } }).label, 'Compliant');
  assert.equal(context.LL.complianceStatus({ status: 'not_applicable', score: null, compliance: { overall_status: 'NOT_APPLICABLE' } }).label, 'Not Applicable');
});
