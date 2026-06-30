"""
Property 6 — Validation error field attribution.

When a single field is invalid, ``RawLeadSchema`` must attribute the failure
to that field: the offending field name must appear in the ``loc`` of at least
one entry returned by ``ValidationError.errors()``.  We assert field-level
attribution only — never brittle, exact error-message wording.

Four independent violations are covered, each keeping every other field valid:

    * invalid contact_email   (missing ``@`` or missing domain)
    * negative annual_revenue
    * blank company_name
    * blank product_category

Pure schema-level property test: no database, no network, no OpenAI key.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st
from pydantic import ValidationError

from src.validation.input_schemas import RawLeadSchema

# Hypothesis data generation can be markedly slower on a shared/throttled CI
# container than on a dev machine; the too_slow health check is about
# environment speed, not test validity, so it is suppressed narrowly here.
prop_settings = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

_non_blank_text = st.text(alphabet=_ALNUM, min_size=1, max_size=10)

_email = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    st.text(alphabet=_ALNUM, min_size=1, max_size=8),
    st.text(alphabet=_ALNUM, min_size=1, max_size=8),
    st.sampled_from(["com", "org", "net", "io", "co", "example"]),
)

# Strings that are intentionally not valid emails: either no ``@`` at all, or
# an ``@`` with nothing after it (no domain).
_invalid_email = st.one_of(
    st.text(alphabet=_ALNUM, min_size=1, max_size=15),  # no "@"
    st.builds(lambda s: s + "@", st.text(alphabet=_ALNUM, min_size=1, max_size=10)),
)

_negative_revenue = st.one_of(
    st.integers(min_value=-10_000_000, max_value=-1),
    st.floats(min_value=-1_000_000.0, max_value=-0.001, allow_nan=False, allow_infinity=False),
)

_blank = st.sampled_from(["", " ", "   ", "\t", "\n", " \t "])


def _base_lead() -> dict:
    # A concrete valid skeleton; the field under test is overwritten per example.
    return {
        "company_name": "Acme Exports Ltd",
        "contact_email": "contact@acme.example.com",
        "product_category": "Electronics",
        "annual_revenue": 1000,
        "target_market": "Germany",
    }


def _loc_fields(exc: ValidationError) -> set[str]:
    fields: set[str] = set()
    for err in exc.errors():
        for part in err["loc"]:
            fields.add(str(part))
    return fields


def _assert_attributed(record: dict, field: str) -> None:
    try:
        RawLeadSchema.model_validate(record)
    except ValidationError as exc:
        assert field in _loc_fields(exc)
    else:  # pragma: no cover - property asserts this never happens
        raise AssertionError(f"expected ValidationError attributed to {field!r}")


@prop_settings
@given(value=_invalid_email, company=_non_blank_text, category=_non_blank_text)
def test_invalid_email_attributed_to_contact_email(value, company, category):
    record = _base_lead()
    record["company_name"] = company
    record["product_category"] = category
    record["contact_email"] = value
    _assert_attributed(record, "contact_email")


@prop_settings
@given(value=_negative_revenue, email=_email)
def test_negative_revenue_attributed_to_annual_revenue(value, email):
    record = _base_lead()
    record["contact_email"] = email
    record["annual_revenue"] = value
    _assert_attributed(record, "annual_revenue")


@prop_settings
@given(value=_blank, email=_email, category=_non_blank_text)
def test_blank_company_name_attributed_to_company_name(value, email, category):
    record = _base_lead()
    record["contact_email"] = email
    record["product_category"] = category
    record["company_name"] = value
    _assert_attributed(record, "company_name")


@prop_settings
@given(value=_blank, email=_email, company=_non_blank_text)
def test_blank_product_category_attributed_to_product_category(value, email, company):
    record = _base_lead()
    record["contact_email"] = email
    record["company_name"] = company
    record["product_category"] = value
    _assert_attributed(record, "product_category")
