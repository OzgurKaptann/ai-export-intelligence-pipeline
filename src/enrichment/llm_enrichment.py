"""
LLM enrichment module with a hard validation gate.

`LLMEnrichmentModule.enrich_lead` is the single entry point that turns one
validated lead into an :class:`Enrichment` record.  It:

* builds the enrichment prompt with :func:`build_enrichment_prompt`,
* obtains an enrichment from either the deterministic mock provider
  (``MOCK_LLM_ENABLED=true``) or a real LLM (``MOCK_LLM_ENABLED=false``,
  isolated behind :meth:`_call_real_llm` so it is fully mockable and performs
  no network I/O in tests),
* **validates** whatever came back against
  :class:`EnrichmentOutputSchema` — the gate that keeps untrusted LLM output
  out of storage,
* persists the result (success *or* failure metadata) through the injected
  :class:`PipelineRepository`, and
* returns an :class:`EnrichmentResult` carrying the status, the new
  enrichment id and a retry decision derived from the retry policy.

Design notes
------------
* The module never opens a database session.  It receives one per call and
  wraps it with ``repository_factory`` (``PipelineRepository`` by default), so
  unit tests can inject a fake repository and a sentinel session and exercise
  the whole flow with no database.
* When ``MOCK_LLM_ENABLED`` is true the real-LLM branch is never touched, so
  ``OPENAI_API_KEY`` is not required.
* Failures are mapped onto the existing nine-value enrichment status taxonomy;
  exceptions are classified, not swallowed.  A genuinely unimplemented real
  path raises loudly rather than being recorded as a runtime failure.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union
from uuid import UUID

from pydantic import ValidationError

from src.database.repository import PipelineRepository
from src.enrichment.mock_llm import MockLLMProvider
from src.enrichment.prompt_builder import PROMPT_VERSION, build_enrichment_prompt
from src.enrichment.retry_policy import should_retry
from src.logging_config import get_logger
from src.validation.enrichment_schemas import EnrichmentOutputSchema
from src.validation.input_schemas import EnrichmentResult, RawLeadSchema

# Factory that turns a session into a repository; overridable for testing.
RepositoryFactory = Callable[[Any], Any]

# Output the provider may hand back before the validation gate: an already
# validated schema instance, a raw JSON string, or a parsed mapping.
ProviderOutput = Union[EnrichmentOutputSchema, str, dict, None]


class _EnrichmentFailure(Exception):
    """Internal carrier mapping a failure to an enrichment-status value.

    ``status`` is always one of the existing taxonomy values; ``raw_response``
    holds the offending text when there is one (e.g. unparseable JSON) so it
    can be persisted for auditing.
    """

    def __init__(
        self,
        status: str,
        message: str,
        raw_response: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.raw_response = raw_response


class LLMEnrichmentModule:
    """Enrich validated leads and persist the result behind a validation gate.

    Parameters
    ----------
    settings:
        Application settings.  Only ``MOCK_LLM_ENABLED``, ``OPENAI_MODEL`` and
        ``RETRY_MAX_ATTEMPTS`` are read here.  Tests may pass any object
        exposing those attributes.
    provider:
        The mock LLM provider instance.  Defaults to a fresh
        :class:`MockLLMProvider`.
    repository_factory:
        Callable turning a session into a repository.  Defaults to
        :class:`PipelineRepository`, so production code gets real persistence
        while tests inject a fake.
    logger:
        Optional structlog logger; one is created when omitted.
    """

    def __init__(
        self,
        settings,
        provider: Optional[MockLLMProvider] = None,
        repository_factory: RepositoryFactory = PipelineRepository,
        logger=None,
    ) -> None:
        self._settings = settings
        self._provider = provider or MockLLMProvider()
        self._repository_factory = repository_factory
        self._logger = logger or get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def enrich_lead(
        self,
        validated_lead_id: UUID,
        lead: Union[RawLeadSchema, dict],
        idempotency_key: str,
        pipeline_run_id: UUID,
        session,
    ) -> EnrichmentResult:
        """Enrich a single validated lead and persist the outcome.

        Returns an :class:`EnrichmentResult` with ``enrichment_status`` set to
        ``"success"`` on a clean run, or to the mapped failure status
        otherwise.  The matching :class:`Enrichment` row is always written
        through the injected repository, and ``should_retry`` reflects the
        retry policy for the recorded status.
        """
        # Session is injected; the repository is the only DB entry point and is
        # never created from a self-opened session.
        repository = self._repository_factory(session)

        # Built for the real LLM path and for traceability; the mock provider
        # derives its own deterministic output from the lead.
        prompt = build_enrichment_prompt(lead, context=None)

        try:
            raw = self._invoke_provider(lead, prompt)
            output, raw_text = self._validate_output(raw)
        except _EnrichmentFailure as exc:
            self._logger.warning(
                "enrichment_failed",
                enrichment_status=exc.status,
                validated_lead_id=str(validated_lead_id),
                pipeline_run_id=str(pipeline_run_id),
            )
            return self._record_failure(
                repository,
                validated_lead_id=validated_lead_id,
                pipeline_run_id=pipeline_run_id,
                status=exc.status,
                message=str(exc),
                raw_response=exc.raw_response,
            )
        except NotImplementedError:
            # An unimplemented real path is a programmer error, not a runtime
            # failure mode — surface it loudly instead of masking it.
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately the catch-all gate
            self._logger.error(
                "enrichment_unknown_error",
                error=str(exc),
                validated_lead_id=str(validated_lead_id),
                pipeline_run_id=str(pipeline_run_id),
            )
            return self._record_failure(
                repository,
                validated_lead_id=validated_lead_id,
                pipeline_run_id=pipeline_run_id,
                status="unknown_error",
                message=str(exc),
                raw_response=None,
            )

        return self._record_success(
            repository,
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
            output=output,
            raw_response=raw_text,
        )

    # ------------------------------------------------------------------ #
    # Provider selection
    # ------------------------------------------------------------------ #

    def _invoke_provider(
        self,
        lead: Union[RawLeadSchema, dict],
        prompt: str,
    ) -> ProviderOutput:
        """Return raw enrichment output from the active provider.

        Mock mode calls :meth:`MockLLMProvider.enrich_lead`; real mode is
        delegated to :meth:`_call_real_llm`.  ``OPENAI_API_KEY`` is never read
        in mock mode.
        """
        if self._settings.MOCK_LLM_ENABLED:
            return self._provider.enrich_lead(lead, context=None)
        return self._call_real_llm(lead, prompt)

    def _call_real_llm(
        self,
        lead: Union[RawLeadSchema, dict],
        prompt: str,
    ) -> str:
        """Real OpenAI enrichment boundary (isolated, monkeypatch-ready).

        Production wiring for the real provider lands in a later task.  Kept as
        a single seam so it can be monkeypatched in tests without any network
        access; it must return a raw JSON string that flows through the same
        validation gate as the mock path.
        """
        raise NotImplementedError(
            "Real OpenAI enrichment is not wired up yet; set MOCK_LLM_ENABLED "
            "true or monkeypatch _call_real_llm."
        )

    # ------------------------------------------------------------------ #
    # Validation gate
    # ------------------------------------------------------------------ #

    def _validate_output(
        self,
        raw: ProviderOutput,
    ) -> tuple[EnrichmentOutputSchema, Optional[str]]:
        """Run the validation gate, returning ``(schema, raw_text)``.

        Accepts an already-validated schema instance, a raw JSON string or a
        mapping.  Raises :class:`_EnrichmentFailure` with the appropriate
        taxonomy status (``empty_response``, ``invalid_json`` or
        ``validation_failed``) when the output cannot be validated.
        """
        if isinstance(raw, EnrichmentOutputSchema):
            # The mock provider returns a pre-validated instance; re-affirm the
            # gate so every path converges on the same guarantee.
            return self._validate_mapping(raw.model_dump(), raw_text=None)

        if raw is None:
            raise _EnrichmentFailure("empty_response", "provider returned no output")

        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raise _EnrichmentFailure(
                    "empty_response", "provider returned an empty string", raw_response=raw
                )
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError) as exc:
                raise _EnrichmentFailure(
                    "invalid_json", f"response is not valid JSON: {exc}", raw_response=text
                )
            return self._validate_mapping(data, raw_text=text)

        if isinstance(raw, dict):
            return self._validate_mapping(raw, raw_text=None)

        raise _EnrichmentFailure(
            "unknown_error",
            f"unexpected provider output type: {type(raw).__name__}",
        )

    def _validate_mapping(
        self,
        data: Any,
        raw_text: Optional[str],
    ) -> tuple[EnrichmentOutputSchema, Optional[str]]:
        """Validate a parsed payload against :class:`EnrichmentOutputSchema`."""
        try:
            output = EnrichmentOutputSchema.model_validate(data)
        except ValidationError as exc:
            raise _EnrichmentFailure(
                "validation_failed", str(exc), raw_response=raw_text
            )
        return output, raw_text

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _record_success(
        self,
        repository,
        *,
        validated_lead_id: UUID,
        pipeline_run_id: UUID,
        output: EnrichmentOutputSchema,
        raw_response: Optional[str],
    ) -> EnrichmentResult:
        """Insert a successful enrichment row and build the result."""
        enrichment_data = {
            "validated_lead_id": str(validated_lead_id),
            "pipeline_run_id": str(pipeline_run_id),
            "enrichment_status": "success",
            "market_potential": output.market_potential,
            "export_readiness": output.export_readiness,
            "risk_assessment": output.risk_assessment.model_dump(),
            "recommended_markets": list(output.recommended_markets),
            "confidence_score": output.confidence_score,
            "retry_count": 0,
            "raw_llm_response": raw_response,
            "prompt_version": PROMPT_VERSION,
            "model_name": self._model_name,
            "enrichment_created_at": datetime.now(timezone.utc),
        }
        enrichment_id = repository.insert_enrichment(enrichment_data)
        self._logger.info(
            "enrichment_success",
            validated_lead_id=str(validated_lead_id),
            pipeline_run_id=str(pipeline_run_id),
        )
        return EnrichmentResult(
            enrichment_status="success",
            enrichment_id=_coerce_uuid(enrichment_id),
            should_retry=False,
            error_message=None,
            retry_count=0,
        )

    def _record_failure(
        self,
        repository,
        *,
        validated_lead_id: UUID,
        pipeline_run_id: UUID,
        status: str,
        message: str,
        raw_response: Optional[str],
    ) -> EnrichmentResult:
        """Insert a failure enrichment row and build the result.

        ``retry_count`` is ``0`` for a fresh attempt; the retry orchestration
        loop (a later task) increments it.  ``should_retry`` is derived from
        the shared retry policy and the configured maximum.
        """
        enrichment_data = {
            "validated_lead_id": str(validated_lead_id),
            "pipeline_run_id": str(pipeline_run_id),
            "enrichment_status": status,
            "error_type": status,
            "error_message": message,
            "failed_at": datetime.now(timezone.utc),
            "retry_count": 0,
            "raw_llm_response": raw_response,
            "prompt_version": PROMPT_VERSION,
            "model_name": self._model_name,
        }
        enrichment_id = repository.insert_enrichment(enrichment_data)
        retry = should_retry(
            status,
            retry_count=0,
            max_retries=self._max_retries,
        )
        return EnrichmentResult(
            enrichment_status=status,
            enrichment_id=_coerce_uuid(enrichment_id),
            should_retry=retry,
            error_message=message,
            retry_count=0,
        )

    # ------------------------------------------------------------------ #
    # Config-derived helpers
    # ------------------------------------------------------------------ #

    @property
    def _model_name(self) -> str:
        """The model identifier to record for the active provider."""
        if self._settings.MOCK_LLM_ENABLED:
            return getattr(self._provider, "model_name", MockLLMProvider.model_name)
        return self._settings.OPENAI_MODEL

    @property
    def _max_retries(self) -> int:
        """Configured retry ceiling, defaulting to 0 when unset."""
        return int(getattr(self._settings, "RETRY_MAX_ATTEMPTS", 0) or 0)


def _coerce_uuid(value: Any) -> Optional[UUID]:
    """Best-effort convert a repository-returned id to a :class:`UUID`.

    Returns the value unchanged when it is already a ``UUID`` or ``None``, a
    parsed ``UUID`` when it is a UUID-shaped string, and the original value
    otherwise (so non-UUID test ids still surface on the result).
    """
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return value
