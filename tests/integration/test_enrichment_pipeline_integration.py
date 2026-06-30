"""
Integration test — full pipeline with the mock LLM (Task 27).

Runs :class:`src.pipeline.orchestrator.PipelineOrchestrator` end-to-end against a
real PostgreSQL database using the deterministic mock LLM
(``MOCK_LLM_ENABLED=true``) and asserts that every stage table is populated:
``pipeline_runs`` (completed), ``raw_leads``, ``validated_leads``,
``enrichments`` (with the mock model name), ``scored_leads``,
``data_quality_reports`` and ``validation_errors`` (the input CSV deliberately
includes one invalid row).

Database
--------
The URL is read from the environment (``DATABASE_URL`` → ``SMOKE_TEST_DATABASE_URL``);
the module is skipped when neither is set, and only a dedicated ``test`` /
``smoke`` / ``ci`` database is ever truncated. Tables are cleaned before and
after each test.

Safety
------
No real OpenAI call, no network and no ``OPENAI_API_KEY`` are required — the
orchestrator's default enrichment module runs in mock mode, set explicitly via
``MOCK_LLM_ENABLED=true`` so any ambient ``.env`` cannot flip it.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from src.database.session import get_engine
from src.enrichment.mock_llm import MODEL_NAME as MOCK_MODEL_NAME

# --------------------------------------------------------------------------- #
# Local integration helpers (kept in-file per Task 27; see the sibling test
# modules — the same small helpers are intentionally repeated, not shared).
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
    """Return the integration DB URL from the environment, or ``None``."""
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


# A small CSV with a known composition:
#   * 3 valid, distinct business identities,
#   * 1 invalid row (missing product_category),
#   * 1 duplicate of the first identity (skipped in skip mode).
_PIPELINE_ROWS = [
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
        "company_name": "Cedar Valley Organics",
        "contact_email": "trade@cedarvalley.example",
        "contact_phone": "+1 503 555 0103",
        "product_category": "Organic Foods",
        "annual_revenue": "990000",
        "target_market": "North America",
    },
    # Invalid: missing the required product_category.
    {
        "company_name": "Tempest Wind",
        "contact_email": "export@tempest-wind.example",
        "contact_phone": "+49 30 555 0120",
        "product_category": "",
        "annual_revenue": "9600000",
        "target_market": "EU",
    },
    # Duplicate identity of the first row — skipped.
    {
        "company_name": "Aurora Coatings",
        "contact_email": "export@aurora.example",
        "contact_phone": "+90 212 555 0199",
        "product_category": "Industrial Coatings",
        "annual_revenue": "4350000",
        "target_market": "EU",
    },
]

_EXPECTED_INSERTED = 3   # 3 distinct valid identities (duplicate skipped)
_EXPECTED_INVALID = 1    # 1 schema-invalid row


def test_full_pipeline_with_mock_llm_populates_all_tables(
    session_factory, tmp_path, monkeypatch
):
    """One mock-LLM run fills every stage table and finishes ``completed``."""
    database_url = get_test_database_url()
    # Force mock mode and a keyless config regardless of any ambient .env, so the
    # orchestrator's default LLMEnrichmentModule never builds an OpenAI client.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MOCK_LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    csv_path = _write_csv(tmp_path / "leads.csv", rows=_PIPELINE_ROWS)

    # Imported here so importing the module has no settings/DB side effects.
    from src.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session_factory=session_factory)
    result = orchestrator.run(csv_path)

    # --- Run-level result --------------------------------------------------- #
    assert result.status == "completed", f"unexpected status: {result.status}"
    assert result.error_message is None
    assert result.valid_records == _EXPECTED_INSERTED
    assert result.invalid_records == _EXPECTED_INVALID
    assert result.enriched_records == _EXPECTED_INSERTED
    assert result.failed_enrichments == 0
    assert result.scored_records == _EXPECTED_INSERTED

    # --- Every stage table is populated ------------------------------------ #
    verify = session_factory()
    try:
        assert _count(verify, "pipeline_runs") == 1
        run_status = verify.execute(
            text("SELECT status FROM pipeline_runs WHERE pipeline_run_id = :rid"),
            {"rid": result.pipeline_run_id},
        ).scalar()
        assert run_status == "completed"

        assert _count(verify, "raw_leads") == _EXPECTED_INSERTED
        assert _count(verify, "validated_leads") == _EXPECTED_INSERTED
        assert _count(verify, "validation_errors") == _EXPECTED_INVALID

        assert _count(verify, "enrichments") == _EXPECTED_INSERTED
        success = verify.execute(
            text(
                "SELECT count(*) FROM enrichments "
                "WHERE enrichment_status = 'success'"
            )
        ).scalar()
        assert int(success) == _EXPECTED_INSERTED

        # Enrichments record the mock provider's model name.
        models = {
            row[0]
            for row in verify.execute(
                text("SELECT DISTINCT model_name FROM enrichments")
            ).all()
        }
        assert models == {MOCK_MODEL_NAME}

        assert _count(verify, "scored_leads") == _EXPECTED_INSERTED
        assert _count(verify, "data_quality_reports") == 1
    finally:
        verify.close()
