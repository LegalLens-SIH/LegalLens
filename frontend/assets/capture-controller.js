(function (global) {
  "use strict";

  var REQUIRED_PANELS = ['front', 'back', 'label'];

  function normalizePanel(panelName) {
    var key = String(panelName || '').trim().toLowerCase();
    if (!key) return '';
    if (['front', 'front panel', 'front-side', 'front side'].indexOf(key) !== -1) return 'front';
    if (['back', 'back panel', 'back-side', 'back side'].indexOf(key) !== -1) return 'back';
    if (['label', 'product label', 'front label', 'label panel'].indexOf(key) !== -1) return 'label';
    return key;
  }

  function CaptureController(options) {
    options = options || {};
    this.requiredPanels = Array.isArray(options.requiredPanels) && options.requiredPanels.length ? options.requiredPanels : REQUIRED_PANELS;
    this.captured = {};
  }

  CaptureController.prototype.record = function (panelName, metadata) {
    var panel = normalizePanel(panelName);
    if (!panel) return this.getStatus();
    this.captured[panel] = {
      panel: panel,
      metadata: metadata || {},
      capturedAt: new Date().toISOString()
    };
    return this.getStatus();
  };

  CaptureController.prototype.isComplete = function () {
    return this.requiredPanels.every(function (panel) {
      return !!this.captured[panel];
    }, this);
  };

  CaptureController.prototype.missingPanels = function () {
    return this.requiredPanels.filter(function (panel) {
      return !this.captured[panel];
    }, this);
  };

  CaptureController.prototype.getStatus = function () {
    var missing = this.missingPanels();
    return {
      complete: missing.length === 0,
      requiredPanels: this.requiredPanels.slice(),
      capturedPanels: Object.keys(this.captured).slice(),
      missingPanels: missing,
      officerReviewRequired: missing.length > 0
    };
  };

  global.LL = global.LL || {};
  global.LL.CaptureController = CaptureController;
  global.LL.captureController = new CaptureController();
})(window);
