"""
Integration test — data quality report generation and retrieval (Task 27).

Two angles against a real PostgreSQL database:

* a full mock-LLM pipeline run generates exactly one ``data_quality_reports``
  row whose six counts match the live per-stage table counts, and the report is
  retrievable through ``PipelineRepository.get_quality_report``;
* :func:`src.pipeline.data_quality.generate_report` called directly on an
  ingestion-only run (no enrichment/scoring) stores a report with the correct
  total / valid / invalid counts and zero enriched/scored counts.

Database / safety: identical to the other Task 27 integration modules — URL from
``DATABASE_URL`` (→ ``SMOKE_TEST_DATABASE_URL``), only a dedicated test/smoke/ci
database is truncated, tables cleaned before and after each test, mock LLM only
(no OpenAI key, network or real API call).
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from src.database.repository import PipelineRepository
from src.database.session import get_engine
from src.ingestion.csv_ingestion import ingest_csv_file
from src.pipeline.data_quality import generate_report

# --------------------------------------------------------------------------- #
# Local integration helpers (kept in-file per Task 27).
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = _REPO_ROOT / "migrations" / "001_initial_schema.sql"

_PROJECT_TABLES = (
    "pipeline_runs",
    "raw_leads",
    "validated_leads",
    "enrichments",
    "scored_leads",
    "data_quality_reports",
    "validation_errors",
)

_SAFE_DB_TOKENS = ("test", "smoke", "ci")

_CSV_HEADER = (
    "company_name",
    "contact_email",
    "contact_phone",
    "product_category",
    "annual_revenue",
    "target_market",
)


def get_test_database_url() -> str | None:
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SMOKE_TEST_DATABASE_URL")
        or ""
    ).strip()
    return url or None


def _is_safe_target(database_url: str) -> bool:
    lowered = database_url.lower()
    return any(token in lowered for token in _SAFE_DB_TOKENS)


def run_migration_sql_if_needed(engine) -> None:
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def truncate_project_tables(engine) -> None:
    statement = (
        "TRUNCATE TABLE "
        + ", ".join(_PROJECT_TABLES)
        + " RESTART IDENTITY CASCADE"
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(statement)


def _count(session, table: str) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0)


def _success_enrichments(session) -> int:
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM enrichments "
                "WHERE enrichment_status = 'success'"
            )
        ).scalar()
        or 0
    )


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in _CSV_HEADER})
    return path


@pytest.fixture()
def db_engine():
    database_url = get_test_database_url()
    if database_url is None:
        pytest.skip(
            "DATABASE_URL is not set; skipping PostgreSQL integration tests "
            "(set DATABASE_URL to a dedicated test/smoke/ci database to run them)."
        )
    if not _is_safe_target(database_url):
        pytest.skip(
            "DATABASE_URL does not name a dedicated test database (its URL must "
            "contain 'test', 'smoke' or 'ci'); refusing to truncate it."
        )

    engine = get_engine(database_url)
    run_migration_sql_if_needed(engine)
    truncate_project_tables(engine)
    try:
        yield engine
    finally:
        truncate_project_tables(engine)
        engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


def _seed_pipeline_run(session_factory, file_path: str) -> str:
    run_id = str(uuid4())
    session = session_factory()
    try:
        repository = PipelineRepository(session)
        repository.create_pipeline_run(
            pipeline_run_id=run_id,
            started_at=datetime.now(timezone.utc),
            file_path=file_path,
            status="in_progress",
        )
        session.commit()
    finally:
        session.close()
    return run_id


# A CSV with 2 valid distinct identities + 1 invalid row (missing contact_email).
_ROWS = [
    {
        "company_name": "Aurora Coatings",
        "contact_email": "export@aurora.example",
        "contact_phone": "+90 212 555 0101",
        "product_category": "Industrial Coatings",
        "annual_revenue": "4200000",
        "target_market": "EU",
    },
    {
        "company_name": "Bosphorus Textiles",
        "contact_email": "sales@bosphorus.example",
        "contact_phone": "+90 212 555 0102",
        "product_category": "Textiles",
        "annual_revenue": "1875000",
        "target_market": "Middle East",
    },
    {
        "company_name": "Summit Grain Traders",
        "contact_email": "",
        "contact_phone": "+1 312 555 0119",
        "product_category": "Grain",
        "annual_revenue": "3300000",
        "target_market": "Africa",
    },
]
_EXPECTED_VALID = 2
_EXPECTED_INVALID = 1


def test_pipeline_report_counts_match_live_table_counts(
    session_factory, tmp_path, monkeypatch
):
    """The run's stored report matches the actual per-table counts, and is retrievable."""
    database_url = get_test_database_url()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MOCK_LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    csv_path = _write_csv(tmp_path / "leads.csv", rows=_ROWS)

    from src.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session_factory=session_factory)
    result = orchestrator.run(csv_path)
    assert result.status == "completed"

    verify = session_factory()
    try:
        # Exactly one report for the run.
        assert _count(verify, "data_quality_reports") == 1

        # The live per-stage counts we will compare the report against.
        raw_total = _count(verify, "raw_leads")
        validated = _count(verify, "validated_leads")
        invalid = _count(verify, "validation_errors")
        enriched = _success_enrichments(verify)
        scored = _count(verify, "scored_leads")

        # Retrieval through the repository returns the stored report.
        report = PipelineRepository(verify).get_quality_report(
            result.pipeline_run_id
        )
        assert report is not None
        assert report.total_records == raw_total == _EXPECTED_VALID
        assert report.valid_records == validated == _EXPECTED_VALID
        assert report.invalid_records == invalid == _EXPECTED_INVALID
        assert report.enriched_records == enriched == _EXPECTED_VALID
        assert report.failed_enrichments == 0
        assert report.scored_records == scored == _EXPECTED_VALID
    finally:
        verify.close()


def test_generate_report_directly_on_ingestion_only_run(
    session_factory, tmp_path
):
    """`generate_report` called directly records ingestion counts with zero enrichment.

    No enrichment or scoring runs here, so the report must show the total/valid/
    invalid ingestion counts with ``enriched_records`` and ``scored_records`` at 0
    — exercising report generation independently of the orchestrator.
    """
    csv_path = _write_csv(tmp_path / "leads.csv", rows=_ROWS)
    run_id = _seed_pipeline_run(session_factory, str(csv_path))

    session = session_factory()
    try:
        ingest_csv_file(str(csv_path), run_id, PipelineRepository(session))
        report_result = generate_report(run_id, session)
        session.commit()
    finally:
        session.close()

    # The returned result carries the ingestion-only counts.
    assert report_result.total_records == _EXPECTED_VALID
    assert report_result.valid_records == _EXPECTED_VALID
    assert report_result.invalid_records == _EXPECTED_INVALID
    assert report_result.enriched_records == 0
    assert report_result.failed_enrichments == 0
    assert report_result.scored_records == 0

    # The row is persisted and retrievable through the repository.
    verify = session_factory()
    try:
        assert _count(verify, "data_quality_reports") == 1
        stored = PipelineRepository(verify).get_quality_report(run_id)
        assert stored is not None
        assert stored.total_records == _EXPECTED_VALID
        assert stored.invalid_records == _EXPECTED_INVALID
        assert stored.scored_records == 0
    finally:
        verify.close()
