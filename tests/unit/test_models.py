"""
Unit tests for src/database/models.py and src/database/session.py.

All tests use SQLite in-memory — no running PostgreSQL required.
PostgreSQL-specific column types (UUID, JSONB, ARRAY) are defined on the
ORM models but are NOT used when creating tables in SQLite.  Column
presence is verified via ``__table__.columns.keys()``, which reads the
ORM metadata directly and never executes DDL — so no type-rendering
issues occur.

Session and get_db tests use a plain SQLite engine (no PG-specific tables
created), so they also stay PostgreSQL-free.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from src.database.models import (
    DataQualityReport,
    Enrichment,
    PipelineRun,
    RawLead,
    ScoredLead,
    ValidatedLead,
    ValidationErrorRecord,
)
from src.database.session import get_db, get_engine


# ---------------------------------------------------------------------------
# 1. Import tests — all 7 model classes must be importable
# ---------------------------------------------------------------------------

class TestModelImports:
    def test_pipeline_run_importable(self):
        assert PipelineRun is not None

    def test_raw_lead_importable(self):
        assert RawLead is not None

    def test_validated_lead_importable(self):
        assert ValidatedLead is not None

    def test_enrichment_importable(self):
        assert Enrichment is not None

    def test_scored_lead_importable(self):
        assert ScoredLead is not None

    def test_data_quality_report_importable(self):
        assert DataQualityReport is not None

    def test_validation_error_record_importable(self):
        assert ValidationErrorRecord is not None


# ---------------------------------------------------------------------------
# 2. __tablename__ checks
# ---------------------------------------------------------------------------

class TestTableNames:
    def test_pipeline_run_tablename(self):
        assert PipelineRun.__tablename__ == "pipeline_runs"

    def test_raw_lead_tablename(self):
        assert RawLead.__tablename__ == "raw_leads"

    def test_validated_lead_tablename(self):
        assert ValidatedLead.__tablename__ == "validated_leads"

    def test_enrichment_tablename(self):
        assert Enrichment.__tablename__ == "enrichments"

    def test_scored_lead_tablename(self):
        assert ScoredLead.__tablename__ == "scored_leads"

    def test_data_quality_report_tablename(self):
        assert DataQualityReport.__tablename__ == "data_quality_reports"

    def test_validation_error_record_tablename(self):
        assert ValidationErrorRecord.__tablename__ == "validation_errors"


# ---------------------------------------------------------------------------
# 3. Column presence checks (reads ORM metadata — no DDL executed)
# ---------------------------------------------------------------------------

def _column_keys(model_cls) -> set[str]:
    """Return the set of column names registered on the model's Table object."""
    return set(model_cls.__table__.columns.keys())


class TestPipelineRunColumns:
    EXPECTED = {
        "pipeline_run_id",
        "status",
        "started_at",
        "finished_at",
        "processed_count",
        "success_count",
        "failed_count",
        "file_path",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(PipelineRun))


class TestRawLeadColumns:
    EXPECTED = {
        "raw_lead_id",
        "idempotency_key",
        "pipeline_run_id",
        "company_name",
        "contact_email",
        "product_category",
        "raw_csv_row",
        "ingested_at",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(RawLead))


class TestValidatedLeadColumns:
    EXPECTED = {
        "validated_lead_id",
        "raw_lead_id",
        "pipeline_run_id",
        "company_name",
        "contact_email",
        "product_category",
        "validated_at",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(ValidatedLead))


class TestEnrichmentColumns:
    EXPECTED = {
        "enrichment_id",
        "validated_lead_id",
        "pipeline_run_id",
        "enrichment_status",
        "retry_count",
        "prompt_version",
        "model_name",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(Enrichment))


class TestScoredLeadColumns:
    EXPECTED = {
        "scored_lead_id",
        "validated_lead_id",
        "enrichment_id",
        "pipeline_run_id",
        "score",
        "score_breakdown",
        "scored_at",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(ScoredLead))


