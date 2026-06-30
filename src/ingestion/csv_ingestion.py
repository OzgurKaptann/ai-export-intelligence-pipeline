"""
CSV ingestion for export lead records.

This module is the entry point of the pipeline's data layer.  It reads a
CSV file of raw export leads, validates every row against
:class:`RawLeadSchema`, and persists the outcome through the repository:

* valid rows are written to ``raw_leads`` **and** ``validated_leads`` in the
  same logical step, after a deterministic idempotency key is generated;
* invalid rows are recorded in ``validation_errors`` and never reach
  ``raw_leads`` or ``validated_leads``.

The module is pure application logic: it only parses CSV, validates with
Pydantic, generates idempotency keys and calls :class:`PipelineRepository`.
It never opens a database session and never touches the database directly —
a repository instance is injected by the caller.

Row-level validation failures are isolated, so one malformed row cannot stop
the rest of the file from being processed.  File-level errors (for example a
missing file) are *not* swallowed and propagate to the caller.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Union
from uuid import UUID, uuid4

from pydantic import ValidationError

from src.database.repository import PipelineRepository
from src.ingestion.idempotency import generate_idempotency_key
from src.validation.input_schemas import IngestionResult, RawLeadSchema

# Fields carried from a validated lead into the raw_leads / validated_leads
# rows, in addition to the always-present required fields.
_OPTIONAL_FIELDS = ("contact_phone", "annual_revenue", "target_market")

# error_stage value recorded for schema validation failures during ingestion.
_ERROR_STAGE = "validation"


def _utcnow() -> datetime:
    """Timezone-aware current time, set explicitly so tests need no DB defaults."""
    return datetime.now(timezone.utc)


def _coerce_run_id(pipeline_run_id: str) -> Union[UUID, str]:
    """Return ``pipeline_run_id`` as a UUID when it parses, else unchanged."""
    try:
        return UUID(str(pipeline_run_id))
    except (ValueError, TypeError, AttributeError):
        return pipeline_run_id


def _describe_error(exc: ValidationError) -> tuple[str | None, str]:
    """Reduce a Pydantic ``ValidationError`` to a (field, message) pair.

    The first reported error is used for attribution; its location becomes the
    ``error_field`` and its message the ``error_message``.
    """
    errors = exc.errors()
    if not errors:
        return None, str(exc)
    first = errors[0]
    loc = first.get("loc") or ()
    field = str(loc[0]) if loc else None
    message = first.get("msg", "validation error")
    return field, message


def ingest_csv_file(
    file_path: Union[str, Path],
    pipeline_run_id: str,
    repository: PipelineRepository,
) -> IngestionResult:
    """Ingest a CSV file of export leads through ``repository``.

    Parameters
    ----------
    file_path:
        Path to a UTF-8 encoded CSV file with a header row.  Required columns:
        ``company_name``, ``contact_email``, ``product_category``.  Optional
        columns: ``contact_phone``, ``annual_revenue``, ``target_market``.
    pipeline_run_id:
        Identifier of the pipeline run the ingested rows belong to.
    repository:
        Injected :class:`PipelineRepository`.  All persistence is delegated to
        its methods; no session is created here.

    Returns
    -------
    IngestionResult
        ``total`` rows read, ``inserted`` valid rows, ``failed`` invalid rows.
        ``skipped`` counts valid rows whose business identity (idempotency
        key) was already ingested in this run — skip-mode duplicate handling.

    Raises
    ------
    FileNotFoundError
        If ``file_path`` does not exist.  File-level errors are not swallowed.
    """
    result = IngestionResult(pipeline_run_id=_coerce_run_id(pipeline_run_id))

    with open(file_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            # Preserve the original parsed row exactly as it appeared in the CSV.
            raw_csv_row = dict(row)
            result.total += 1
            try:
                inserted = _ingest_row(raw_csv_row, pipeline_run_id, repository)
            except ValidationError as exc:
                _record_validation_error(
                    raw_csv_row, pipeline_run_id, repository, exc
                )
                result.failed += 1
            else:
                if inserted:
                    result.inserted += 1
                else:
                    result.skipped += 1

    return result


def _ingest_row(
    raw_csv_row: dict,
    pipeline_run_id: str,
    repository: PipelineRepository,
) -> bool:
    """Validate and persist a single valid row.

    Returns ``True`` when the row was inserted into ``raw_leads`` /
    ``validated_leads`` and ``False`` when it was skipped as a duplicate of an
    already-ingested business identity (skip mode).  Skipping the duplicate
    avoids violating the ``raw_leads.idempotency_key`` unique constraint.

    Raises :class:`ValidationError` if the row fails schema validation; the
    caller is responsible for recording the failure so that one bad row does
    not abort the file.
    """
    lead = RawLeadSchema.model_validate(raw_csv_row)
    lead_data = lead.model_dump()

    idempotency_key = generate_idempotency_key(lead)
    if _is_duplicate(repository, idempotency_key):
        return False

    raw_lead_id = str(uuid4())
    validated_lead_id = str(uuid4())
    now = _utcnow()

    raw_lead = {
        "raw_lead_id": raw_lead_id,
        "idempotency_key": idempotency_key,
        "pipeline_run_id": pipeline_run_id,
        "company_name": lead_data["company_name"],
        "contact_email": lead_data["contact_email"],
        "product_category": lead_data["product_category"],
        "raw_csv_row": raw_csv_row,
        "ingested_at": now,
    }
    for field in _OPTIONAL_FIELDS:
        raw_lead[field] = lead_data[field]
    repository.insert_raw_lead(raw_lead)

    validated_lead = {
        "validated_lead_id": validated_lead_id,
        "raw_lead_id": raw_lead_id,
        "pipeline_run_id": pipeline_run_id,
        "company_name": lead_data["company_name"],
        "contact_email": lead_data["contact_email"],
        "product_category": lead_data["product_category"],
        "validated_at": now,
    }
    for field in _OPTIONAL_FIELDS:
        validated_lead[field] = lead_data[field]
    repository.insert_validated_lead(validated_lead)

    return True


def _is_duplicate(repository: PipelineRepository, idempotency_key: str) -> bool:
    """Return ``True`` when a raw_lead with *idempotency_key* already exists.

    Skip-mode duplicate detection delegates to the repository's
    ``get_raw_lead_by_idempotency_key`` lookup.  When the injected repository
    does not expose that method (for example a minimal test double), duplicate
    detection is disabled and every validated row is treated as new — the
    smallest possible behaviour that keeps existing callers working.
    """
    lookup = getattr(repository, "get_raw_lead_by_idempotency_key", None)
    if lookup is None:
        return False
    return lookup(idempotency_key) is not None


def _record_validation_error(
    raw_csv_row: dict,
    pipeline_run_id: str,
    repository: PipelineRepository,
    exc: ValidationError,
) -> None:
    """Persist a validation failure for an invalid row."""
    field, message = _describe_error(exc)
    repository.insert_validation_error(
        {
            "error_id": str(uuid4()),
            "pipeline_run_id": pipeline_run_id,
            "raw_lead_id": None,
            "error_stage": _ERROR_STAGE,
            "error_field": field,
            "error_message": message,
            "recorded_at": _utcnow(),
        }
    )
