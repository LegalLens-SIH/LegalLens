"""
Data contracts for the Authenticity Verification Layer's EXIF evidence.

Scope (Section 3 of the Authenticity Verification Layer implementation
guide - EXIF metadata extraction ONLY): this module describes independent,
uninterpreted image-metadata EVIDENCE, never a fraud/authenticity verdict.
Nothing here computes a score, flags an image as fake/suspicious/edited, or
feeds back into OCR, structured extraction, or ComplianceEngine - all three
remain entirely unaware this module exists. EXIF absence, partial EXIF
presence, or an editing-software tag (Photoshop, GIMP, Snapseed, ...) are
all just facts to record, not conclusions to draw; any such interpretation
(if ever built) belongs to a future, separate scoring layer, not this one.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ExifEvidence(BaseModel):
    """Independent EXIF metadata evidence for one uploaded image.

    `available` is the ONLY thing that distinguishes "no EXIF block was
    present on the image at all" from "an EXIF block was present, but none
    of the handful of fields this layer extracts happened to be populated
    in it" - `available=True` with every value field still None is a real,
    valid, non-error outcome, not a bug. `error` is set only when
    extraction itself failed (corrupt/unsupported metadata); in that case
    `available` is False, same as genuine absence - this layer does not
    need to distinguish "no EXIF" from "EXIF unreadable" for any decision,
    since neither is ever treated as a fraud signal either way.
    """

    available: bool = Field(..., description="Whether an EXIF metadata block was found on the image at all")
    make: Optional[str] = Field(None, description="Camera/device manufacturer (EXIF tag 271, 'Make')")
    model: Optional[str] = Field(None, description="Camera/device model (EXIF tag 272, 'Model')")
    software: Optional[str] = Field(
        None,
        description="Editing/capture software tag (EXIF tag 305, 'Software') - evidence only; never interpreted as 'edited' or 'fake' by this layer",
    )
    datetime_original: Optional[str] = Field(
        None,
        description="Original capture timestamp (EXIF tag 36867, 'DateTimeOriginal'), exactly as printed by the device - never reformatted, parsed, or validated here",
    )
    gps_present: bool = Field(
        False,
        description="Whether a GPS IFD block was present on the image - presence only; raw GPS coordinates are never extracted or stored by this layer",
    )
    error: Optional[str] = Field(
        None,
        description="Set only when EXIF extraction itself failed (e.g. malformed/unsupported metadata); never affects OCR/compliance processing, which does not consult this field",
    )


class ElaEvidence(BaseModel):
    """Additive ELA-style evidence for a single uploaded image.

    This is intentionally a lightweight, privacy-safe proxy: it carries a
    normalized numeric score and metadata about the method, but never stores
    raw pixel-level diff maps or GPS coordinates. The score is evidence only
    and is never used as a fraud verdict by the compliance pipeline.
    """

    available: bool = Field(False, description="Whether JPEG recompression-difference evidence was computed")
    method: str = Field("jpeg_recompression_difference", description="The evidence method used")
    normalized_score: Optional[float] = Field(
        None, ge=0, le=1,
        description="Normalized mean recompression difference in [0, 1]; evidence only, never a fraud verdict",
    )
    recompression_quality: Optional[int] = Field(None, ge=1, le=100)
    difference_sha256: Optional[str] = Field(None, description="SHA-256 of the recompression difference pixels")
    image_width: Optional[int] = Field(None, gt=0)
    image_height: Optional[int] = Field(None, gt=0)
    error: Optional[str] = Field(
        None, description="Unavailable reason or computation error; never affects OCR/compliance processing",
    )


class AuthenticityEvidence(BaseModel):
    """Top-level container for independent authenticity evidence - additive
    and parallel to (never consulted by) the OCR/extraction/compliance
    pipeline. See backend/services/authenticity_service.py's module
    docstring for the full architectural note.

    The evidence set may grow over time; `exif` and `ela` are sibling
    blocks and both are evidence-only metadata, never a fraud verdict.
    """

    exif: ExifEvidence
    ela: ElaEvidence = Field(default_factory=lambda: ElaEvidence(available=False))
