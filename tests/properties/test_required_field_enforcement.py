"""
Property 3 — Required field enforcement.

``RawLeadSchema`` has three required fields:

    company_name, contact_email, product_category

A record in which one or more of these is missing, ``None``, empty or
whitespace-only must raise ``pydantic.ValidationError``, and the error must
attribute the failure to the affected field (where practical).  A generated
valid baseline is checked alongside to confirm the property is exercising a
real transition from pass to fail, not vacuously rejecting everything.

Pure schema-level property test: no database, no network, no OpenAI key.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import ValidationError

from src.validation.input_schemas import RawLeadSchema

REQUIRED = ("company_name", "contact_email", "product_category")

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


# A way to break a required field: drop it, or set it to None/empty/whitespace.
_BREAKERS = ("missing", None, "", "   ", "\t")


def _error_fields(exc: ValidationError) -> set[str]:
    fields: set[str] = set()
    for err in exc.errors():
        for part in err["loc"]:
            fields.add(str(part))
    return fields


# --------------------------------------------------------------------------- #
# Baseline — the generated valid record must actually pass.
# --------------------------------------------------------------------------- #


@prop_settings
@given(lead=_valid_lead())
def test_valid_baseline_record_passes(lead):
    model = RawLeadSchema.model_validate(lead)
    assert model.company_name.strip()
    assert model.product_category.strip()


# --------------------------------------------------------------------------- #
# Property — breaking any non-empty subset of required fields must fail.
# --------------------------------------------------------------------------- #


@prop_settings
@given(
    lead=_valid_lead(),
    targets=st.lists(st.sampled_from(REQUIRED), min_size=1, max_size=3, unique=True),
    breakers=st.lists(st.sampled_from(_BREAKERS), min_size=1, max_size=3),
)
def test_missing_or_blank_required_fields_are_rejected(lead, targets, breakers):
    record = dict(lead)
    for i, field in enumerate(targets):
        breaker = breakers[i % len(breakers)]
        if breaker == "missing":
            record.pop(field, None)
        else:
            record[field] = breaker

    try:
        RawLeadSchema.model_validate(record)
    except ValidationError as exc:
        # Every broken field should be attributed in the error report.
        attributed = _error_fields(exc)
        assert attributed & set(targets)
    else:  # pragma: no cover - property asserts this never happens
        raise AssertionError(
            f"expected ValidationError after breaking {targets} with {breakers}"
        )
