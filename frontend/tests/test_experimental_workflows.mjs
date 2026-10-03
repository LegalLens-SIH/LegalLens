import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const FRONTEND_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPORT = readFileSync(path.join(FRONTEND_DIR, "compliance-report.html"), "utf8");
const RESULT_PAGE = readFileSync(path.join(FRONTEND_DIR, "compliance-result.html"), "utf8");
const SELF_CHECK_REPORT = readFileSync(path.join(FRONTEND_DIR, "self-check-report.html"), "utf8");
const CASE_DETAILS = readFileSync(path.join(FRONTEND_DIR, "case-details.html"), "utf8");
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
  assert.match(REPORT, /detection\.symbology === 'EAN_13' && detection\.code/);
  assert.match(OCR_CLIENT, /\/api\/scans\/.*\/calibration/);
});

test("GS1 evidence renders independently and stays bound to the selected image report", () => {
  assert.match(REPORT, /id="gs1-verification-card"/);
  assert.match(REPORT, /id="gs1-verification-content"/);
  assert.match(REPORT, /gs1Verification: activeReport\.gs1Verification \|\| null/);
  assert.match(REPORT, /var gs1Result = scan\.gs1Verification \|\| null/);
  assert.match(REPORT, /evidence\.crop_url/);
  assert.match(REPORT, /Barcode region:/);
  assert.match(REPORT, /Evidence ID:/);
  assert.match(REPORT, /Image ID:/);
  assert.match(REPORT, /Report ID:/);
  const gs1Section = REPORT.match(/<section[^>]*id="gs1-verification-card"[\s\S]*?<\/section>/);
  assert.ok(gs1Section);
  assert.doesNotMatch(gs1Section[0], /id="experimental-evidence-card"|id="report-findings"/);
});

test("GS1 local-only and provider-unavailable states never claim a registry match", () => {
  assert.match(REPORT, /Local GTIN check-digit validation is not a registry lookup/);
  assert.match(REPORT, /Resolver CE performs local Digital Link resolution only/);
  assert.match(REPORT, /Resolver CE Digital Link resolution:/);
  assert.match(REPORT, /Environment: ' \+ LL\.escapeHtml\(external\.environment \|\| 'development'\)/);
  assert.match(REPORT, /Fixture: ' \+ LL\.escapeHtml\(external\.fixture_status \|\| 'not applicable'\)/);
  assert.match(REPORT, /Verified by GS1 and GS1 India DataKart are not connected/);
  assert.match(REPORT, /not official registry verification/);
  assert.doesNotMatch(REPORT, /GS1 verified product/);
});

test("PDF and DOC exports include report-scoped GS1 supporting evidence, separate from compliance rules", () => {
  assert.match(REPORT, /id="export-gs1-evidence"/);
  assert.match(REPORT, /var gs1 = scan\.gs1Verification/);
  assert.match(REPORT, /Supporting identity evidence only/);
  assert.match(REPORT, /Crop SHA-256:/);
  assert.match(REPORT, /Barcode region:/);
  assert.match(REPORT, /Fixture: ' \+ exportEscape\(\(barcode\.external_verification \|\| \{\}\)\.fixture_status/);
  assert.match(REPORT, /document\.getElementById\('export-pdf-btn'\)\.addEventListener/);
  assert.match(REPORT, /document\.getElementById\('export-doc-btn'\)\.addEventListener/);
  assert.match(REPORT, /var rules = scan\.compliance && scan\.compliance\.rule_results/);
});

test("report and exports expose the JSON-driven 75-rule evaluation with trace and evidence", () => {
  assert.match(REPORT, /id="json-rules-evaluation-card"/);
  assert.match(REPORT, /id="json-rules-results"/);
  assert.match(REPORT, /id="json-rules-gates"/);
  assert.match(REPORT, /data_driven_evaluation/);
  assert.match(REPORT, /rule_3_chapter_ii_scope/);
  assert.match(REPORT, /rule_26_package_exemption/);
  assert.match(REPORT, /total_rules_with_executable_checks/);
  assert.match(REPORT, /validation_warnings/);
  assert.match(REPORT, /Rule trace/);
  assert.match(REPORT, /highlightEvidenceRegions\(ids, rule && rule\.rule_id/);
  assert.match(REPORT, /Consolidated 75-Rule JSON Evaluation/);
  assert.match(REPORT, /source_sha256/);
  assert.match(REPORT, /NOT_ASSESSED/);
  assert.match(REPORT, /Legacy Rule 6 Findings \(Reference Only\)/);
  assert.match(REPORT, /Legacy profile score \(not a legal percentage\)/);
  assert.match(REPORT, /Canonical Result:/);
  assert.match(REPORT, /scan\.compliance\.overall_status/);
  assert.match(REPORT, /Canonical compliance status/);
});

test("compliance result page presents canonical JSON rules and labels legacy output as reference only", () => {
  assert.match(RESULT_PAGE, /Canonical 75-rule JSON evaluation/);
  assert.match(RESULT_PAGE, /result\.data_driven_evaluation/);
  assert.match(RESULT_PAGE, /rule\.category/);
  assert.match(RESULT_PAGE, /rule\.title/);
  assert.match(RESULT_PAGE, /traceValue\(step\.actual\)/);
  assert.match(RESULT_PAGE, /item\.source_text/);
  assert.match(RESULT_PAGE, /item\.ocr_confidence/);
  assert.match(RESULT_PAGE, /item\.extraction_confidence/);
  assert.match(RESULT_PAGE, /Evidence IDs:/);
  assert.match(RESULT_PAGE, /Legacy profile findings \(reference only\)/);
  assert.match(RESULT_PAGE, /not a legal-compliance percentage/);
});

test("manufacturer self-check report presents canonical JSON rules before legacy reference findings", () => {
  assert.match(SELF_CHECK_REPORT, /Canonical 75-Rule JSON Evaluation/);
  assert.match(SELF_CHECK_REPORT, /compliance\.data_driven_evaluation/);
  assert.match(SELF_CHECK_REPORT, /canonical-json-summary/);
  assert.match(SELF_CHECK_REPORT, /item\.source_text/);
  assert.match(SELF_CHECK_REPORT, /item\.extraction_confidence/);
  assert.match(SELF_CHECK_REPORT, /Legacy Profile Findings \(Reference Only\)/);
});

test("official case detail presents canonical JSON findings before legacy profile references", () => {
  assert.match(CASE_DETAILS, /Canonical 75-Rule JSON Evaluation/);
  assert.match(CASE_DETAILS, /compliance\.data_driven_evaluation/);
  assert.match(CASE_DETAILS, /case-json-summary/);
  assert.match(CASE_DETAILS, /Legacy profile findings \(reference only\)/);
  assert.match(CASE_DETAILS, /item\.source_text/);
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