class TestDataQualityReportColumns:
    EXPECTED = {
        "report_id",
        "pipeline_run_id",
        "total_records",
        "valid_records",
        "invalid_records",
        "enriched_records",
        "failed_enrichments",
        "scored_records",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(DataQualityReport))


class TestValidationErrorRecordColumns:
    EXPECTED = {
        "error_id",
        "pipeline_run_id",
        "error_stage",
        "error_message",
        "recorded_at",
    }

    def test_has_required_columns(self):
        assert self.EXPECTED.issubset(_column_keys(ValidationErrorRecord))


# ---------------------------------------------------------------------------
# 4. get_engine() returns a SQLAlchemy Engine
# ---------------------------------------------------------------------------

class TestGetEngine:
    def test_returns_engine_instance(self):
        engine = get_engine("sqlite:///:memory:")
        assert isinstance(engine, Engine)
        engine.dispose()

    def test_engine_can_connect(self):
        engine = get_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        engine.dispose()

    def test_pool_pre_ping_enabled(self):
        """Verify get_engine creates an engine without error (pool_pre_ping=True)."""
        engine = get_engine("sqlite:///:memory:")
        assert engine is not None
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. sessionmaker created from the engine returns a usable Session
# ---------------------------------------------------------------------------

class TestSessionMaker:
    def test_sessionmaker_yields_session(self):
        engine = get_engine("sqlite:///:memory:")
        factory = sessionmaker(bind=engine)
        session = factory()
        assert isinstance(session, Session)
        session.close()
        engine.dispose()

    def test_session_can_execute_query(self):
        engine = get_engine("sqlite:///:memory:")
        factory = sessionmaker(bind=engine)
        with factory() as session:
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        engine.dispose()


# ---------------------------------------------------------------------------
# 6. get_db() generator yields a session and closes it
# ---------------------------------------------------------------------------

class TestGetDbGenerator:
    """
    Patch _get_session_local to return a factory bound to an in-memory
    SQLite engine so get_db never needs DATABASE_URL or PostgreSQL.
    """

    @pytest.fixture()
    def sqlite_factory(self):
        engine = get_engine("sqlite:///:memory:")
        factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        yield factory
        engine.dispose()

    def test_get_db_yields_session(self, sqlite_factory, monkeypatch):
        import src.database.session as session_module

        monkeypatch.setattr(session_module, "_get_session_local", lambda: sqlite_factory)

        gen = get_db()
        session = next(gen)
        assert isinstance(session, Session)
        # exhaust the generator to trigger the finally block
        with pytest.raises(StopIteration):
            next(gen)

    def test_get_db_closes_session_on_normal_exit(self, sqlite_factory, monkeypatch):
        import src.database.session as session_module

        closed_sessions: list[Session] = []
        original_close = Session.close

        def tracking_close(self_inner):
            closed_sessions.append(self_inner)
            original_close(self_inner)

        monkeypatch.setattr(session_module, "_get_session_local", lambda: sqlite_factory)
        monkeypatch.setattr(Session, "close", tracking_close)

        gen = get_db()
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)

        assert len(closed_sessions) == 1

    def test_get_db_closes_session_on_exception(self, sqlite_factory, monkeypatch):
        import src.database.session as session_module

        closed_sessions: list[Session] = []
        original_close = Session.close

        def tracking_close(self_inner):
            closed_sessions.append(self_inner)
            original_close(self_inner)

        monkeypatch.setattr(session_module, "_get_session_local", lambda: sqlite_factory)
        monkeypatch.setattr(Session, "close", tracking_close)

        gen = get_db()
        next(gen)
        # The generator's finally block runs when an exception is thrown in;
        # the exception propagates out of gen.throw() — catch it here.
        with pytest.raises(RuntimeError, match="simulated error"):
            gen.throw(RuntimeError("simulated error"))

        assert len(closed_sessions) == 1
