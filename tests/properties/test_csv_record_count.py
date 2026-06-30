"""
Property 1 — CSV record count preservation.

Every input row read from the CSV must be accounted for by exactly one
outcome: inserted, invalid (validation error) or skipped (duplicate business
identity).  Across an arbitrary mix of valid rows, rows missing a required
field, and duplicate rows, the ingestion module's counters must satisfy:

    inserted + invalid + skipped == total input rows

and the persisted side-effects must agree with the counters:

    raw insert count        == inserted
    validated insert count  == inserted
    validation error count  == invalid

The test uses a deterministic in-memory fake repository (no PostgreSQL, no
network, no OpenAI) and writes each generated dataset to a temporary CSV with
``csv.DictWriter``.  Strategies keep cells small and realistic — ASCII
letters/digits only, no newlines or path separators.
"""

from __future__ import annotations

import csv
import os
import tempfile

from hypothesis import HealthCheck, given, settings, strategies as st

from src.ingestion.csv_ingestion import ingest_csv_file

RUN_ID = "11111111-1111-1111-1111-111111111111"

FIELDNAMES = [
    "company_name",
    "contact_email",
    "product_category",
    "contact_phone",
    "annual_revenue",
    "target_market",
]

REQUIRED = ("company_name", "contact_email", "product_category")

# Hypothesis data generation can be markedly slower on a shared/throttled CI
# container than on a dev machine, and this test also does temp-file I/O per
# example.  The too_slow health check and the per-example deadline are about
# environment speed, not test validity, so both are relaxed narrowly here.
prop_settings = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# --------------------------------------------------------------------------- #
# Test double — deterministic, in-memory, scoped to a single ingest run
# --------------------------------------------------------------------------- #


class FakeRepository:
    """In-memory spy mirroring the subset of PipelineRepository ingestion uses."""

    def __init__(self) -> None:
        self.raw_leads: list[dict] = []
        self.validated_leads: list[dict] = []
        self.validation_errors: list[dict] = []

    def insert_raw_lead(self, raw_lead: dict) -> str:
        self.raw_leads.append(raw_lead)
        return raw_lead["raw_lead_id"]

    def get_raw_lead_by_idempotency_key(self, idempotency_key: str):
        for raw_lead in self.raw_leads:
            if raw_lead.get("idempotency_key") == idempotency_key:
                return raw_lead
        return None

    def insert_validated_lead(self, validated_lead: dict) -> str:
        self.validated_leads.append(validated_lead)
        return validated_lead["validated_lead_id"]

    def insert_validation_error(self, error: dict) -> str:
        self.validation_errors.append(error)
        return error["error_id"]


# --------------------------------------------------------------------------- #
# Strategies — small, realistic, CSV-safe cell values (cheap to generate)
# --------------------------------------------------------------------------- #

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_DIGITS = "0123456789"

# Non-blank text: letters/digits only (no whitespace), so it never strips to
# empty — required fields reject blank/whitespace.
_non_blank_text = st.text(alphabet=_ALNUM, min_size=1, max_size=10)

# Valid email built from generated parts so it always parses as an EmailStr.
_email = st.builds(
    lambda local, domain, tld: f"{local}@{domain}.{tld}",
    st.text(alphabet=_ALNUM, min_size=1, max_size=8),
    st.text(alphabet=_ALNUM, min_size=1, max_size=8),
    st.sampled_from(["com", "org", "net", "io", "co", "example"]),
)

_revenue_cell = st.one_of(
    st.just(""),
    st.integers(min_value=0, max_value=10_000_000).map(str),
)

_phone_cell = st.one_of(
    st.just(""),
    st.text(alphabet=_DIGITS + "-+", min_size=3, max_size=12),
)

_target_cell = st.one_of(st.just(""), _non_blank_text)

# A blank value that any required field rejects.
_blank_cell = st.sampled_from(["", " ", "   ", "\t"])


@st.composite
def _valid_row(draw) -> dict:
    return {
        "company_name": draw(_non_blank_text),
        "contact_email": draw(_email),
        "product_category": draw(_non_blank_text),
        "contact_phone": draw(_phone_cell),
        "annual_revenue": draw(_revenue_cell),
        "target_market": draw(_target_cell),
    }


@st.composite
def _row(draw) -> dict:
    """A row that is either valid or invalid (one required field blanked)."""
    row = draw(_valid_row())
    if draw(st.booleans()):
        field = draw(st.sampled_from(REQUIRED))
        row[field] = draw(_blank_cell)
    return row


@st.composite
def _dataset(draw) -> list[dict]:
    """A list of rows with an injected handful of exact-duplicate rows."""
    rows = draw(st.lists(_row(), min_size=0, max_size=8))
    if rows:
        n_dup = draw(st.integers(min_value=0, max_value=3))
        for _ in range(n_dup):
            idx = draw(st.integers(min_value=0, max_value=len(rows) - 1))
            rows.append(dict(rows[idx]))
    return rows


def _is_valid(row: dict) -> bool:
    return all(str(row[name]).strip() for name in REQUIRED) and (
        "@" in row["contact_email"] and "." in row["contact_email"].split("@")[-1]
    )


# --------------------------------------------------------------------------- #
# Property
# --------------------------------------------------------------------------- #


@prop_settings
@given(rows=_dataset())
def test_every_input_row_is_accounted_for(rows):
    repo = FakeRepository()

    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

        result = ingest_csv_file(path, RUN_ID, repo)
    finally:
        os.remove(path)

    total = len(rows)

    # Core conservation property: nothing is lost or double-counted.
    assert result.total == total
    assert result.inserted + result.failed + result.skipped == total

    # Persisted side-effects agree with the reported counters.
    assert len(repo.raw_leads) == result.inserted
    assert len(repo.validated_leads) == result.inserted
    assert len(repo.validation_errors) == result.failed

    # Every row that is invalid by construction must have failed validation,
    # and the number of valid rows must equal inserted + skipped.
    valid_count = sum(1 for row in rows if _is_valid(row))
    assert result.failed == total - valid_count
    assert result.inserted + result.skipped == valid_count
