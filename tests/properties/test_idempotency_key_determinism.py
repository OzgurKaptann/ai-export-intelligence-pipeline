"""
Property 16 — Idempotency key determinism.

``generate_idempotency_key`` reduces a lead's business identity
(company_name, contact_email, product_category, target_market) to a stable
SHA-256 digest.  The properties checked here:

    * the same input always produces the same key
    * a ``dict`` and the equivalent ``RawLeadSchema`` produce the same key
    * whitespace and case differences in the identity fields are normalized
      away (same key)
    * ``None``, a missing key, and a blank/whitespace ``target_market`` are all
      treated identically
    * the key is a 64-character lowercase SHA-256 hex string
    * the function never mutates the input dictionary
    * changing an identity field (here: appending a distinct character to
      ``company_name``) does change the key

Pure property test over ``src.ingestion.idempotency``: no database, no
network, no OpenAI key.
"""

from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings, strategies as st

from src.ingestion.idempotency import generate_idempotency_key
from src.validation.input_schemas import RawLeadSchema

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Hypothesis data generation can be markedly slower on a shared/throttled CI
# container than on a dev machine; the too_slow health check is about
# environment speed, not test validity, so it is suppressed narrowly here.
prop_settings = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Non-blank text: letters/digits only (no whitespace), so it never strips to
# empty and is cheap to generate.
_non_blank_text = st.text(alphabet=_ALNUM, min_size=1, max_size=10)

_email = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    st.text(alphabet=_ALNUM, min_size=1, max_size=8),
    st.text(alphabet=_ALNUM, min_size=1, max_size=8),
    st.sampled_from(["com", "org", "net", "io", "co", "example"]),
)


@st.composite
def _valid_lead(draw) -> dict:
    return {
        "company_name": draw(_non_blank_text),
        "contact_email": draw(_email),
        "product_category": draw(_non_blank_text),
        "target_market": draw(st.one_of(st.none(), _non_blank_text)),
    }


# --------------------------------------------------------------------------- #
# Determinism, shape and purity
# --------------------------------------------------------------------------- #


@prop_settings
@given(lead=_valid_lead())
def test_same_input_produces_same_key(lead):
    assert generate_idempotency_key(dict(lead)) == generate_idempotency_key(dict(lead))


@prop_settings
@given(lead=_valid_lead())
def test_key_is_64_char_sha256_hex(lead):
    key = generate_idempotency_key(lead)
    assert isinstance(key, str)
    assert len(key) == 64
    assert _HEX64.match(key)


@prop_settings
@given(lead=_valid_lead())
def test_input_dict_is_not_mutated(lead):
    snapshot = dict(lead)
    generate_idempotency_key(lead)
    assert lead == snapshot


# --------------------------------------------------------------------------- #
# dict vs RawLeadSchema equivalence
# --------------------------------------------------------------------------- #


@prop_settings
@given(lead=_valid_lead())
def test_dict_and_schema_inputs_match(lead):
    schema = RawLeadSchema.model_validate(lead)
    assert generate_idempotency_key(lead) == generate_idempotency_key(schema)


# --------------------------------------------------------------------------- #
# Normalization — whitespace and case
# --------------------------------------------------------------------------- #


@prop_settings
@given(lead=_valid_lead(), pad=st.sampled_from(["", " ", "  ", "\t", " \t "]))
def test_whitespace_and_case_differences_produce_same_key(lead, pad):
    base = dict(lead)
    variant = dict(lead)
    for field in ("company_name", "contact_email", "product_category"):
        variant[field] = f"{pad}{str(base[field]).upper()}{pad}"
    if base.get("target_market") is not None:
        variant["target_market"] = f"{pad}{str(base['target_market']).upper()}{pad}"
    assert generate_idempotency_key(variant) == generate_idempotency_key(base)


# --------------------------------------------------------------------------- #
# target_market: None, missing and blank are interchangeable
# --------------------------------------------------------------------------- #


@prop_settings
@given(lead=_valid_lead(), blank=st.sampled_from(["", " ", "   ", "\t"]))
def test_none_missing_and_blank_target_market_are_consistent(lead, blank):
    with_none = {**lead, "target_market": None}

    missing = dict(lead)
    missing.pop("target_market", None)

    with_blank = {**lead, "target_market": blank}

    key_none = generate_idempotency_key(with_none)
    key_missing = generate_idempotency_key(missing)
    key_blank = generate_idempotency_key(with_blank)

    assert key_none == key_missing == key_blank


# --------------------------------------------------------------------------- #
# Discrimination — changing an identity field changes the key
# --------------------------------------------------------------------------- #


@prop_settings
@given(lead=_valid_lead())
def test_changing_company_name_changes_the_key(lead):
    # Appending a distinct lowercase letter changes the normalized identity,
    # so the key must change too.
    changed = {**lead, "company_name": str(lead["company_name"]) + "z"}
    assert generate_idempotency_key(changed) != generate_idempotency_key(lead)
