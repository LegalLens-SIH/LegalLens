/* Shared rendering helpers for a ComplianceResult (backend/models/
   compliance.py), extracted so self-check-report.html, compliance-report.html
   and case-details.html (the official case-review page) render the SAME
   field labels/status vocabulary/evidence boxes from one place instead of
   three forked copies. Pure rendering only - never calls the backend. */
(function (global) {
  "use strict";

  // Covers every FieldStatus (PASS/FAIL/PARTIAL/NEEDS_MANUAL_VERIFICATION/
  // NOT_DETECTED) and every rule/overall ComplianceStatus the engine can
  // return - collapsed to a three-state vocabulary (passed/failed/review)
  // plus notApplicable, matching self-check-report.html's original mapping.
  var FIELD_LABELS = {
    manufacturer_packer_importer_details: "Manufacturer / Packer / Importer Details",
    name_and_address_of_manufacturer_or_packer: "Name and Address of Manufacturer or Packer",
    country_of_origin: "Country of Origin",
    common_generic_name_of_commodity: "Common / Generic Name of Commodity",
    net_quantity: "Net Quantity",
    month_and_year_of_manufacture_or_packing: "Month & Year of Manufacture / Packing",
    maximum_retail_price_mrp: "Maximum Retail Price (MRP)",
    unit_sale_price: "Unit Sale Price",
    consumer_care_details: "Consumer Care Details",
  };

  function fieldLabel(key) {
    return FIELD_LABELS[key] || String(key || "").replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  var STATUS_META = {
    PASS: { label: "PASS", icon: "check_circle", chip: "bg-secondary/10 text-secondary", bucket: "passed" },
    COMPLIANT: { label: "PASS", icon: "check_circle", chip: "bg-secondary/10 text-secondary", bucket: "passed" },
    FAIL: { label: "NON-COMPLIANT", icon: "cancel", chip: "bg-error/10 text-error", bucket: "failed" },
    PARTIAL: { label: "NON-COMPLIANT", icon: "cancel", chip: "bg-error/10 text-error", bucket: "failed" },
    NON_COMPLIANT: { label: "NON-COMPLIANT", icon: "cancel", chip: "bg-error/10 text-error", bucket: "failed" },
    PARTIALLY_COMPLIANT: { label: "NON-COMPLIANT", icon: "cancel", chip: "bg-error/10 text-error", bucket: "failed" },
    NEEDS_MANUAL_VERIFICATION: { label: "NEEDS MANUAL VERIFICATION", icon: "warning", chip: "bg-tertiary-fixed-dim/10 text-tertiary-container", bucket: "review" },
    REVIEW_REQUIRED: { label: "NEEDS MANUAL VERIFICATION", icon: "warning", chip: "bg-tertiary-fixed-dim/10 text-tertiary-container", bucket: "review" },
    NOT_DETECTED: { label: "NEEDS MANUAL VERIFICATION", icon: "warning", chip: "bg-tertiary-fixed-dim/10 text-tertiary-container", bucket: "review" },
    NOT_APPLICABLE: { label: "NOT APPLICABLE", icon: "remove_circle", chip: "bg-surface-variant text-on-surface-variant", bucket: "notApplicable" },
  };

  function statusMeta(status) {
    return STATUS_META[status] || { label: String(status || "UNKNOWN").replace(/_/g, " "), icon: "help", chip: "bg-surface-variant text-on-surface-variant", bucket: "review" };
  }

  // Flattens every rule's checks into one list: a rule with required_fields
  // contributes one check per field; a rule with none (e.g. a whole-document
  // NOT_APPLICABLE case) contributes its own rule-level status as a single
  // check - mirrors compliance_engine.py's own overall-status aggregation.
  // aiAssistedFields is an optional list of field keys that were resolved by
  // the backend's vision-AI fallback (never on manufacturer self-checks
  // today - see backend/api/manufacturer.py's create_self_check, which does
  // not use that fallback); when a field key appears in it, the check is
  // flagged `aiAssisted: true` so a review page can label it distinctly from
  // a purely deterministic finding. The frontend never names the AI vendor -
  // it is a backend implementation detail only.
  function flattenChecks(compliance, aiAssistedFields) {
    var aiFields = Array.isArray(aiAssistedFields) ? aiAssistedFields : [];
    var checks = [];
    (compliance.rule_results || []).forEach(function (rule) {
      var fieldKeys = Object.keys(rule.required_fields || {});
      if (fieldKeys.length) {
        fieldKeys.forEach(function (key) {
          var field = rule.required_fields[key];
          checks.push({
            key: key, label: fieldLabel(key), status: field.status, value: field.value,
            confidence: field.confidence, explanation: field.explanation,
            regions: Array.isArray(field.regions) ? field.regions : [],
            aiAssisted: aiFields.indexOf(key) !== -1,
          });
        });
      } else {
        checks.push({ key: null, label: rule.rule_name, status: rule.status, value: null, confidence: null, explanation: rule.explanation, regions: [], aiAssisted: false });
      }
    });
    return checks;
  }

  // Draws one evidence box, positioned as a CSS percentage of the image's
  // natural size (stays aligned across resize/zoom with no listener).
  // `container` must be position:relative/absolute-positioned over `img`.
  // Ported from compliance-report.html's drawEvidenceBox - same math, same
  // low-confidence accent - minus that page's separate zoom/fullscreen/
  // focus-one-field chrome, which isn't needed for a review page that
  // simply needs to SHOW the evidence, not let an official pan/zoom it.
  function drawEvidenceBox(container, bbox, text, confidence, naturalWidth, naturalHeight) {
    if (!Array.isArray(bbox) || bbox.length !== 4 || !naturalWidth || !naturalHeight) return;
    var x1 = Math.max(0, Math.min(naturalWidth, Number(bbox[0])));
    var y1 = Math.max(0, Math.min(naturalHeight, Number(bbox[1])));
    var x2 = Math.max(x1, Math.min(naturalWidth, Number(bbox[2])));
    var y2 = Math.max(y1, Math.min(naturalHeight, Number(bbox[3])));
    if (x2 <= x1 || y2 <= y1) return;
    var box = document.createElement("div");
    var lowConfidence = Number(confidence) < 0.7;
    box.className = "ll-evidence-box" + (lowConfidence ? " ll-evidence-box-low" : "");
    box.style.left = (x1 / naturalWidth * 100) + "%";
    box.style.top = (y1 / naturalHeight * 100) + "%";
    box.style.width = ((x2 - x1) / naturalWidth * 100) + "%";
    box.style.height = ((y2 - y1) / naturalHeight * 100) + "%";
    box.title = (text || "Detected text") + " (" + Math.round(Number(confidence || 0) * 100) + "%)";
    var label = document.createElement("span");
    label.className = "ll-evidence-box-label";
    label.textContent = text || "Detected text";
    box.appendChild(label);
    container.appendChild(box);
  }

  // Renders every evidence region found across ALL required_fields[].regions
  // on `compliance` onto `container` (an absolutely-positioned overlay div
  // sized to match `img`). Call once img has loaded (naturalWidth/Height
  // available). Idempotent - clears the container first.
  function renderEvidenceOverlay(img, container, compliance) {
    container.innerHTML = "";
    var naturalWidth = img.naturalWidth;
    var naturalHeight = img.naturalHeight;
    if (!naturalWidth || !naturalHeight) return;
    var seen = {};
    (compliance && compliance.rule_results || []).forEach(function (rule) {
      Object.keys(rule.required_fields || {}).forEach(function (name) {
        var regions = rule.required_fields[name].regions;
        if (!Array.isArray(regions)) return;
        regions.forEach(function (region) {
          if (!region || !region.region_id || seen[region.region_id]) return;
          seen[region.region_id] = true;
          drawEvidenceBox(container, region.bbox, region.text, region.confidence, naturalWidth, naturalHeight);
        });
      });
    });
  }

  // Minimal CSS for the evidence overlay (same visual language as
  // compliance-report.html's .bounding-box, under an ll- prefix so it can't
  // collide with any page-specific styling) - injected once per page.
  var EVIDENCE_CSS =
    ".ll-evidence-box{border:2px solid #00274a;position:absolute;background-color:rgba(0,39,74,0.1);pointer-events:none}" +
    ".ll-evidence-box-label{position:absolute;left:-2px;top:-24px;max-width:240px;padding:2px 5px;color:#fff;background:#00274a;font:600 10px/14px Inter,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-radius:2px;box-shadow:0 1px 3px rgba(0,0,0,.25)}" +
    ".ll-evidence-box-low{border-color:#ba1a1a;background-color:rgba(186,26,26,0.12)}" +
    ".ll-evidence-box-low .ll-evidence-box-label{background:#ba1a1a}";

  function injectEvidenceStyles() {
    if (document.getElementById("ll-evidence-style")) return;
    var style = document.createElement("style");
    style.id = "ll-evidence-style";
    style.textContent = EVIDENCE_CSS;
    document.head.appendChild(style);
  }

  injectEvidenceStyles();

  global.LLComplianceRender = {
    fieldLabel: fieldLabel,
    statusMeta: statusMeta,
    flattenChecks: flattenChecks,
    renderEvidenceOverlay: renderEvidenceOverlay,
  };
})(window);
