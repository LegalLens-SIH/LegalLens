import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const FRONTEND_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPORT = readFileSync(path.join(FRONTEND_DIR, "compliance-report.html"), "utf8");
const OCR_CLIENT = readFileSync(path.join(FRONTEND_DIR, "assets", "ocr-client.js"), "utf8");
const NEW_SCAN = readFileSync(path.join(FRONTEND_DIR, "new-compliance-scan.html"), "utf8");

test("report exposes real TSA and e-Maap actions against stored evidence packages", () => {
  assert.match(REPORT, /LLOcr\.timestampEvidencePackage\(entry\.id\)/);
  assert.match(REPORT, /LLOcr\.transmitEvidencePackage\(entry\.id\)/);
  assert.match(REPORT, /cryptographically verified token/);
  assert.match(REPORT, /acknowledgement not verified/);
  assert.match(OCR_CLIENT, /\/api\/evidence-packages\/.*\/timestamp/);
  assert.match(OCR_CLIENT, /\/api\/em-maap\/transmit-package\//);
});

test("barcode and calibration controls require checksum-validated decoder evidence and a reference file", () => {
  assert.match(REPORT, /detection\.checksum_valid === true/);
  assert.match(REPORT, /id="calibration-reference-file" type="file"/);
  assert.match(REPORT, /crypto\.subtle\.digest\('SHA-256', buffer\)/);
  assert.match(REPORT, /reference_file_base64: btoa\(binary\)/);
  assert.match(REPORT, /reference_verification_status: 'verified'/);
  assert.match(OCR_CLIENT, /\/api\/scans\/.*\/calibration/);
});

test("calibration shows provider/API failures without changing compliance", () => {
  assert.match(REPORT, /Calibration result unavailable/);
  assert.match(REPORT, /No compliance decision is made from these measurements/);
  assert.match(REPORT, /Calibration request failed:/);
  assert.match(REPORT, /response\.data && response\.data\.detail/);
});

test("report exposes authenticity and source-image integrity evidence as supporting data", () => {
  assert.match(REPORT, /scan\.panelAuthenticity/);
  assert.match(REPORT, /scan\.authenticity/);
  assert.match(REPORT, /difference SHA-256/);
  assert.match(REPORT, /Source image SHA-256/);
  assert.match(REPORT, /neither establishes authenticity or changes compliance/);
});

test("report exposes capture metrics and alignment guidance without making compliance decisions", () => {
  assert.match(REPORT, /scan\.captureAssessments/);
  assert.match(REPORT, /quality\.blur_score/);
  assert.match(REPORT, /quality\.glare_score/);
  assert.match(REPORT, /skew\.angle_degrees/);
  assert.match(REPORT, /quality\.text_visibility/);
  assert.match(REPORT, /textVisibility\.evidence_regions/);
  assert.match(REPORT, /Keep the package face centered, flat, and parallel to the camera/);
  assert.match(REPORT, /Capture metrics guide image review only; they do not determine compliance/);
});

test("single- and multi-image scans use the asynchronous job lifecycle", () => {
  assert.match(NEW_SCAN, /LLOcr\.startAnalysisJob\(chosenFiles/);
  assert.doesNotMatch(NEW_SCAN, /LLOcr\.createScan\(chosenFile/);
  assert.match(OCR_CLIENT, /formData\.append\("ocr_mode", metadata\.ocrMode \|\| "standard"\)/);
});

test("e-Maap evidence status links to the official portal without claiming API availability", () => {
  assert.match(REPORT, /https:\/\/emaap\.gov\.in\/about_us/);
  assert.match(REPORT, /no verified acknowledgement recorded/);
});

test("report distinguishes registered physical references and uncertainty from compliance", () => {
  assert.match(REPORT, /scan\.physicalReferenceCalibrations/);
  assert.match(REPORT, /scale_uncertainty_px_per_mm/);
  assert.match(REPORT, /Registered physical-reference calibration · review only/);
});

test("timestamp report exposes provider, generation time, request digest, and token hash only", () => {
  assert.match(REPORT, /data-tsa-details/);
  assert.match(REPORT, /tsaResult\.tsa_url/);
  assert.match(REPORT, /tsaResult\.timestamp/);
  assert.match(REPORT, /tsaResult\.token_sha256/);
  assert.doesNotMatch(REPORT, /token_der_base64/);
  assert.doesNotMatch(REPORT, /request_der_base64/);
});

test("PDP evidence keeps model and dataset provenance visible as experimental", () => {
  assert.match(REPORT, /result\.model_hash/);
  assert.match(REPORT, /result\.dataset_version/);
  assert.match(REPORT, /result\.dataset_hash/);
  assert.match(REPORT, /result\.validation_state/);
  assert.match(REPORT, /PDP region candidates · experimental/);
});