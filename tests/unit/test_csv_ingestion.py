"""
Unit tests for src/ingestion/csv_ingestion.py.

These tests exercise the ingestion logic with a lightweight in-memory spy
repository instead of a real database — no PostgreSQL or SQLite required.
They verify that valid rows are written to both the raw and validated
collections, that invalid rows become validation errors without polluting the
valid collections, that one bad row does not stop the rest of the file, that
idempotency keys and original CSV rows are preserved, and that the returned
counts are correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.csv_ingestion import ingest_csv_file
from src.ingestion.idempotency import generate_idempotency_key
from src.validation.input_schemas import IngestionResult


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeRepository:
    """In-memory spy standing in for PipelineRepository.

    Records every insert so tests can assert on what the ingestion module
    persisted, without needing a database session.
    """

    def __init__(self) -> None:
        self.raw_leads: list[dict] = []
        self.validated_leads: list[dict] = []
        self.validation_errors: list[dict] = []

    def insert_raw_lead(self, raw_lead: dict) -> str:
        self.raw_leads.append(raw_lead)
        return raw_lead["raw_lead_id"]

    def insert_validated_lead(self, validated_lead: dict) -> str:
        self.validated_leads.append(validated_lead)
        return validated_lead["validated_lead_id"]

    def insert_validation_error(self, error: dict) -> str:
        self.validation_errors.append(error)
        return error["error_id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUN_ID = "11111111-1111-1111-1111-111111111111"

HEADER = "company_name,contact_email,product_category,contact_phone,annual_revenue,target_market"


def write_csv(tmp_path: Path, rows: list[str], header: str = HEADER) -> Path:
    """Write a CSV file with *header* and the given raw *rows* lines."""
    path = tmp_path / "leads.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


VALID_ROW = "Acme Exports Ltd,contact@acme.example.com,Electronics,+1-555-0100,1000000,Germany"


# ---------------------------------------------------------------------------
# Valid rows
# ---------------------------------------------------------------------------

def test_valid_row_inserted_into_raw_and_validated(tmp_path):
    repo = FakeRepository()
    path = write_csv(tmp_path, [VALID_ROW])

    result = ingest_csv_file(path, RUN_ID, repo)

    assert len(repo.raw_leads) == 1
    assert len(repo.validated_leads) == 1
    assert repo.validation_errors == []

    raw = repo.raw_leads[0]
    validated = repo.validated_leads[0]
    assert raw["company_name"] == "Acme Exports Ltd"
    assert raw["contact_email"] == "contact@acme.example.com"
    assert raw["product_category"] == "Electronics"
    # raw_lead and validated_lead are linked in the same processing step.
    assert validated["raw_lead_id"] == raw["raw_lead_id"]
    assert raw["pipeline_run_id"] == RUN_ID
    assert validated["pipeline_run_id"] == RUN_ID
    assert isinstance(result, IngestionResult)


def test_idempotency_key_generated_and_stored(tmp_path):
    repo = FakeRepository()
    path = write_csv(tmp_path, [VALID_ROW])

    ingest_csv_file(path, RUN_ID, repo)

    expected = generate_idempotency_key(
        {
            "company_name": "Acme Exports Ltd",
            "contact_email": "contact@acme.example.com",
            "product_category": "Electronics",
            "target_market": "Germany",
        }
    )
    assert repo.raw_leads[0]["idempotency_key"] == expected


def test_raw_csv_row_preserves_original_values(tmp_path):
    repo = FakeRepository()
    path = write_csv(tmp_path, [VALID_ROW])

    ingest_csv_file(path, RUN_ID, repo)

    raw_csv_row = repo.raw_leads[0]["raw_csv_row"]
    assert raw_csv_row["company_name"] == "Acme Exports Ltd"
    assert raw_csv_row["contact_email"] == "contact@acme.example.com"
    assert raw_csv_row["product_category"] == "Electronics"
    assert raw_csv_row["contact_phone"] == "+1-555-0100"
    assert raw_csv_row["annual_revenue"] == "1000000"
    assert raw_csv_row["target_market"] == "Germany"


def test_optional_fields_handled_cleanly_when_blank(tmp_path):
    repo = FakeRepository()
    row = "Globex Trading,sales@globex.example.com,Textiles,,,"
    path = write_csv(tmp_path, [row])

    result = ingest_csv_file(path, RUN_ID, repo)

    assert result.inserted == 1
    raw = repo.raw_leads[0]
    assert raw["contact_phone"] is None
    assert raw["annual_revenue"] is None
    assert raw["target_market"] is None


def test_annual_revenue_validated_through_schema(tmp_path):
    repo = FakeRepository()
    # Negative revenue is rejected by RawLeadSchema.
    bad = "Globex Trading,sales@globex.example.com,Textiles,,-50,France"
    good = "Acme Exports Ltd,contact@acme.example.com,Electronics,,2500.50,Germany"
    path = write_csv(tmp_path, [bad, good])

    result = ingest_csv_file(path, RUN_ID, repo)

    assert result.inserted == 1
    assert result.failed == 1
    # The valid row's revenue was coerced to a float by the schema.
    assert repo.raw_leads[0]["annual_revenue"] == 2500.50
    assert repo.validation_errors[0]["error_field"] == "annual_revenue"


# ---------------------------------------------------------------------------
# Invalid rows
# ---------------------------------------------------------------------------

def test_invalid_row_recorded_as_validation_error(tmp_path):
    repo = FakeRepository()
    # Missing contact_email.
    row = "Acme Exports Ltd,,Electronics,,,Germany"
    path = write_csv(tmp_path, [row])

    result = ingest_csv_file(path, RUN_ID, repo)

    assert repo.raw_leads == []
    assert repo.validated_leads == []
    assert len(repo.validation_errors) == 1
    assert result.inserted == 0
    assert result.failed == 1

    error = repo.validation_errors[0]
    assert error["pipeline_run_id"] == RUN_ID
    assert error["raw_lead_id"] is None
    assert error["error_field"] is not None
    assert error["error_message"]


def test_missing_required_field_produces_validation_error(tmp_path):
    repo = FakeRepository()
    # Missing product_category.
    row = "Acme Exports Ltd,contact@acme.example.com,,,,Germany"
    path = write_csv(tmp_path, [row])

    ingest_csv_file(path, RUN_ID, repo)

    assert repo.raw_leads == []
    assert len(repo.validation_errors) == 1
    assert repo.validation_errors[0]["error_field"] == "product_category"


def test_invalid_email_produces_validation_error(tmp_path):
    repo = FakeRepository()
    row = "Acme Exports Ltd,not-an-email,Electronics,,,Germany"
    path = write_csv(tmp_path, [row])

    ingest_csv_file(path, RUN_ID, repo)

    assert repo.raw_leads == []
    assert len(repo.validation_errors) == 1
    assert repo.validation_errors[0]["error_field"] == "contact_email"


def test_invalid_row_does_not_stop_valid_rows(tmp_path):
    repo = FakeRepository()
    invalid = "Acme Exports Ltd,not-an-email,Electronics,,,Germany"
    valid_a = "Globex Trading,sales@globex.example.com,Textiles,,,France"
    valid_b = "Initech,info@initech.example.com,Software,,,Spain"
    path = write_csv(tmp_path, [invalid, valid_a, valid_b])

    result = ingest_csv_file(path, RUN_ID, repo)

    assert result.total == 3
    assert result.inserted == 2
    assert result.failed == 1
    assert len(repo.raw_leads) == 2
    assert len(repo.validated_leads) == 2
    assert len(repo.validation_errors) == 1


# ---------------------------------------------------------------------------
# Counts and edge cases
# ---------------------------------------------------------------------------

def test_result_counts_are_correct(tmp_path):
    repo = FakeRepository()
    rows = [
        VALID_ROW,
        "Globex Trading,sales@globex.example.com,Textiles,,,France",
        "Acme Exports Ltd,,Electronics,,,Germany",  # invalid: missing email
    ]
    path = write_csv(tmp_path, rows)

    result = ingest_csv_file(path, RUN_ID, repo)

    assert result.total == 3
    assert result.inserted == 2
    assert result.failed == 1
    assert result.skipped == 0


def test_empty_csv_returns_zero_counts(tmp_path):
    repo = FakeRepository()
    path = write_csv(tmp_path, [])  # header only, no data rows

    result = ingest_csv_file(path, RUN_ID, repo)

    assert result.total == 0
    assert result.inserted == 0
    assert result.failed == 0
    assert repo.raw_leads == []
    assert repo.validated_leads == []
    assert repo.validation_errors == []


def test_file_not_found_raises(tmp_path):
    repo = FakeRepository()
    missing = tmp_path / "does_not_exist.csv"

    with pytest.raises(FileNotFoundError):
        ingest_csv_file(missing, RUN_ID, repo)
