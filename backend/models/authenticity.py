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


class AuthenticityEvidence(BaseModel):
    """Top-level container for independent authenticity evidence - additive
    and parallel to (never consulted by) the OCR/extraction/compliance
    pipeline. See backend/services/authenticity_service.py's module
    docstring for the full architectural note.

    Deliberately just a container for now (only `exif`); other evidence
    types (ELA, noise/compression analysis, ...) are explicitly out of
    scope for this task. A future task would add those as sibling fields
    here, never by changing what `exif` means or how it is produced.
    """

    exif: ExifEvidence
