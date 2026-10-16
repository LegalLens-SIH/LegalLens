/* Thin fetch wrapper for GET /api/compliance/legal-basis (specs/001-legal-rag).
   Read-only, no auth (backend/api/legal_basis.py has no auth dependency).
   Every failure mode - network error, non-2xx response, malformed JSON, or
   a well-formed "not_available" response - resolves to the SAME safe
   not-found shape rather than rejecting or inventing placeholder text, so a
   legal-basis lookup can never break, block, or alter the existing
   compliance rendering it is displayed alongside (constitution Principle
   III/IV). This module never talks to compliance_engine.py's output or any
   officialDecision endpoint - it only ever reads this one read-only route. */
(function (global) {
  "use strict";

  var isLocalHost = /^(localhost|127\.0\.0\.1)$/.test(global.location.hostname);
  var API_ORIGIN = isLocalHost
    ? "http://" + global.location.hostname + ":8000"
    : "https://legallens-lil2.onrender.com";
  var NOT_AVAILABLE = { status: "not_available", retrieval_method: null, provisions: [] };

  function get(ruleId, field) {
    if (!ruleId) return Promise.resolve(NOT_AVAILABLE);
    var url = API_ORIGIN + "/api/compliance/legal-basis?rule_id=" + encodeURIComponent(ruleId) +
      (field ? "&field=" + encodeURIComponent(field) : "");
    return fetch(url)
      .then(function (response) {
        if (!response.ok) return NOT_AVAILABLE;
        return response.json().catch(function () { return NOT_AVAILABLE; });
      })
      .catch(function () { return NOT_AVAILABLE; })
      .then(function (data) {
        if (!data || data.status !== "found" || !Array.isArray(data.provisions) || !data.provisions.length) {
          return NOT_AVAILABLE;
        }
        return data;
      });
  }

  global.LLLegalBasis = { get: get };
})(window);
