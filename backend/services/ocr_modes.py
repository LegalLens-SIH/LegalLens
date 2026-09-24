"""
Phase 2B: user-selectable OCR modes.

Exactly TWO server-defined modes, each mapping to a real, ALREADY-EXISTING
configuration difference in this codebase - never a new PaddleOCR
constructor parameter, and never anything derived from unvalidated client
input (see parse_ocr_mode's strict allow-listing).

  standard (default): the new production baseline as of the 2026-09-17
      tuning pass - see backend/api/ocr.py's module-level
      `_preprocess_options`, which now ALWAYS applies resize (downscale to
      max_side=2000px) unconditionally, plus denoise/enhance_contrast only
      when the pre-existing, unrelated OCR_PREPROCESSING_ENABLED env flag
      is set. Selecting "standard" explicitly is equivalent to not
      selecting a mode at all.
  high_accuracy: resize (the same new baseline every mode gets) PLUS
      denoise + CLAHE contrast enhancement on top - a genuine superset/
      slower-but-more-thorough tradeoff, not a different, incomparable
      bundle.

"fast" was RETIRED on 2026-09-17: it was originally `PreprocessOptions(
resize=True)` alone, offering resize as an opt-in speed tradeoff against a
non-resizing "standard" baseline. Once real end-to-end benchmarking (see
training/benchmark_results/phase2_* and backend/ocr/README.md's
"Production OCR performance tuning" section) confirmed resize's ~3.3x
speedup was worth making unconditional for every call, "fast" became
byte-for-byte identical to the new "standard" - i.e. it stopped
demonstrating any distinct tradeoff at all. Per this module's own standing
principle ("if a proposed mode cannot demonstrate a meaningful tradeoff,
do not expose it as a production mode" - see PHASE 2 IMPLEMENTATION
REPORT's OCR Mode Benchmark section), it was removed rather than kept as
a confusing, meaningless duplicate of "standard". A client that still
sends ocr_mode="fast" gets an explicit 400 (InvalidOCRModeError), never a
silent redirect to "standard" - loud failure over hidden behavior change.

No mode ever changes lang / use_doc_orientation_classify /
use_doc_unwarping / use_textline_orientation / enable_mkldnn /
text_rec_score_thresh - the underlying PaddleOCR ENGINE (see
paddle_ocr_service.py's _engine_cache) is byte-for-byte identical across
both modes; only the image PREPROCESSING applied before that same engine
runs differs. This is a deliberately minimal surface: a client can only
ever select one of two fixed names, never inject an arbitrary PaddleOCR
parameter.
"""

from __future__ import annotations

from typing import Literal

from backend.ocr.preprocessing import MAX_SIDE_DEFAULT, PreprocessOptions

OCRMode = Literal["standard", "high_accuracy"]

DEFAULT_OCR_MODE: OCRMode = "standard"
VALID_OCR_MODES: frozenset[str] = frozenset({"standard", "high_accuracy"})


class InvalidOCRModeError(ValueError):
    """Raised when a client supplies an OCR mode outside the fixed,
    server-defined set (this now includes the retired "fast" value).
    Callers (backend/api/ocr.py) turn this into a 400 response - never a
    silent fallback to a default, which would hide a client-side bug or a
    silent behavior change for old clients."""


def parse_ocr_mode(raw: str | None) -> OCRMode:
    """Strictly allow-list a client-supplied mode string. The only thing
    this function ever returns is one of the two literal mode names
    above, used purely as a lookup key into _FORCED_PREPROCESS_OPTIONS_BY_MODE -
    it is never passed through to any PaddleOCR constructor argument."""
    if raw is None or raw.strip() == "":
        return DEFAULT_OCR_MODE
    candidate = raw.strip().lower()
    if candidate not in VALID_OCR_MODES:
        raise InvalidOCRModeError(f"Unknown OCR mode '{raw}'. Valid modes: {sorted(VALID_OCR_MODES)}")
    return candidate  # type: ignore[return-value]


# "standard" is deliberately absent here - see create_scan's handling,
# which uses the existing module-level _preprocess_options for it instead
# of a hardcoded value, preserving the OCR_PREPROCESSING_ENABLED env flag's
# existing effect exactly. high_accuracy explicitly LAYERS resize on top
# (rather than replacing it) so it stays strictly a superset of "standard",
# never a regression on the one dimension every mode now shares.
_FORCED_PREPROCESS_OPTIONS_BY_MODE: dict[str, PreprocessOptions] = {
    "high_accuracy": PreprocessOptions(resize=True, max_side=MAX_SIDE_DEFAULT, denoise=True, enhance_contrast=True),
}


def forced_preprocess_options_for_mode(mode: OCRMode) -> PreprocessOptions | None:
    """None for "standard" (caller must use its own existing baseline
    option instead - see module docstring); a fixed PreprocessOptions for
    every other mode."""
    return _FORCED_PREPROCESS_OPTIONS_BY_MODE.get(mode)
