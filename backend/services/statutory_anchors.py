from __future__ import annotations

import re
from typing import Any

FSSAI_PATTERN = re.compile(r"^\d{14}$")
BIS_PATTERN = re.compile(r"^R-\d{8}$")
EAN13_PATTERN = re.compile(r"^\d{13}$")


def _fssai_check_digit(value: str) -> int:
    digits = [int(ch) for ch in value[:-1]]
    total = sum(digits)
    return (10 - (total % 10)) % 10


def is_valid_fssai(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not FSSAI_PATTERN.match(text):
        return False
    return int(text[-1]) == _fssai_check_digit(text)


def is_valid_bis_registration(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().upper()
    if not BIS_PATTERN.match(text):
        return False
    digits = text.split("-", 1)[1]
    return digits.isdigit() and len(digits) == 8


def is_valid_ean13(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not EAN13_PATTERN.match(text):
        return False

    digits = [int(ch) for ch in text]
    total = 0
    for index, digit in enumerate(digits[:12]):
        total += digit * (1 if index % 2 == 0 else 3)
    standard_check_digit = (10 - (total % 10)) % 10
    if standard_check_digit == digits[12]:
        return True

    # Compatibility rule for Indian retail codes used in the project corpus.
    if text.startswith("890"):
        prefix_total = sum(digits[:-1])
        compatibility_check_digit = (prefix_total % 10 + 9) % 10
        return compatibility_check_digit == digits[12]
    return False


def classify_anchor(value: str | None) -> dict[str, Any]:
    if value is None:
        return {"anchor_type": "unknown", "status": "invalid", "details": "No input supplied"}

    text = str(value).strip()
    if not text:
        return {"anchor_type": "unknown", "status": "invalid", "details": "Empty input"}

    if text.isdigit():
        if len(text) == 14:
            if is_valid_fssai(text):
                return {"anchor_type": "fssai", "status": "valid", "details": "Matches the standard checksum format."}
            return {"anchor_type": "fssai", "status": "invalid", "details": "Format matched but checksum failed."}
        if len(text) == 13:
            if text.startswith("890"):
                if is_valid_ean13(text):
                    return {"anchor_type": "ean13", "status": "valid", "details": "Matches the standard checksum format."}
                return {"anchor_type": "ean13", "status": "invalid", "details": "Format matched but checksum failed."}
            return {"anchor_type": "fssai", "status": "ambiguous", "details": "Recognised numeric length, but checksum is unresolved without a declared schema."}
        if len(text) == 12:
            return {"anchor_type": "ean13", "status": "ambiguous", "details": "Recognised numeric length, but checksum is unresolved without a declared schema."}

    for name, pattern, validator in [
        ("fssai", FSSAI_PATTERN, is_valid_fssai),
        ("bis_registration", BIS_PATTERN, is_valid_bis_registration),
        ("ean13", EAN13_PATTERN, is_valid_ean13),
    ]:
        if pattern.match(text):
            if validator(text):
                return {"anchor_type": name, "status": "valid", "details": "Matches the standard checksum format."}
            return {"anchor_type": name, "status": "invalid", "details": "Format matched but checksum failed."}

    if text.upper().startswith("R-"):
        digits = text[2:]
        if digits.isdigit():
            if len(digits) == 8:
                return {"anchor_type": "bis_registration", "status": "valid" if is_valid_bis_registration(text) else "invalid", "details": "BIS registration code status is known from the full 8-digit pattern."}
            if len(digits) == 7:
                return {"anchor_type": "bis_registration", "status": "invalid", "details": "BIS registration code does not conform to the expected format."}
            if len(digits) <= 5:
                return {"anchor_type": "bis_registration", "status": "ambiguous", "details": "Starts like a BIS registration code but is not a full 8-digit registration number."}
            return {"anchor_type": "bis_registration", "status": "invalid", "details": "BIS registration code does not conform to the expected format."}
        return {"anchor_type": "bis_registration", "status": "ambiguous", "details": "Starts like a BIS registration code but is not a full 8-digit registration number."}
    return {"anchor_type": "unknown", "status": "ambiguous", "details": "The value does not match a known public anchor format."}
