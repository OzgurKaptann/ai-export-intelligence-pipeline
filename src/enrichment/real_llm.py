"""
Real OpenAI enrichment provider (optional, late-stage — Task 26).

`RealLLMProvider` is the production counterpart to
:class:`~src.enrichment.mock_llm.MockLLMProvider`.  It calls the OpenAI Chat
Completions API with structured JSON output mode and returns the raw response
text.  It deliberately does **not** parse or validate that text — that stays
the responsibility of :class:`~src.enrichment.llm_enrichment.LLMEnrichmentModule`,
which runs every provider's output through the same
:class:`~src.validation.enrichment_schemas.EnrichmentOutputSchema` gate.

Design constraints (all enforced here)
--------------------------------------
* **No work at import time.** The OpenAI client is never built, settings are
  never read and the API is never called when this module is imported.  Every
  side effect is deferred until :meth:`generate` (or an explicit client
  request) actually runs.
* **Dependency-injection friendly.** Both ``settings_provider`` and ``client``
  are optional constructor arguments.  Tests inject a fake client and never
  touch the network or require an ``OPENAI_API_KEY``.
* **Lazy, key-checked client creation.** When no client is injected the real
  ``openai.OpenAI`` client is created lazily, and a missing/empty
  ``OPENAI_API_KEY`` fails loudly with :class:`RealLLMConfigError` rather than
  producing a confusing downstream error.
* **API keys are never logged or printed.**

The three transient OpenAI failure modes are intentionally *not* caught here;
they propagate so :class:`LLMEnrichmentModule` can map them onto the existing
enrichment-status taxonomy (``timeout`` / ``network_error`` / ``rate_limited``).
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Union

from src.config import Settings, get_settings
from src.validation.input_schemas import RawLeadSchema

# System instruction paired with the per-lead prompt.  The prompt already spells
# out the required JSON shape; this reinforces JSON-only output to complement
# ``response_format={"type": "json_object"}``.
_SYSTEM_INSTRUCTION = (
    "You are an export-market analyst. Respond with a single valid JSON object "
    "matching the requested schema and nothing else."
)

# Either a ready settings object or a zero-arg callable returning one.
SettingsProvider = Union[Settings, Callable[[], Settings], None]


class RealLLMConfigError(RuntimeError):
    """Raised when real LLM mode is requested but cannot be configured.

    The most common cause is a missing or empty ``OPENAI_API_KEY`` while
    ``MOCK_LLM_ENABLED=false`` and no client has been injected.
    """


class RealLLMProvider:
    """Call the OpenAI Chat Completions API and return raw JSON text.

    Parameters
    ----------
    settings_provider:
        Either an application :class:`~src.config.Settings` instance or a
        zero-argument callable returning one.  Resolved lazily on first use, so
        ``get_settings()`` is never called at import time.  Defaults to
        :func:`~src.config.get_settings`.
    client:
        Optional pre-built OpenAI client (or any object exposing
        ``chat.completions.create``).  When provided it is used directly and no
        ``OPENAI_API_KEY`` is required — this is the seam unit tests use.  When
        omitted, a real ``openai.OpenAI`` client is created lazily from
        ``settings.OPENAI_API_KEY``.
    """

    def __init__(
        self,
        settings_provider: SettingsProvider = None,
        client: Optional[Any] = None,
    ) -> None:
        self._settings_provider: SettingsProvider = settings_provider
        self._client = client
        self._settings: Optional[Settings] = None
        # The model recorded for an enrichment.  Until a response is seen this
        # falls back to the configured model (see :attr:`model_name`).
        self._response_model: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Lazy resolution helpers
    # ------------------------------------------------------------------ #

    @property
    def _resolved_settings(self) -> Settings:
        """Resolve settings once, lazily, from the injected provider."""
        if self._settings is None:
            provider = self._settings_provider
            if provider is None:
                self._settings = get_settings()
            elif callable(provider):
                self._settings = provider()
            else:
                self._settings = provider
        return self._settings

    @property
    def _model(self) -> str:
        """The configured model id read from settings."""
        return getattr(self._resolved_settings, "OPENAI_MODEL", "") or ""

    def _get_client(self) -> Any:
        """Return the OpenAI client, creating it lazily when not injected.

        Raises :class:`RealLLMConfigError` when a client must be built but no
        ``OPENAI_API_KEY`` is configured.
        """
        if self._client is None:
            api_key = getattr(self._resolved_settings, "OPENAI_API_KEY", "") or ""
            if not api_key:
                raise RealLLMConfigError(
                    "OPENAI_API_KEY is required for real LLM mode "
                    "(MOCK_LLM_ENABLED=false). Set OPENAI_API_KEY or enable "
                    "mock mode."
                )
            # Imported lazily so merely importing this module never pulls in or
            # configures the OpenAI client.
            import openai

            self._client = openai.OpenAI(api_key=api_key)
        return self._client

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def model_name(self) -> str:
        """Model id to record: the actual response model, else the configured one.

        After :meth:`generate` runs this reflects ``response.model`` when the
        API reported one (e.g. a dated snapshot like ``gpt-4o-mini-2024-07-18``);
        before any call, or when the response omits a model, it falls back to
        ``settings.OPENAI_MODEL``.
        """
        return self._response_model or self._model

    def generate(self, lead: RawLeadSchema, prompt: str) -> str:
        """Call the Chat Completions API and return the raw JSON string.

        Sends the configured model, ``response_format={"type": "json_object"}``
        and the enrichment ``prompt`` as the user message.  Captures the actual
        response model for :attr:`model_name`.

        Returns the assistant message content verbatim.  When the response has
        no content (missing or empty) an empty string is returned so
        :class:`LLMEnrichmentModule` maps it to ``empty_response`` through the
        normal gate.  Transient OpenAI errors are not caught here; they
        propagate to the caller for taxonomy mapping.
        """
        client = self._get_client()
        model = self._model

        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
        )

        # Record the model the API actually served; fall back to configured.
        self._response_model = getattr(response, "model", None) or model

        return _extract_content(response)


def _extract_content(response: Any) -> str:
    """Pull ``choices[0].message.content`` from a Chat Completions response.

    Returns an empty string when any expected field is missing or the content
    is empty — the common shape is supported without over-engineering for every
    possible response variant.
    """
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", None)
    return content or ""
