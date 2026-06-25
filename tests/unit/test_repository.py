"""
Unit tests for src/database/repository.py.

Strategy
--------
SQLite in-memory is used for all tests so no running PostgreSQL is needed.

PostgreSQL-specific column types (UUID, JSONB, ARRAY) used in models.py
do NOT render to SQLite DDL correctly.  To sidestep this, the tests
create lightweight plain-SQL tables in SQLite that mirror the columns
used by each repository method, then insert/query through raw SQL to
verify data round-trips.

What IS tested here
  - All 12 repository methods exist on PipelineRepository
  - PipelineRepository accepts a Session as its only constructor arg
  - create_pipeline_run inserts a row retrievable by get_pipeline_run
  - update_pipeline_run updates a column value
  - insert_raw_lead inserts a row retrievable by get_raw_lead_by_idempotency_key
  - insert_validated_lead inserts a row retrievable via get_validated_leads_for_run
  - insert_enrichment inserts a row
  - update_enrichment updates a column
  - insert_scored_lead inserts a row retrievable by get_scored_lead_by_id
  - get_scored_leads returns rows filtered by min_score
  - insert_quality_report inserts a row retrievable by get_quality_report
  - insert_validation_error inserts a row

What is NOT tested here (deferred to integration tests)
  - JSONB column contents
  - ARRAY column contents
  - UUID server-side defaults
  - UNIQUE constraint on idempotency_key
  - Index performance
  - Foreign-key cascade behaviour
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.database.repository import PipelineRepository


# ---------------------------------------------------------------------------
# SQLite-compatible schema helpers
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    pipeline_run_id TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'in_progress',
    processed_count INTEGER NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    file_path       TEXT NOT NULL,
    run_metadata    TEXT
);

CREATE TABLE IF NOT EXISTS raw_leads (
    raw_lead_id       TEXT PRIMARY KEY,
    idempotency_key   TEXT NOT NULL UNIQUE,
    pipeline_run_id   TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    contact_email     TEXT NOT NULL,
    contact_phone     TEXT,
    product_category  TEXT NOT NULL,
    annual_revenue    REAL,
    target_market     TEXT,
    raw_csv_row       TEXT NOT NULL,
    ingested_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validated_leads (
    validated_lead_id TEXT PRIMARY KEY,
    raw_lead_id       TEXT NOT NULL,
    pipeline_run_id   TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    contact_email     TEXT NOT NULL,
    contact_phone     TEXT,
    product_category  TEXT NOT NULL,
    annual_revenue    REAL,
    target_market     TEXT,
    validated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichments (
    enrichment_id         TEXT PRIMARY KEY,
    validated_lead_id     TEXT NOT NULL,
    pipeline_run_id       TEXT NOT NULL,
    enrichment_status     TEXT NOT NULL,
    market_potential      REAL,
    export_readiness      REAL,
    risk_assessment       TEXT,
    recommended_markets   TEXT,
    confidence_score      REAL,
    error_type            TEXT,
    error_message         TEXT,
    failed_at             TEXT,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    raw_llm_response      TEXT,
    prompt_version        TEXT NOT NULL,
    model_name            TEXT NOT NULL,
    enrichment_created_at TEXT
);

CREATE TABLE IF NOT EXISTS scored_leads (
    scored_lead_id    TEXT PRIMARY KEY,
    validated_lead_id TEXT NOT NULL,
    enrichment_id     TEXT NOT NULL,
    pipeline_run_id   TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    product_category  TEXT NOT NULL,
    score             REAL NOT NULL,
    score_breakdown   TEXT NOT NULL,
    scored_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    report_id           TEXT PRIMARY KEY,
    pipeline_run_id     TEXT NOT NULL UNIQUE,
    total_records       INTEGER NOT NULL,
    valid_records       INTEGER NOT NULL,
    invalid_records     INTEGER NOT NULL,
    enriched_records    INTEGER NOT NULL,
    failed_enrichments  INTEGER NOT NULL,
    scored_records      INTEGER NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_errors (
    error_id        TEXT PRIMARY KEY,
    pipeline_run_id TEXT NOT NULL,
    raw_lead_id     TEXT,
    error_stage     TEXT NOT NULL,
    error_field     TEXT,
    error_message   TEXT NOT NULL,
    recorded_at     TEXT NOT NULL
);
"""


