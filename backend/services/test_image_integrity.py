"""Phase 1, Requirement 6: SHA-256 image integrity hashing - focused unit
tests for the pure hashing function. Integration coverage (hash attached
to a real scan/revision, computed before OCR/preprocessing) lives in
backend/api/test_ocr_authenticity_integration.py and
backend/api/test_manufacturer.py.
"""
from __future__ import annotations

import hashlib

from backend.services.image_integrity import compute_image_sha256


def test_hash_is_deterministic_same_bytes_same_hash():
    data = b"identical bytes every time"
    assert compute_image_sha256(data) == compute_image_sha256(data)


def test_hash_changes_when_bytes_change():
    original = b"original image bytes"
    changed = b"original image byteS"  # one changed byte
    assert compute_image_sha256(original) != compute_image_sha256(changed)


def test_hash_matches_stdlib_sha256_directly():
    """Not a reimplementation - confirms this is genuinely SHA-256, not
    some other digest, by comparing against hashlib directly."""
    data = b"some jpeg-shaped bytes \xff\xd8\xff\xe0"
    assert compute_image_sha256(data) == hashlib.sha256(data).hexdigest()


def test_hash_is_lowercase_hex_64_chars():
    digest = compute_image_sha256(b"any bytes")
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_of_empty_bytes_does_not_raise():
    """Never raises, even for degenerate input - matches the well-known
    SHA-256 digest of the empty byte string."""
    assert compute_image_sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_hash_is_insensitive_to_anything_other_than_the_exact_bytes():
    """Two DIFFERENT byte strings that happen to represent visually
    similar content (e.g. same text, different encoding) must still hash
    differently - this function hashes bytes, never interprets them."""
    assert compute_image_sha256(b"abc") != compute_image_sha256("abc".encode("utf-16"))
