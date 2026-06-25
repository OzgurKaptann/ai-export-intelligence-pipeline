"""
Unit tests for src/logging_config.py.

These tests are fully hermetic: no database, API, Docker or external services.
They exercise the public logging API (configure_logging / get_logger /
bind_pipeline_context) and assert on structlog's in-memory state, so they are
deterministic and never depend on captured timestamps or colours.

Documented behaviour under test: an invalid log level raises a clear
``ValueError`` (we fail loudly rather than silently falling back to a default).
"""

import json
import os
import sys

import pytest
import structlog

# Ensure the project root is on sys.path when running from any directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.logging_config import (  # noqa: E402
    bind_pipeline_context,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _reset_structlog():
    """Restore structlog to its default configuration after each test."""
    yield
    structlog.reset_defaults()


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------

def test_configure_logging_runs_without_error():
    configure_logging()
    # A logger obtained afterwards must be usable.
    logger = get_logger("test")
    logger.info("hello")


def test_configure_logging_json_does_not_crash():
    configure_logging(json_logs=True)
    logger = get_logger("test")
    logger.info("json-event", foo="bar")


def test_json_logs_emit_valid_json(capsys):
    configure_logging(json_logs=True, log_level="INFO")
    get_logger("test").info("structured", key="value")
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["event"] == "structured"
    assert payload["key"] == "value"
    assert payload["level"] == "info"


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_valid_log_levels_accepted(level):
    # Both upper-case and lower-case spellings are accepted.
    configure_logging(log_level=level)
    configure_logging(log_level=level.lower())


def test_log_level_filters_below_threshold(capsys):
    configure_logging(log_level="WARNING")
    logger = get_logger("test")
    logger.info("should-be-filtered")
    logger.warning("should-appear")
    out = capsys.readouterr().out
    assert "should-be-filtered" not in out
    assert "should-appear" in out


@pytest.mark.parametrize("bad_level", ["VERBOSE", "trace", "", "10"])
def test_invalid_log_level_raises_valueerror(bad_level):
    with pytest.raises(ValueError):
        configure_logging(log_level=bad_level)


def test_repeated_configure_does_not_break_logging(capsys):
    configure_logging(json_logs=False)
    configure_logging(json_logs=True)
    configure_logging(log_level="DEBUG", json_logs=True)
    get_logger("test").debug("still-works", n=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["event"] == "still-works"
    assert payload["n"] == 1


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------

def test_get_logger_returns_usable_logger():
    configure_logging()
    logger = get_logger()
    assert hasattr(logger, "info")
    assert hasattr(logger, "bind")
    # Calling it must not raise.
    logger.info("usable")


def test_get_logger_without_name():
    configure_logging()
    logger = get_logger()
    logger.info("anonymous")


# ---------------------------------------------------------------------------
# bind_pipeline_context
# ---------------------------------------------------------------------------

def test_bind_pipeline_context_includes_both_fields():
    configure_logging()
    logger = bind_pipeline_context(
        get_logger("test"), pipeline_run_id="run-123", component="ingestion"
    )
    assert logger._context["pipeline_run_id"] == "run-123"
    assert logger._context["component"] == "ingestion"


def test_bind_pipeline_context_partial():
    configure_logging()
    logger = bind_pipeline_context(get_logger("test"), pipeline_run_id="run-9")
    assert logger._context["pipeline_run_id"] == "run-9"
    assert "component" not in logger._context


def test_bind_pipeline_context_none_leaves_context_empty():
    configure_logging()
    base = get_logger("test")
    bound = bind_pipeline_context(base)
    assert "pipeline_run_id" not in bound._context
    assert "component" not in bound._context


def test_bound_context_appears_in_output(capsys):
    configure_logging(json_logs=True)
    logger = bind_pipeline_context(
        get_logger("test"), pipeline_run_id="run-abc", component="scoring"
    )
    logger.info("processing")
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["pipeline_run_id"] == "run-abc"
    assert payload["component"] == "scoring"
