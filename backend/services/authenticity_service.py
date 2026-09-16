"""
Authenticity Verification Layer - Section 3: EXIF metadata extraction.

Scope (deliberately narrow): extract available EXIF metadata from an
uploaded image and preserve it as independent, structured EVIDENCE. This
module does NOT interpret that evidence - it never decides "genuine" or
"fake", never computes a score, and never feeds back into OCR, structured
extraction, or backend/services/compliance_engine.py, none of which import
or call anything here. See backend/models/authenticity.py's docstring for
the full data-contract reasoning.

Architectural note (matches the Upload -> [EXIF | OCR] -> ... -> Report
diagram this task specifies): this module is called with the SAME raw
upload bytes the OCR path already has in memory, run independently
alongside (not inside) PaddleOCRService - see backend/api/ocr.py's
create_scan, the one call site. It reads image bytes directly with
Pillow's own decoder rather than reusing PaddleOCRService.load_image()'s
already-decoded array, because that array comes from cv2.imdecode, which
does NOT preserve EXIF metadata (cv2 has no EXIF API at all) - so this is
a second, but lightweight, decode (Pillow's header/metadata parse;
.getexif() does not touch pixel data) of the same bytes, not a second
image-processing pipeline, and it never touches the filesystem or requires
the caller to write a second temp file.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

from PIL import Image

from backend.models.authenticity import AuthenticityEvidence, ExifEvidence

logger = logging.getLogger("legallense.services.authenticity")

# Standard TIFF/EXIF tag numbers (from the EXIF specification - stable,
# not specific to this Pillow version). Make/Model/Software/DateTime live
# directly in the base IFD, exposed by Image.getexif(); DateTimeOriginal
# and GPS data live in their own sub-IFDs, reached via Exif.get_ifd(...).
_TAG_MAKE = 271
_TAG_MODEL = 272
_TAG_SOFTWARE = 305
_TAG_DATETIME_ORIGINAL = 36867  # lives in the Exif sub-IFD, not the base IFD
_IFD_EXIF = 0x8769  # 34665 - Exif.get_ifd() key for the "Exif" sub-IFD
_IFD_GPS = 0x8825  # 34853 - Exif.get_ifd() key for the "GPSInfo" sub-IFD


def _clean_str(value: Any) -> Optional[str]:
    """EXIF string tags are sometimes returned with trailing NUL padding
    or surrounding whitespace (a real, common camera/library quirk) -
    stripped here so a value is either a clean, non-empty string or None,
    never an empty/whitespace-only string masquerading as "present"."""
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text or None


def extract_exif_evidence(image_bytes: bytes) -> ExifEvidence:
    """Extract independent EXIF metadata evidence from raw image bytes.

    Never raises: any failure (an unreadable/corrupt image, malformed or
    unsupported metadata) is captured into ExifEvidence(available=False,
    error=...) instead, so a caller can attach this evidence unconditionally
    with no try/except of its own, and the OCR/compliance pipeline (which
    never touches this bytes buffer or this module at all) can never be put
    at risk by an EXIF-specific failure.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            exif = img.getexif()
            if not exif:
                return ExifEvidence(available=False)

            make = _clean_str(exif.get(_TAG_MAKE))
            model = _clean_str(exif.get(_TAG_MODEL))
            software = _clean_str(exif.get(_TAG_SOFTWARE))

            datetime_original = None
            try:
                exif_sub_ifd = exif.get_ifd(_IFD_EXIF)
                datetime_original = _clean_str(exif_sub_ifd.get(_TAG_DATETIME_ORIGINAL))
            except Exception:
                # A malformed Exif sub-IFD must not fail the whole
                # extraction - the base-IFD fields above are still valid
                # evidence on their own.
                logger.debug("Could not read the Exif sub-IFD for DateTimeOriginal", exc_info=True)

            gps_present = False
            try:
                gps_ifd = exif.get_ifd(_IFD_GPS)
                gps_present = bool(gps_ifd)
            except Exception:
                logger.debug("Could not read the GPS sub-IFD", exc_info=True)

            return ExifEvidence(
                available=True,
                make=make,
                model=model,
                software=software,
                datetime_original=datetime_original,
                gps_present=gps_present,
            )
    except Exception as exc:
        logger.warning("EXIF extraction failed; continuing without EXIF evidence: %s", exc)
        return ExifEvidence(available=False, error=f"{type(exc).__name__}: {exc}")


def build_authenticity_evidence(image_bytes: bytes) -> AuthenticityEvidence:
    """Entry point for the result pipeline: wraps extract_exif_evidence
    into the top-level AuthenticityEvidence container. Never raises (see
    extract_exif_evidence's own docstring), so callers can attach this
    unconditionally alongside OCR/compliance without special-casing."""
    return AuthenticityEvidence(exif=extract_exif_evidence(image_bytes))
