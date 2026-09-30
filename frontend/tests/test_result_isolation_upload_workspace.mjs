import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const frontendRoot = path.resolve(import.meta.dirname, '..');
const root = path.resolve(frontendRoot, '..');
const report = fs.readFileSync(path.join(frontendRoot, 'compliance-report.html'), 'utf8');
const upload = fs.readFileSync(path.join(frontendRoot, 'new-compliance-scan.html'), 'utf8');
const history = fs.readFileSync(path.join(frontendRoot, 'scan-history.html'), 'utf8');
const backend = fs.readFileSync(path.join(root, 'backend', 'api', 'ocr.py'), 'utf8');

test('reports select image-scoped report IDs and preserve scan identity', () => {
  assert.match(report, /report-selector/);
  assert.match(report, /reportRecords/);
  assert.match(report, /report\.reportId/);
  assert.match(report, /Image-specific reports/);
});

test('multi-image backend persists independent report and image identities', () => {
  assert.match(backend, /per_image_payloads/);
  assert.match(backend, /scan\["reports"\]\s*=\s*per_image_payloads/);
  assert.match(backend, /get_scan_report/);
  assert.match(backend, /evidenceScope.*image_only/);
});

test('upload workspace uses compact OCR setting and no duplicate selected-card title', () => {
  assert.match(upload, /id="preview-card"/);
  assert.match(upload, /Product Images/);
  assert.match(upload, /id="ocr-mode-select"/);
  assert.doesNotMatch(upload, /Selected Product Views/);
  assert.match(upload, /scan-drop-zone.*hidden/);
});

test('history opens an individual report when a scan has multiple reports', () => {
  assert.match(history, /scan\.reports/);
  assert.match(history, /report\.reportId/);
  assert.match(history, /[?&]report=/);
});
