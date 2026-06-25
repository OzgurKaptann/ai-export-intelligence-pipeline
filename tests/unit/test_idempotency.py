"""
Unit tests for src/ingestion/idempotency.py.

Covers determinism, normalization (whitespace, case, missing/empty/None
target_market), discrimination between logically distinct leads, the
shape of the returned key, both accepted input types, and purity.
"""

import re

import pytest

from src.ingestion.idempotency import generate_idempotency_key
from src.validation.input_schemas import RawLeadSchema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_LEAD = {
    "company_name": "Acme Exports Ltd",
    "contact_email": "contact@acme.example.com",
    "product_category": "Electronics",
    "target_market": "Germany",
}

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def lead(**overrides):
    data = dict(BASE_LEAD)
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_input_produces_same_key():
    assert generate_idempotency_key(lead()) == generate_idempotency_key(lead())


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_whitespace_differences_produce_same_key():
    padded = lead(
        company_name="  Acme Exports Ltd  ",
        contact_email="contact@acme.example.com  ",
        product_category="\tElectronics\n",
        target_market="  Germany  ",
    )
    assert generate_idempotency_key(padded) == generate_idempotency_key(lead())


def test_case_differences_produce_same_key():
    upper = lead(
        company_name="ACME EXPORTS LTD",
        contact_email="CONTACT@ACME.EXAMPLE.COM",
        product_category="ELECTRONICS",
        target_market="GERMANY",
    )
    assert generate_idempotency_key(upper) == generate_idempotency_key(lead())


def test_missing_and_empty_target_market_produce_same_key():
    missing = lead()
    del missing["target_market"]
    empty = lead(target_market="")
    assert generate_idempotency_key(missing) == generate_idempotency_key(empty)


def test_none_and_empty_target_market_produce_same_key():
    none_value = lead(target_market=None)
    empty = lead(target_market="")
    assert generate_idempotency_key(none_value) == generate_idempotency_key(empty)


def test_whitespace_only_target_market_matches_missing():
    whitespace = lead(target_market="   ")
    missing = lead()
    del missing["target_market"]
    assert generate_idempotency_key(whitespace) == generate_idempotency_key(missing)


# ---------------------------------------------------------------------------
# Discrimination — different logical leads produce different keys
# ---------------------------------------------------------------------------

def test_different_email_produces_different_key():
    other = lead(contact_email="other@acme.example.com")
    assert generate_idempotency_key(other) != generate_idempotency_key(lead())


def test_different_company_name_produces_different_key():
    other = lead(company_name="Globex Trading")
    assert generate_idempotency_key(other) != generate_idempotency_key(lead())


def test_different_product_category_produces_different_key():
    other = lead(product_category="Textiles")
    assert generate_idempotency_key(other) != generate_idempotency_key(lead())


def test_different_target_market_produces_different_key_when_present():
    other = lead(target_market="France")
    assert generate_idempotency_key(other) != generate_idempotency_key(lead())


# ---------------------------------------------------------------------------
# Key shape
# ---------------------------------------------------------------------------

def test_key_is_sha256_hex_string_length_64():
    key = generate_idempotency_key(lead())
    assert isinstance(key, str)
    assert len(key) == 64
    assert HEX64.match(key)


# ---------------------------------------------------------------------------
# Accepted input types
# ---------------------------------------------------------------------------

def test_accepts_raw_lead_schema_input():
    schema = RawLeadSchema.model_validate(lead())
    assert HEX64.match(generate_idempotency_key(schema))


def test_raw_lead_schema_and_dict_produce_same_key():
    schema = RawLeadSchema.model_validate(lead())
    assert generate_idempotency_key(schema) == generate_idempotency_key(lead())


def test_accepts_dict_input():
    assert HEX64.match(generate_idempotency_key(lead()))


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------

def test_no_side_effects_on_input_dict():
    data = lead()
    snapshot = dict(data)
    generate_idempotency_key(data)
    assert data == snapshot
