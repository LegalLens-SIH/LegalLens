/* Thin fetch wrapper for the official/inspector review-workflow backend.
   Kept as its own module (same reasoning as ocr-client.js being separate
   from app.js): backend/api/official.py is a structurally separate,
   official_user-gated router from everything auth-client.js/ocr-client.js
   already talk to, so this module is the one place any /api/official call
   is made from - a manufacturer-facing page should never load this file. */
(function (global) {
  "use strict";

  var API_ORIGIN = "http://" + (global.location.hostname || "localhost") + ":8000";

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
      return request("/api/official/review-queue" + (status ? "?status=" + encodeURIComponent(status) : "")).then(function (data) { return data.items; });
    },
    getRevision: function (revisionId) {
      return request("/api/official/revisions/" + encodeURIComponent(revisionId)).then(function (data) { return data.revision; });
    },
    recordDecision: function (revisionId, payload) {
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
