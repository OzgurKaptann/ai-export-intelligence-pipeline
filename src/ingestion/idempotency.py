"""
Deterministic idempotency key generation for lead records.

A lead's *business identity* is defined by four fields:

    company_name, contact_email, product_category, target_market

Two leads that share the same normalized values for these fields are
considered the same logical lead and therefore receive the same
idempotency key.  The key is a SHA-256 hex digest, making it stable
across processes, machines, and Python runs (unlike the salted built-in
``hash()``).

The single public entry point :func:`generate_idempotency_key` is pure
and side-effect free: it never mutates its input and never touches the
database.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Union

from src.validation.input_schemas import RawLeadSchema

# Fields that together define the business identity of a lead, in the
# canonical order used to build the hash input.
_IDENTITY_FIELDS = (
    "company_name",
    "contact_email",
    "product_category",
    "target_market",
)

# Delimiter placed between canonical field values.  Using a non-printable
# control character avoids accidental collisions where a value contains the
# delimiter itself.
_FIELD_DELIMITER = "\x1f"  # ASCII unit separator


def _normalize(value: Any) -> str:
    """
    Reduce a single field value to its canonical string form.

    - ``None`` and empty / whitespace-only values collapse to ``""`` so
      that a missing, empty, and ``None`` ``target_market`` all behave
      identically.
    - All other values are stripped of surrounding whitespace and
      lowercased so that case and padding differences are ignored.
    """
    if value is None:
        return ""
    return str(value).strip().lower()


def _to_dict(lead: Union[RawLeadSchema, Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return a mapping view of ``lead`` without mutating the input."""
    if isinstance(lead, RawLeadSchema):
        return lead.model_dump()
    return lead


def generate_idempotency_key(lead: Union[RawLeadSchema, Mapping[str, Any]]) -> str:
    """
    Generate a deterministic idempotency key for a lead.

    Accepts either a :class:`RawLeadSchema` instance or a plain mapping
    (``dict``).  The same logical lead always produces the same
    64-character lowercase SHA-256 hex digest; logically different leads
    produce different digests.

    The function is pure: the input is never modified.
    """
    data = _to_dict(lead)
    canonical = _FIELD_DELIMITER.join(
        _normalize(data.get(name)) for name in _IDENTITY_FIELDS
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
