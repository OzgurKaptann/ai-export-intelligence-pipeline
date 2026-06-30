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
  ``OPENAI_API_KEY`` is not required and no OpenAI client is ever built.  When
  it is false the module delegates to :class:`RealLLMProvider`
  (``src.enrichment.real_llm``), which performs the OpenAI call.
* Failures are mapped onto the existing nine-value enrichment status taxonomy;
  exceptions are classified, not swallowed.  The three transient OpenAI errors
  map to ``timeout`` / ``network_error`` / ``rate_limited``; a misconfiguration
  (real mode with no ``OPENAI_API_KEY``) raises loudly rather than being
  recorded as a per-lead runtime failure.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union
from uuid import UUID

from pydantic import ValidationError

from src.database.repository import PipelineRepository
from src.enrichment.mock_llm import MockLLMProvider
from src.enrichment.prompt_builder import PROMPT_VERSION, build_enrichment_prompt
from src.enrichment.real_llm import RealLLMConfigError, RealLLMProvider
from src.enrichment.retry_policy import is_retryable, should_retry
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
    real_provider:
        The real OpenAI provider instance used when ``MOCK_LLM_ENABLED`` is
        false.  Defaults to ``None`` and is created lazily (only when the real
        path is actually exercised) from a :class:`RealLLMProvider` bound to
        these settings, so mock mode never builds a client or reads
        ``OPENAI_API_KEY``.  Tests inject a provider with a fake client.
    repository_factory:
        Callable turning a session into a repository.  Defaults to
        :class:`PipelineRepository`, so production code gets real persistence
        while tests inject a fake.
    logger:
        Optional structlog logger; one is created when omitted.
    max_retries:
        Optional override for the retry ceiling used by
        :meth:`enrich_with_retry`.  When ``None`` the value is read lazily from
        ``settings.RETRY_MAX_ATTEMPTS`` at call time, so tests can pin it
        without touching the environment.
    retry_delay_seconds:
        Optional override for the exponential-backoff base delay.  When
        ``None`` the value is read lazily from ``settings.RETRY_DELAY_SECONDS``.
    sleep_func:
        Callable used to wait between retries; defaults to :func:`time.sleep`.
        Tests inject a no-op so no real time passes.
    jitter_func:
        Callable ``(low, high) -> float`` used for backoff jitter; defaults to
        :func:`random.uniform`.  Tests inject a deterministic stub.
    """

    def __init__(
        self,
        settings,
        provider: Optional[MockLLMProvider] = None,
        real_provider: Optional[RealLLMProvider] = None,
        repository_factory: RepositoryFactory = PipelineRepository,
        logger=None,
        max_retries: Optional[int] = None,
        retry_delay_seconds: Optional[float] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
        jitter_func: Optional[Callable[[float, float], float]] = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or MockLLMProvider()
        self._real_provider = real_provider
        self._repository_factory = repository_factory
        self._logger = logger or get_logger(__name__)
        # Retry knobs: stored as overrides so the config-derived defaults stay
        # lazy (read per call), never at import time.
        self._max_retries_override = max_retries
        self._retry_delay_seconds_override = retry_delay_seconds
        self._sleep_func = sleep_func or time.sleep
        self._jitter_func = jitter_func or random.uniform

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
        retry_count: int = 0,
    ) -> EnrichmentResult:
        """Enrich a single validated lead and persist the outcome.

        Returns an :class:`EnrichmentResult` with ``enrichment_status`` set to
        ``"success"`` on a clean run, or to the mapped failure status
        otherwise.  The matching :class:`Enrichment` row is always written
        through the injected repository, and ``should_retry`` reflects the
        retry policy for the recorded status.

        ``retry_count`` records how many retries preceded this attempt; it is
        ``0`` for a standalone call and is supplied by :meth:`enrich_with_retry`
        when the same attempt is replayed.  It is persisted on the enrichment
        row and surfaced on the result for auditing.
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
                retry_count=retry_count,
            )
        except (NotImplementedError, RealLLMConfigError):
            # Misconfiguration (e.g. real mode with no OPENAI_API_KEY) is a
            # deployment/programmer error, not a per-lead runtime failure mode —
            # surface it loudly instead of masking it as N "unknown_error" rows.
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
                retry_count=retry_count,
            )

        return self._record_success(
            repository,
            validated_lead_id=validated_lead_id,
            pipeline_run_id=pipeline_run_id,
            output=output,
            raw_response=raw_text,
            retry_count=retry_count,
        )

    def enrich_with_retry(
        self,
        validated_lead_id: UUID,
        lead: Union[RawLeadSchema, dict],
        idempotency_key: str,
        pipeline_run_id: UUID,
        session,
    ) -> EnrichmentResult:
        """Enrich a lead, retrying only transient failures with backoff.

        Wraps :meth:`enrich_lead` in a retry loop governed entirely by the
        shared retry policy (:func:`is_retryable` / :func:`should_retry`):

        * The initial attempt always runs.
        * Each retryable failure (``timeout``, ``network_error``,
          ``rate_limited``) increments ``retry_count``; every other status —
          success, non-retryable failures and anything outside the taxonomy —
          stops the loop immediately.
        * Before each retry the loop waits ``retry_delay_seconds *
          (2 ** retry_count) + jitter`` seconds via the injected ``sleep_func``
          and ``jitter_func``; no sleep happens after the final attempt.
        * The loop stops as soon as ``should_retry`` says so, so ``retry_count``
          never drives a call past ``max_retries`` (and a non-positive
          ``max_retries`` means no retries at all).

        The reused session is passed straight through to :meth:`enrich_lead`;
        no new session is ever created, and persistence stays the
        responsibility of ``enrich_lead`` (one row per attempt, no
        double-writes here).  The returned :class:`EnrichmentResult` carries the
        final status and the total number of retryable failures observed.
        """
        max_retries = self._max_retries

        retry_count = 0
        result = self.enrich_lead(
            validated_lead_id=validated_lead_id,
            lead=lead,
            idempotency_key=idempotency_key,
            pipeline_run_id=pipeline_run_id,
            session=session,
            retry_count=retry_count,
        )

        while is_retryable(result.enrichment_status):
            retry_count += 1
            if not should_retry(
                result.enrichment_status,
                retry_count=retry_count,
                max_retries=max_retries,
            ):
                # Ceiling reached (or retries disabled) — stop without sleeping.
                break

            delay = self._retry_delay_seconds * (2 ** retry_count) + self._jitter_func(0.0, 1.0)
            self._logger.info(
                "enrichment_retry",
                enrichment_status=result.enrichment_status,
                retry_count=retry_count,
                max_retries=max_retries,
                validated_lead_id=str(validated_lead_id),
                pipeline_run_id=str(pipeline_run_id),
            )
            self._sleep_func(delay)

            result = self.enrich_lead(
                validated_lead_id=validated_lead_id,
                lead=lead,
                idempotency_key=idempotency_key,
                pipeline_run_id=pipeline_run_id,
                session=session,
                retry_count=retry_count,
            )

        # Surface the total retryable-failure count; the orchestration loop has
        # exhausted its own retries, so no further retry is expected upstream.
        result.retry_count = retry_count
        result.should_retry = False
        return result

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

    def _get_real_provider(self) -> RealLLMProvider:
        """Return the real provider, creating it lazily bound to these settings.

        Created only when the real path is first touched, so mock mode never
        builds an OpenAI client or reads ``OPENAI_API_KEY``.  Tests may inject a
        provider with a fake client via the constructor.
        """
        if self._real_provider is None:
            self._real_provider = RealLLMProvider(
                settings_provider=lambda: self._settings
            )
        return self._real_provider

    def _call_real_llm(
        self,
        lead: Union[RawLeadSchema, dict],
        prompt: str,
    ) -> str:
        """Real OpenAI enrichment boundary (isolated, monkeypatch-ready).

        Delegates to :meth:`RealLLMProvider.generate`, returning the raw JSON
        string so it flows through the same validation gate as the mock path.
        The three transient OpenAI failures are mapped onto the existing
        enrichment-status taxonomy; every other path (success, config errors,
        bad JSON) is left to the shared gate / loud re-raise in
        :meth:`enrich_lead`.

        Note: in the installed ``openai`` v1 SDK the timeout exception is
        ``openai.APITimeoutError`` (``openai.Timeout`` is the httpx timeout
        *config* object, not an exception).  ``APITimeoutError`` subclasses
        ``APIConnectionError``, so it must be caught first.
        """
        import openai

        provider = self._get_real_provider()
        try:
            return provider.generate(lead, prompt)
        except openai.APITimeoutError as exc:
            raise _EnrichmentFailure("timeout", str(exc))
        except openai.APIConnectionError as exc:
            raise _EnrichmentFailure("network_error", str(exc))
        except openai.RateLimitError as exc:
            raise _EnrichmentFailure("rate_limited", str(exc))

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
        retry_count: int = 0,
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
            "retry_count": retry_count,
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
            retry_count=retry_count,
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
        retry_count: int = 0,
    ) -> EnrichmentResult:
        """Insert a failure enrichment row and build the result.

        ``retry_count`` is ``0`` for a fresh attempt and is supplied by
        :meth:`enrich_with_retry` when an attempt is replayed.  ``should_retry``
        is derived from the shared retry policy and the configured maximum.
        """
        enrichment_data = {
            "validated_lead_id": str(validated_lead_id),
            "pipeline_run_id": str(pipeline_run_id),
            "enrichment_status": status,
            "error_type": status,
            "error_message": message,
            "failed_at": datetime.now(timezone.utc),
            "retry_count": retry_count,
            "raw_llm_response": raw_response,
            "prompt_version": PROMPT_VERSION,
            "model_name": self._model_name,
        }
        enrichment_id = repository.insert_enrichment(enrichment_data)
        retry = should_retry(
            status,
            retry_count=retry_count,
            max_retries=self._max_retries,
        )
        return EnrichmentResult(
            enrichment_status=status,
            enrichment_id=_coerce_uuid(enrichment_id),
            should_retry=retry,
            error_message=message,
            retry_count=retry_count,
        )

    # ------------------------------------------------------------------ #
    # Config-derived helpers
    # ------------------------------------------------------------------ #

    @property
    def _model_name(self) -> str:
        """The model identifier to record for the active provider.

        In real mode this is the real provider's ``model_name``, which reflects
        the actual model from the API response after a call (e.g. a dated
        snapshot) and falls back to the configured ``OPENAI_MODEL`` otherwise.
        """
        if self._settings.MOCK_LLM_ENABLED:
            return getattr(self._provider, "model_name", MockLLMProvider.model_name)
        return self._get_real_provider().model_name

    @property
    def _max_retries(self) -> int:
        """Retry ceiling: constructor override, else config, else 0."""
        if self._max_retries_override is not None:
            return int(self._max_retries_override)
        return int(getattr(self._settings, "RETRY_MAX_ATTEMPTS", 0) or 0)

    @property
    def _retry_delay_seconds(self) -> float:
        """Backoff base delay: constructor override, else config, else 0.0."""
        if self._retry_delay_seconds_override is not None:
            return float(self._retry_delay_seconds_override)
        return float(getattr(self._settings, "RETRY_DELAY_SECONDS", 0.0) or 0.0)


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
