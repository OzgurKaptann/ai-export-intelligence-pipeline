"""
Structured logging setup for the AI Export Intelligence Pipeline.

A thin wrapper around :mod:`structlog` that gives the rest of the codebase a
small, stable API for application-wide logging:

* :func:`configure_logging` installs the structlog processor chain — human
  readable console output by default, JSON when ``json_logs=True``.
* :func:`get_logger` returns a structlog logger.
* :func:`bind_pipeline_context` binds the pipeline run id / component fields
  that recur throughout the pipeline.

Importing this module has **no side effects**: nothing is configured until
:func:`configure_logging` is called explicitly (typically at an application
entry point).  The functions never open files and never touch the database,
so they are safe to use from unit tests and from any process — API, worker or
one-off script — without dragging in FastAPI, Streamlit or Docker.
"""

from __future__ import annotations

import logging
from typing import Optional

import structlog

# Log levels we accept, mapped to their stdlib numeric value.  Used both to
# validate the requested level and to set the structlog filter threshold.
_VALID_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_level(log_level: str) -> int:
    """Map a level name to its numeric value.

    Raises :class:`ValueError` with a clear message for anything outside
    :data:`_VALID_LEVELS`; we fail loudly rather than silently defaulting so
    misconfiguration is caught at setup time.
    """
    if not isinstance(log_level, str):
        raise ValueError(f"log_level must be a string, got {type(log_level).__name__}")
    key = log_level.strip().upper()
    if key not in _VALID_LEVELS:
        valid = ", ".join(sorted(_VALID_LEVELS))
        raise ValueError(
            f"invalid log_level {log_level!r}; expected one of: {valid}"
        )
    return _VALID_LEVELS[key]


def configure_logging(log_level: str = "INFO", json_logs: bool = False) -> None:
    """Configure structlog for the whole application.

    Parameters
    ----------
    log_level:
        Minimum level to emit, e.g. ``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
        ``"ERROR"`` or ``"CRITICAL"`` (case-insensitive).  Invalid values raise
        :class:`ValueError`.
    json_logs:
        When ``True`` render each event as a single JSON line (machine
        readable, suited to production/aggregation).  When ``False`` (default)
        render colourised, human-readable console output.

    The call is idempotent and safe to invoke more than once — each call simply
    re-installs the processor chain, so tests may reconfigure freely.  No files
    are opened and no I/O beyond writing to the standard logging stream occurs.
    """
    level = _resolve_level(log_level)

    renderer: structlog.types.Processor
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: Optional[str] = None):
    """Return a structlog logger.

    ``name`` is recorded under the ``logger`` key so log lines can be traced to
    their origin; when omitted structlog infers a sensible default.  This may be
    called before :func:`configure_logging` — structlog lazily applies whatever
    configuration is active when the logger is first used.
    """
    if name is None:
        return structlog.get_logger()
    return structlog.get_logger(name)


def bind_pipeline_context(
    logger,
    pipeline_run_id: Optional[str] = None,
    component: Optional[str] = None,
):
    """Return ``logger`` with pipeline context bound.

    Only the fields that are provided are bound, so callers can attach a
    ``pipeline_run_id``, a ``component`` or both.  The original logger is left
    untouched; the bound logger is returned (structlog loggers are immutable).
    """
    bindings = {}
    if pipeline_run_id is not None:
        bindings["pipeline_run_id"] = pipeline_run_id
    if component is not None:
        bindings["component"] = component
    if not bindings:
        return logger
    return logger.bind(**bindings)
