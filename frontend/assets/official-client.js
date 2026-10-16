/* Thin fetch wrapper for the official/inspector review-workflow backend.
   Kept as its own module (same reasoning as ocr-client.js being separate
   from app.js): backend/api/official.py is a structurally separate,
   official_user-gated router from everything auth-client.js/ocr-client.js
   already talk to, so this module is the one place any /api/official call
   is made from - a manufacturer-facing page should never load this file. */
(function (global) {
  "use strict";

  var isLocalHost = /^(localhost|127\.0\.0\.1)$/.test(global.location.hostname);
  var API_ORIGIN = isLocalHost
    ? "http://" + global.location.hostname + ":8000"
    : "https://legallens-lil2.onrender.com";

  function isDemoMode() {
    return !!(global.LL && global.LL.isDemoMode && global.LL.isDemoMode());
  }

  function demoRevision(revisionId) {
    var cases = global.LL.getCases();
    var item = cases.find(function (record) { return record.id === revisionId; });
    if (!item) return null;
    var scan = global.LL.getScan(item.scanId) || {};
    var ruleResults = (scan.findings || []).map(function (finding, index) {
      var status = finding.status === "compliant" ? "PASS" : (finding.status === "missing" ? "NOT_DETECTED" : "NEEDS_MANUAL_VERIFICATION");
      return {
        rule_id: "DEMO-RULE-" + (index + 1),
        rule_name: finding.field,
        status: status,
        explanation: finding.note || "Demonstration finding from the uploaded product sample.",
        required_fields: {
          [finding.field.toLowerCase().replace(/[^a-z0-9]+/g, "_")]: {
            status: status,
            value: finding.value,
            confidence: scan.score || 82,
            explanation: finding.note || "Demonstration finding from the uploaded product sample."
          }
        }
      };
    });
    return {
      revisionId: item.id,
      imageReference: scan.productName || item.entity,
      manufacturerName: item.entity,
      manufacturerCompany: item.entity,
      version: 1,
      submittedAt: item.timeline && item.timeline[0] ? item.timeline[0].date : new Date().toISOString(),
      reviewStatus: item.status === "under_review" ? "UNDER_OFFICIAL_REVIEW" : "PENDING_OFFICIAL_REVIEW",
      complianceResult: {
        disclaimer: "This demonstration assessment is for preview purposes only.",
        overall_status: scan.status === "compliant" ? "PASS" : "REVIEW_REQUIRED",
        compliance_score: scan.score,
        rule_results: ruleResults
      }
    };
  }

  function request(path, options) {
    options = options || {};
    options.credentials = "include";
    var url = API_ORIGIN + path;
    return fetch(url, options).catch(function () {
      throw new Error("Could not reach the backend at " + API_ORIGIN + ". Start the FastAPI server and try again.");
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) {
          throw new Error(data.detail || data.message || "Request failed");
        }
        return data;
      });
    });
  }

  global.LLOfficial = {
    reviewQueue: function (status) {
      if (isDemoMode()) {
        var items = global.LL.getCases().map(function (record) {
          var revision = demoRevision(record.id);
          return revision;
        }).filter(Boolean);
        return Promise.resolve(status ? items.filter(function (item) { return item.reviewStatus === status; }) : items);
      }
      return request("/api/official/review-queue" + (status ? "?status=" + encodeURIComponent(status) : "")).then(function (data) { return data.items; });
    },
    getRevision: function (revisionId) {
      if (isDemoMode()) return Promise.resolve(demoRevision(revisionId));
      return request("/api/official/revisions/" + encodeURIComponent(revisionId)).then(function (data) { return data.revision; });
    },
    recordDecision: function (revisionId, payload) {
      if (isDemoMode()) {
        var revision = demoRevision(revisionId);
        if (revision) revision.reviewStatus = "DECISION_RECORDED";
        return Promise.resolve(revision);
      }
      return request("/api/official/revisions/" + encodeURIComponent(revisionId) + "/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (data) { return data.revision; });
    },
    imageUrl: function (revisionId) {
      return API_ORIGIN + "/api/official/revisions/" + encodeURIComponent(revisionId) + "/image";
    },
  };
})(window);