@pytest.fixture(scope="function")
def session():
    """Return a fresh SQLite in-memory session with all 7 tables created."""
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.commit()

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    sess = factory()
    yield sess
    sess.close()
    engine.dispose()


@pytest.fixture(scope="function")
def repo(session):
    return PipelineRepository(session)


# ---------------------------------------------------------------------------
# Tiny factory helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_pipeline_run(repo: PipelineRepository, run_id: str | None = None) -> str:
    rid = run_id or _uid()
    repo.create_pipeline_run(
        pipeline_run_id=rid,
        started_at=_now(),
        file_path="data/sample/leads.csv",
    )
    repo._session.commit()
    return rid


def _make_raw_lead(
    repo: PipelineRepository,
    pipeline_run_id: str,
    idempotency_key: str | None = None,
    raw_lead_id: str | None = None,
) -> str:
    rlid = raw_lead_id or _uid()
    ikey = idempotency_key or _uid()
    repo.insert_raw_lead({
        "raw_lead_id": rlid,
        "idempotency_key": ikey,
        "pipeline_run_id": pipeline_run_id,
        "company_name": "Acme Corp",
        "contact_email": "hello@acme.example.com",
        "product_category": "Electronics",
        "raw_csv_row": "{}",          # TEXT in SQLite
        "ingested_at": _now(),        # datetime object, not string
    })
    repo._session.commit()
    return rlid


def _make_validated_lead(
    repo: PipelineRepository,
    pipeline_run_id: str,
    raw_lead_id: str,
    validated_lead_id: str | None = None,
) -> str:
    vlid = validated_lead_id or _uid()
    repo.insert_validated_lead({
        "validated_lead_id": vlid,
        "raw_lead_id": raw_lead_id,
        "pipeline_run_id": pipeline_run_id,
        "company_name": "Acme Corp",
        "contact_email": "hello@acme.example.com",
        "product_category": "Electronics",
        "validated_at": _now(),       # datetime object, not string
    })
    repo._session.commit()
    return vlid


def _make_enrichment(
    repo: PipelineRepository,
    pipeline_run_id: str,
    validated_lead_id: str,
    enrichment_id: str | None = None,
) -> str:
    eid = enrichment_id or _uid()
    repo.insert_enrichment({
        "enrichment_id": eid,
        "validated_lead_id": validated_lead_id,
        "pipeline_run_id": pipeline_run_id,
        "enrichment_status": "success",
        "prompt_version": "v1.0",
        "model_name": "mock-llm-v1",
        "retry_count": 0,
    })
    repo._session.commit()
    return eid


def _make_scored_lead(
    repo: PipelineRepository,
    pipeline_run_id: str,
    validated_lead_id: str,
    enrichment_id: str,
    score: float = 75.0,
    scored_lead_id: str | None = None,
) -> str:
    slid = scored_lead_id or _uid()
    repo.insert_scored_lead({
        "scored_lead_id": slid,
        "validated_lead_id": validated_lead_id,
        "enrichment_id": enrichment_id,
        "pipeline_run_id": pipeline_run_id,
        "company_name": "Acme Corp",
        "product_category": "Electronics",
        "score": score,
        "score_breakdown": "{}",      # TEXT in SQLite
        "scored_at": _now(),          # datetime object, not string
    })
    repo._session.commit()
    return slid


# ---------------------------------------------------------------------------
# 1. All 12 repository methods exist
# ---------------------------------------------------------------------------

REQUIRED_METHODS = [
    "insert_raw_lead",
    "get_raw_lead_by_idempotency_key",
    "insert_validated_lead",
    "insert_enrichment",
    "insert_scored_lead",
    "create_pipeline_run",
    "update_pipeline_run",
    "insert_quality_report",
    "get_scored_leads",
    "get_scored_lead_by_id",
    "get_quality_report",
    "insert_validation_error",
]


class TestRepositoryInterface:
    def test_all_required_methods_exist(self, repo):
        for method_name in REQUIRED_METHODS:
            assert hasattr(repo, method_name), f"Missing method: {method_name}"
            assert callable(getattr(repo, method_name))

    def test_constructor_takes_session(self):
        """PipelineRepository must declare `session` in its __init__ signature."""
        sig = inspect.signature(PipelineRepository.__init__)
        assert "session" in sig.parameters

    def test_no_global_session(self):
        """Repository must NOT import or use a global SessionLocal."""
        import src.database.repository as repo_module
        # Verify the module does not reference `SessionLocal` at module level
        source = inspect.getsource(repo_module)
        assert "SessionLocal" not in source


