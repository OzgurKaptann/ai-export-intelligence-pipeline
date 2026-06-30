"""
Unit tests for src/enrichment/real_llm.py and the real-provider integration in
src/enrichment/llm_enrichment.py.

These tests run **entirely offline**:

* No real OpenAI call is ever made — a fake client object records the
  ``chat.completions.create`` call and returns a canned response (or raises a
  canned error).
* ``OPENAI_API_KEY`` is never required, because a client is always injected.
* Importing the module performs no work (no client, no settings read, no API
  call); this is asserted explicitly.

The transient-error mapping tests construct genuine ``openai`` exception
instances and assert they map to the existing enrichment-status taxonomy
(``timeout`` / ``network_error`` / ``rate_limited``) when surfaced through
:class:`LLMEnrichmentModule`.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import openai
import pytest

from src.enrichment.llm_enrichment import LLMEnrichmentModule
from src.enrichment.real_llm import RealLLMConfigError, RealLLMProvider
from src.validation.input_schemas import RawLeadSchema


# ---------------------------------------------------------------------------
# Fake OpenAI client doubles (no network, no key)
# ---------------------------------------------------------------------------

class FakeCompletions:
    """Records the create() call and returns a preset response (or raises)."""

    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


class FakeClient:
    """Minimal stand-in for openai.OpenAI exposing chat.completions.create."""

    def __init__(self, response=None, exc: Exception | None = None) -> None:
        self.completions = FakeCompletions(response=response, exc=exc)
        self.chat = SimpleNamespace(completions=self.completions)


def make_response(content="{}", model="gpt-4o-mini-2024-07-18"):
    """Build a Chat-Completions-shaped response object."""
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], model=model)


def make_settings(*, openai_model="gpt-4o-mini", openai_api_key=""):
    return SimpleNamespace(
        MOCK_LLM_ENABLED=False,
        OPENAI_API_KEY=openai_api_key,
        OPENAI_MODEL=openai_model,
        RETRY_MAX_ATTEMPTS=3,
        RETRY_DELAY_SECONDS=2.0,
        LLM_TIMEOUT_SECONDS=30,
    )


LEAD = RawLeadSchema(
    company_name="Acme Exports Ltd",
    contact_email="contact@acme.example.com",
    product_category="Electronics",
    target_market="Germany",
)

VALID_JSON = (
    '{"market_potential": 0.6, "export_readiness": 0.6,'
    ' "risk_assessment": {"overall_risk": 0.3},'
    ' "recommended_markets": ["Germany"], "confidence_score": 0.6}'
)


def _timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    )


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    )


def _rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code=429, request=request)
    return openai.RateLimitError("rate limited", response=response, body=None)


# ---------------------------------------------------------------------------
# RealLLMProvider — construction and the generate() contract
# ---------------------------------------------------------------------------

def test_instantiated_with_injected_fake_client():
    client = FakeClient(response=make_response())
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)
    assert provider is not None


def test_generate_calls_fake_client_create():
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    provider.generate(LEAD, "PROMPT TEXT")

    assert len(client.completions.calls) == 1


def test_generate_sends_configured_model():
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(
        settings_provider=make_settings(openai_model="gpt-4o-mini"), client=client
    )

    provider.generate(LEAD, "PROMPT TEXT")

    assert client.completions.calls[0]["model"] == "gpt-4o-mini"


def test_generate_sends_json_object_response_format():
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    provider.generate(LEAD, "PROMPT TEXT")

    assert client.completions.calls[0]["response_format"] == {"type": "json_object"}


def test_generate_includes_prompt_in_messages():
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    provider.generate(LEAD, "UNIQUE-PROMPT-MARKER")

    messages = client.completions.calls[0]["messages"]
    assert any(m["role"] == "user" for m in messages)
    assert any("UNIQUE-PROMPT-MARKER" in m["content"] for m in messages)


def test_generate_returns_message_content():
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    result = provider.generate(LEAD, "PROMPT TEXT")

    assert result == VALID_JSON


def test_model_name_from_response_when_present():
    client = FakeClient(
        response=make_response(content=VALID_JSON, model="gpt-4o-mini-2024-07-18")
    )
    provider = RealLLMProvider(
        settings_provider=make_settings(openai_model="gpt-4o-mini"), client=client
    )

    provider.generate(LEAD, "PROMPT TEXT")

    assert provider.model_name == "gpt-4o-mini-2024-07-18"


def test_model_name_falls_back_to_configured_when_response_missing_model():
    client = FakeClient(response=make_response(content=VALID_JSON, model=None))
    provider = RealLLMProvider(
        settings_provider=make_settings(openai_model="gpt-4o-mini"), client=client
    )

    provider.generate(LEAD, "PROMPT TEXT")

    assert provider.model_name == "gpt-4o-mini"


def test_model_name_before_any_call_is_configured_model():
    client = FakeClient(response=make_response())
    provider = RealLLMProvider(
        settings_provider=make_settings(openai_model="gpt-4o-mini"), client=client
    )

    assert provider.model_name == "gpt-4o-mini"


def test_missing_content_returns_empty_string():
    client = FakeClient(response=make_response(content=None))
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    assert provider.generate(LEAD, "PROMPT TEXT") == ""


def test_empty_content_returns_empty_string():
    client = FakeClient(response=make_response(content=""))
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    assert provider.generate(LEAD, "PROMPT TEXT") == ""


def test_no_choices_returns_empty_string():
    response = SimpleNamespace(choices=[], model="gpt-4o-mini")
    client = FakeClient(response=response)
    provider = RealLLMProvider(settings_provider=make_settings(), client=client)

    assert provider.generate(LEAD, "PROMPT TEXT") == ""


def test_no_api_key_required_when_client_injected():
    # Empty OPENAI_API_KEY but an injected client -> no key needed, no error.
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(
        settings_provider=make_settings(openai_api_key=""), client=client
    )

    assert provider.generate(LEAD, "PROMPT TEXT") == VALID_JSON


def test_missing_api_key_without_client_raises_config_error():
    # No injected client and no key -> clear configuration error, no network.
    provider = RealLLMProvider(settings_provider=make_settings(openai_api_key=""))

    with pytest.raises(RealLLMConfigError):
        provider.generate(LEAD, "PROMPT TEXT")


def test_settings_provider_accepts_callable():
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider = RealLLMProvider(
        settings_provider=lambda: make_settings(openai_model="gpt-4o"), client=client
    )

    provider.generate(LEAD, "PROMPT TEXT")

    assert client.completions.calls[0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# No work performed at import time
# ---------------------------------------------------------------------------

def test_no_api_call_or_client_at_import_or_construction(monkeypatch):
    # Constructing the provider must not touch openai.OpenAI at all.
    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("openai.OpenAI must not be constructed eagerly")

    monkeypatch.setattr(openai, "OpenAI", explode)

    # Construction with no client and no settings access -> no client built.
    provider = RealLLMProvider()
    assert provider is not None
    # Injected-client path also never builds a real client.
    client = FakeClient(response=make_response(content=VALID_JSON))
    provider2 = RealLLMProvider(settings_provider=make_settings(), client=client)
    provider2.generate(LEAD, "PROMPT TEXT")


# ---------------------------------------------------------------------------
# Transient OpenAI errors map through LLMEnrichmentModule
# ---------------------------------------------------------------------------

class FakeRepository:
    """In-memory spy standing in for PipelineRepository."""

    def __init__(self) -> None:
        self.enrichments: list[dict] = []

    def insert_enrichment(self, enrichment_data: dict) -> str:
        enrichment_id = str(uuid4())
        record = dict(enrichment_data)
        record["enrichment_id"] = enrichment_id
        self.enrichments.append(record)
        return enrichment_id


VALIDATED_LEAD_ID = UUID("22222222-2222-2222-2222-222222222222")
PIPELINE_RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
IDEMPOTENCY_KEY = "a" * 64


def build_module(*, client=None, exc=None, response=None, settings=None):
    """Wire an LLMEnrichmentModule in real mode with an injected fake provider."""
    settings = settings or make_settings()
    repo = FakeRepository()
    if client is None:
        client = FakeClient(response=response, exc=exc)
    real_provider = RealLLMProvider(settings_provider=settings, client=client)
    module = LLMEnrichmentModule(
        settings=settings,
        real_provider=real_provider,
        repository_factory=lambda session: repo,
    )
    return module, repo


def run(module):
    return module.enrich_lead(
        validated_lead_id=VALIDATED_LEAD_ID,
        lead=LEAD,
        idempotency_key=IDEMPOTENCY_KEY,
        pipeline_run_id=PIPELINE_RUN_ID,
        session=object(),
    )


def test_timeout_maps_to_timeout_status():
    module, repo = build_module(exc=_timeout_error())

    result = run(module)

    assert result.enrichment_status == "timeout"
    assert repo.enrichments[0]["enrichment_status"] == "timeout"


def test_api_connection_error_maps_to_network_error():
    module, repo = build_module(exc=_connection_error())

    result = run(module)

    assert result.enrichment_status == "network_error"
    assert repo.enrichments[0]["enrichment_status"] == "network_error"


def test_rate_limit_error_maps_to_rate_limited():
    module, repo = build_module(exc=_rate_limit_error())

    result = run(module)

    assert result.enrichment_status == "rate_limited"
    assert repo.enrichments[0]["enrichment_status"] == "rate_limited"


def test_real_mode_success_persists_response_model_name():
    module, repo = build_module(
        response=make_response(content=VALID_JSON, model="gpt-4o-mini-2024-07-18")
    )

    result = run(module)

    assert result.enrichment_status == "success"
    assert repo.enrichments[0]["model_name"] == "gpt-4o-mini-2024-07-18"


def test_real_mode_no_openai_key_required_with_injected_client():
    # Real mode, empty key, but the injected client never needs one.
    settings = make_settings(openai_api_key="")
    module, repo = build_module(
        settings=settings, response=make_response(content=VALID_JSON)
    )

    result = run(module)

    assert result.enrichment_status == "success"
