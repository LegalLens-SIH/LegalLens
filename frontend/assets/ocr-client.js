/* Thin fetch wrapper for the LegalLens OCR backend (FastAPI + PaddleOCR).
   Kept separate from app.js (the local mock-data/session layer) since this
   is the one module in the frontend that actually talks to a real server. */
(function (global) {
  "use strict";

  // Match whichever hostname this page was loaded from (localhost vs
  // 127.0.0.1) rather than hardcoding one - see auth-client.js's matching
  // comment: a mismatch here breaks the SameSite=Lax session cookie.
  var DEFAULT_BASE_URL = "http://" + (global.location.hostname || "localhost") + ":8000";
  var ALLOWED_TYPES = ["image/jpeg", "image/jpg", "image/png", "image/webp"];
  var ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"];

  function getExtension(filename) {
    var idx = filename.lastIndexOf(".");
    return idx === -1 ? "" : filename.slice(idx).toLowerCase();
  }

  function validateFile(file) {
    if (!file) return "No file selected.";
    var ext = getExtension(file.name || "");
    var typeOk = !file.type || ALLOWED_TYPES.indexOf(file.type.toLowerCase()) !== -1;
    var extOk = ALLOWED_EXTENSIONS.indexOf(ext) !== -1;
    if (!typeOk && !extOk) {
      return "Unsupported file type. Please upload a JPG, JPEG, PNG, or WEBP image.";
    }
    return null;
  }

  function isDemoMode() {
    return !!(global.LL && global.LL.isDemoMode && global.LL.isDemoMode());
  }

  function runOcr(file, baseUrl) {
    var url = (baseUrl || DEFAULT_BASE_URL) + "/api/ocr";
    var formData = new FormData();
    formData.append("file", file, file.name);

    return fetch(url, { method: "POST", body: formData, credentials: "include" })
      .then(function (response) {
        return response.json().then(function (data) {
          return { httpOk: response.ok, status: response.status, data: data };
        });
      })
      .catch(function (networkErr) {
        return {
          httpOk: false,
          status: 0,
          data: {
            success: false,
            image: file.name,
            error: {
              code: "network_error",
              message: "Could not reach the OCR backend at " + url + ". Is it running? (" + networkErr.message + ")",
            },
          },
        };
      });
  }

  function evaluateCaptureQuality(file, baseUrl) {
    var url = (baseUrl || DEFAULT_BASE_URL) + "/api/captures/quality";
    var formData = new FormData();
    formData.append("file", file, file.name);
    return fetch(url, { method: "POST", body: formData, credentials: "include" })
      .then(function (response) {
        return response.json().then(function (data) {
          return { httpOk: response.ok, status: response.status, data: data };
        });
      })
      .catch(function (networkErr) {
        return {
          httpOk: false,
          status: 0,
          data: { detail: "Could not evaluate capture quality. " + networkErr.message },
        };
      });
  }

  function createScan(file, metadata, baseUrl) {
    var url = (baseUrl || DEFAULT_BASE_URL) + "/api/scans";
    var formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("product_name", metadata.productName || file.name);
    formData.append("category", metadata.category || "General");
    formData.append("manufacturer", metadata.manufacturer || "Not specified");
    // Phase 2B: optional per-scan OCR mode (see backend/services/ocr_modes.py) -
    // omitted/left as "standard" preserves today's exact default behavior.
    formData.append("ocr_mode", metadata.ocrMode || "standard");
    formData.append("image_side", metadata.imageSide || "Other");
    formData.append("capture_quality_requested", metadata.captureQualityRequested ? "true" : "false");

    return fetch(url, { method: "POST", body: formData, credentials: "include" })
      .then(function (response) {
        return response.json().then(function (data) {
          return { httpOk: response.ok, status: response.status, data: data };
        });
      })
      .catch(function (networkErr) {
        return { httpOk: false, status: 0, data: { success: false, error: { code: "network_error", message: networkErr.message } } };
      });
  }

  function startAnalysisJob(file, metadata, baseUrl) {
    var url = (baseUrl || DEFAULT_BASE_URL) + "/api/analysis-jobs";
    var formData = new FormData();
    var files = Array.isArray(file) ? file : [file];
    files.forEach(function (item) { formData.append("file", item.file || item, (item.file || item).name); });
    formData.append("image_sides", JSON.stringify(files.map(function (item) { return item.side || "Other"; })));
    formData.append("capture_quality_requested", JSON.stringify(files.map(function (item) { return !!item.captureQuality; })));
    formData.append("product_name", metadata.productName || (files[0].file || files[0]).name);
    formData.append("category", metadata.category || "General");
    formData.append("manufacturer", metadata.manufacturer || "Not specified");
    formData.append("ocr_mode", metadata.ocrMode || "standard");
    return fetch(url, { method: "POST", body: formData, credentials: "include" }).then(function (response) {
      return response.json().then(function (data) { return { httpOk: response.ok, status: response.status, data: data }; });
    }).catch(function (networkErr) {
      return { httpOk: false, status: 0, data: { detail: "Could not reach the analysis backend. " + networkErr.message } };
    });
  }

  function getAnalysisJob(jobId, baseUrl) {
    return fetch((baseUrl || DEFAULT_BASE_URL) + "/api/analysis-jobs/" + encodeURIComponent(jobId), { credentials: "include", cache: "no-store" }).then(function (response) {
      return response.json().then(function (data) { return { httpOk: response.ok, status: response.status, data: data }; });
    });
  }

  function getScans(baseUrl) {
    if (isDemoMode()) return Promise.resolve(global.LL.getScans());
    return fetch((baseUrl || DEFAULT_BASE_URL) + "/api/scans", { credentials: "include", cache: "no-store" }).then(function (response) { return response.json(); });
  }

  function getScan(id, baseUrl) {
    if (isDemoMode()) return Promise.resolve(global.LL.getScan(id));
    return fetch((baseUrl || DEFAULT_BASE_URL) + "/api/scans/" + encodeURIComponent(id), { credentials: "include", cache: "no-store" }).then(function (response) {
      if (!response.ok) return null;
      return response.json();
    });
  }

  function deleteScan(id, baseUrl) {
    return fetch((baseUrl || DEFAULT_BASE_URL) + "/api/scans/" + encodeURIComponent(id), { method: "DELETE", credentials: "include" })
      .then(function (response) { return response.json().then(function (data) { return { httpOk: response.ok, status: response.status, data: data }; }); });
  }

  function evaluateCompliance(ocrResult, profile, baseUrl) {
    var url = (baseUrl || DEFAULT_BASE_URL) + "/api/compliance/evaluate";
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ ocr_result: ocrResult, validation_profile: profile || "e_commerce_product_listing" })
    }).then(function (response) {
      return response.json().then(function (data) {
        return { httpOk: response.ok, status: response.status, data: data };
      });
    }).catch(function (networkErr) {
      return { httpOk: false, status: 0, data: { detail: "Could not reach the compliance backend. " + networkErr.message } };
    });
  }

  function getCases(baseUrl) {
    if (isDemoMode()) return Promise.resolve(global.LL.getCases());
    return fetch((baseUrl || DEFAULT_BASE_URL) + "/api/cases", { credentials: "include" }).then(function (response) { return response.json(); });
  }

  // Phase 2A: e-commerce listing verification (backend/api/listing.py) -
  // supplementary evidence for an already-persisted scan the official
  // owns. Never touches compliance/status/score.
  function verifyListing(scanId, url, baseUrl) {
    var endpoint = (baseUrl || DEFAULT_BASE_URL) + "/api/scans/" + encodeURIComponent(scanId) + "/listing-verification";
    return fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ url: url }),
    }).then(function (response) {
      return response.json().then(function (data) {
        return { httpOk: response.ok, status: response.status, data: data };
      });
    }).catch(function (networkErr) {
      return { httpOk: false, status: 0, data: { detail: "Could not reach the listing verification backend. " + networkErr.message } };
    });
  }

  function getListingVerification(scanId, baseUrl) {
    var endpoint = (baseUrl || DEFAULT_BASE_URL) + "/api/scans/" + encodeURIComponent(scanId) + "/listing-verification";
    return fetch(endpoint, { credentials: "include", cache: "no-store" }).then(function (response) {
      if (!response.ok) return null;
      return response.json();
    });
  }

  function timestampEvidencePackage(packageId, baseUrl) {
    var endpoint = (baseUrl || DEFAULT_BASE_URL) + "/api/evidence-packages/" + encodeURIComponent(packageId) + "/timestamp";
    return fetch(endpoint, { method: "POST", credentials: "include" }).then(function (response) {
      return response.json().then(function (data) { return { httpOk: response.ok, status: response.status, data: data }; });
    }).catch(function (networkErr) {
      return { httpOk: false, status: 0, data: { detail: "Could not reach the timestamp authority endpoint. " + networkErr.message } };
    });
  }

  function transmitEvidencePackage(packageId, baseUrl) {
    var endpoint = (baseUrl || DEFAULT_BASE_URL) + "/api/em-maap/transmit-package/" + encodeURIComponent(packageId);
    return fetch(endpoint, { method: "POST", credentials: "include" }).then(function (response) {
      return response.json().then(function (data) { return { httpOk: response.ok, status: response.status, data: data }; });
    }).catch(function (networkErr) {
      return { httpOk: false, status: 0, data: { detail: "Could not reach the e-Maap endpoint. " + networkErr.message } };
    });
  }

  function submitScanCalibration(scanId, calibration, baseUrl) {
    var endpoint = (baseUrl || DEFAULT_BASE_URL) + "/api/scans/" + encodeURIComponent(scanId) + "/calibration";
    return fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(calibration),
    }).then(function (response) {
      return response.json().then(function (data) { return { httpOk: response.ok, status: response.status, data: data }; });
    }).catch(function (networkErr) {
      return { httpOk: false, status: 0, data: { detail: "Could not reach the calibration endpoint. " + networkErr.message } };
    });
  }

  global.LLOcr = {
    runOcr: runOcr,
    evaluateCaptureQuality: evaluateCaptureQuality,
    createScan: createScan,
    startAnalysisJob: startAnalysisJob,
    getAnalysisJob: getAnalysisJob,
    getScans: getScans,
    getScan: getScan,
    deleteScan: deleteScan,
    evaluateCompliance: evaluateCompliance,
    getCases: getCases,
    verifyListing: verifyListing,
    getListingVerification: getListingVerification,
    timestampEvidencePackage: timestampEvidencePackage,
    transmitEvidencePackage: transmitEvidencePackage,
    submitScanCalibration: submitScanCalibration,
    validateFile: validateFile,
    DEFAULT_BASE_URL: DEFAULT_BASE_URL,
  };
})(window);