# ---------------------------------------------------------------------------
# 2. pipeline_runs CRUD
# ---------------------------------------------------------------------------

class TestPipelineRunCRUD:
    def test_create_and_retrieve(self, repo):
        rid = _make_pipeline_run(repo)
        run = repo.get_pipeline_run(rid)
        assert run is not None
        assert run.pipeline_run_id == rid
        assert run.status == "in_progress"
        assert run.file_path == "data/sample/leads.csv"
        assert run.processed_count == 0

    def test_update_status(self, repo):
        rid = _make_pipeline_run(repo)
        repo.update_pipeline_run(rid, {"status": "completed", "processed_count": 10})
        repo._session.commit()
        run = repo.get_pipeline_run(rid)
        assert run.status == "completed"
        assert run.processed_count == 10

    def test_update_nonexistent_raises(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.update_pipeline_run(_uid(), {"status": "completed"})

    def test_get_nonexistent_returns_none(self, repo):
        assert repo.get_pipeline_run(_uid()) is None


# ---------------------------------------------------------------------------
# 3. raw_leads
# ---------------------------------------------------------------------------

class TestRawLeadCRUD:
    def test_insert_and_retrieve_by_idempotency_key(self, repo):
        rid = _make_pipeline_run(repo)
        ikey = "sha256abc" + _uid().replace("-", "")[:55]
        rlid = _uid()
        repo.insert_raw_lead({
            "raw_lead_id": rlid,
            "idempotency_key": ikey,
            "pipeline_run_id": rid,
            "company_name": "Beta Ltd",
            "contact_email": "b@beta.example.com",
            "product_category": "Textiles",
            "raw_csv_row": "{}",
            "ingested_at": _now(),
        })
        repo._session.commit()

        found = repo.get_raw_lead_by_idempotency_key(ikey)
        assert found is not None
        assert found.raw_lead_id == rlid
        assert found.company_name == "Beta Ltd"

    def test_get_missing_idempotency_key_returns_none(self, repo):
        assert repo.get_raw_lead_by_idempotency_key("nonexistent_key") is None


# ---------------------------------------------------------------------------
# 4. validated_leads
# ---------------------------------------------------------------------------

class TestValidatedLeadCRUD:
    def test_insert_and_retrieve_for_run(self, repo):
        rid = _make_pipeline_run(repo)
        rlid = _make_raw_lead(repo, rid)
        vlid = _make_validated_lead(repo, rid, rlid)

        rows = repo.get_validated_leads_for_run(rid)
        assert len(rows) == 1
        assert rows[0].validated_lead_id == vlid

    def test_multiple_validated_leads_for_run(self, repo):
        rid = _make_pipeline_run(repo)
        for _ in range(3):
            rlid = _make_raw_lead(repo, rid)
            _make_validated_lead(repo, rid, rlid)

        rows = repo.get_validated_leads_for_run(rid)
        assert len(rows) == 3

    def test_no_rows_for_other_run(self, repo):
        rid1 = _make_pipeline_run(repo)
        rid2 = _make_pipeline_run(repo)
        rlid = _make_raw_lead(repo, rid1)
        _make_validated_lead(repo, rid1, rlid)

        assert repo.get_validated_leads_for_run(rid2) == []


# ---------------------------------------------------------------------------
# 5. enrichments
# ---------------------------------------------------------------------------

class TestEnrichmentCRUD:
    def test_insert_enrichment(self, repo):
        rid = _make_pipeline_run(repo)
        rlid = _make_raw_lead(repo, rid)
        vlid = _make_validated_lead(repo, rid, rlid)
        eid = _make_enrichment(repo, rid, vlid)

        # Use ORM get — stays in the same session identity map
        from src.database.models import Enrichment
        row = repo._session.get(Enrichment, eid)
        assert row is not None
        assert row.enrichment_status == "success"

    def test_update_enrichment(self, repo):
        rid = _make_pipeline_run(repo)
        rlid = _make_raw_lead(repo, rid)
        vlid = _make_validated_lead(repo, rid, rlid)
        eid = _make_enrichment(repo, rid, vlid)

        repo.update_enrichment(eid, {"enrichment_status": "validation_failed", "retry_count": 1})
        repo._session.commit()

        from src.database.models import Enrichment
        repo._session.expire_all()  # ensure fresh read
        row = repo._session.get(Enrichment, eid)
        assert row.enrichment_status == "validation_failed"
        assert row.retry_count == 1

    def test_update_nonexistent_enrichment_raises(self, repo):
        with pytest.raises(ValueError, match="not found"):
            repo.update_enrichment(_uid(), {"enrichment_status": "timeout"})


# ---------------------------------------------------------------------------
# 6. scored_leads
# ---------------------------------------------------------------------------

class TestScoredLeadCRUD:
    def test_insert_and_get_by_id(self, repo):
        rid = _make_pipeline_run(repo)
        rlid = _make_raw_lead(repo, rid)
        vlid = _make_validated_lead(repo, rid, rlid)
        eid = _make_enrichment(repo, rid, vlid)
        slid = _make_scored_lead(repo, rid, vlid, eid, score=82.5)

        found = repo.get_scored_lead_by_id(slid)
        assert found is not None
        assert found.score == 82.5

    def test_get_scored_leads_all(self, repo):
        rid = _make_pipeline_run(repo)
        for score in [30.0, 70.0, 55.0]:
            rlid = _make_raw_lead(repo, rid)
            vlid = _make_validated_lead(repo, rid, rlid)
            eid = _make_enrichment(repo, rid, vlid)
            _make_scored_lead(repo, rid, vlid, eid, score=score)

        leads = repo.get_scored_leads()
        assert len(leads) == 3

    def test_get_scored_leads_min_score_filter(self, repo):
        rid = _make_pipeline_run(repo)
        for score in [20.0, 60.0, 90.0]:
            rlid = _make_raw_lead(repo, rid)
            vlid = _make_validated_lead(repo, rid, rlid)
            eid = _make_enrichment(repo, rid, vlid)
            _make_scored_lead(repo, rid, vlid, eid, score=score)

        high = repo.get_scored_leads(min_score=61.0)
        assert len(high) == 1
        assert high[0].score == 90.0

    def test_get_scored_leads_ordered_desc(self, repo):
        rid = _make_pipeline_run(repo)
        for score in [10.0, 90.0, 50.0]:
            rlid = _make_raw_lead(repo, rid)
            vlid = _make_validated_lead(repo, rid, rlid)
            eid = _make_enrichment(repo, rid, vlid)
            _make_scored_lead(repo, rid, vlid, eid, score=score)

        leads = repo.get_scored_leads()
        scores = [float(l.score) for l in leads]
        assert scores == sorted(scores, reverse=True)

    def test_get_scored_lead_by_id_missing_returns_none(self, repo):
        assert repo.get_scored_lead_by_id(_uid()) is None


# ---------------------------------------------------------------------------
# 7. data_quality_reports
# ---------------------------------------------------------------------------

class TestDataQualityReportCRUD:
    def _report_dict(self, pipeline_run_id: str) -> dict:
        return {
            "report_id": _uid(),
            "pipeline_run_id": pipeline_run_id,
            "total_records": 20,
            "valid_records": 18,
            "invalid_records": 2,
            "enriched_records": 17,
            "failed_enrichments": 1,
            "scored_records": 17,
            "created_at": _now(),     # datetime object, not string
        }

    def test_insert_and_retrieve(self, repo):
        rid = _make_pipeline_run(repo)
        repo.insert_quality_report(self._report_dict(rid))
        repo._session.commit()

        report = repo.get_quality_report(rid)
        assert report is not None
        assert report.total_records == 20
        assert report.valid_records == 18

    def test_get_quality_report_missing_returns_none(self, repo):
        assert repo.get_quality_report(_uid()) is None


# ---------------------------------------------------------------------------
# 8. validation_errors
# ---------------------------------------------------------------------------

class TestValidationErrorCRUD:
    def test_insert_validation_error(self, repo):
        rid = _make_pipeline_run(repo)
        eid = repo.insert_validation_error({
            "error_id": _uid(),
            "pipeline_run_id": rid,
            "error_stage": "validation",
            "error_message": "contact_email is required",
            "recorded_at": _now(),
        })
        repo._session.commit()
        assert eid is not None

        from src.database.models import ValidationErrorRecord

        row = repo._session.get(ValidationErrorRecord, eid)

        assert row is not None
        assert row.error_stage == "validation"
        assert row.error_message == "contact_email is required"
        assert str(row.pipeline_run_id) == str(rid)
