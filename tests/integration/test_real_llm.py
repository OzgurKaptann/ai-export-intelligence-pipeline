"""
Live integration test for the real OpenAI LLM provider (Task 26).

This test performs a **real** OpenAI API call and is therefore *skipped by
default*.  It runs only when BOTH opt-in signals are present:

* ``OPENAI_API_KEY`` is set (a usable key), and
* ``RUN_LIVE_LLM_TESTS=true`` (explicit opt-in so the call never happens by
  accident in CI or local default runs).

When either is missing the test is reported as **skipped**, never failed, so
default test runs (unit tests, Docker mock mode, smoke tests) need no API key
and make no external call.

Run it explicitly with, e.g.::

    MOCK_LLM_ENABLED=false OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-4o-mini \
        RUN_LIVE_LLM_TESTS=true pytest -m live_llm tests/integration/test_real_llm.py -v

It is intentionally minimal and low-cost: a single short prompt, asserting only
that the call returns content and a non-empty model id consistent with the
configured model.
"""

from __future__ import annotations

import os

import pytest

from src.enrichment.real_llm import RealLLMProvider
from src.validation.input_schemas import RawLeadSchema

# Skip-by-default guard: both an API key and the explicit opt-in are required.
_HAS_KEY = bool(os.getenv("OPENAI_API_KEY"))
_OPTED_IN = os.getenv("RUN_LIVE_LLM_TESTS", "").lower() == "true"

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        not (_HAS_KEY and _OPTED_IN),
        reason=(
            "live LLM test skipped: set OPENAI_API_KEY and "
            "RUN_LIVE_LLM_TESTS=true to opt in"
        ),
    ),
]


LEAD = RawLeadSchema(
    company_name="Acme Exports Ltd",
    contact_email="contact@acme.example.com",
    product_category="Electronics",
    target_market="Germany",
)


def test_real_llm_generate_returns_content_and_model():
    """Call the real API once and assert basic shape of the response."""
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Build settings inline so the test does not depend on a .env / DATABASE_URL.
    from types import SimpleNamespace

    settings = SimpleNamespace(
        MOCK_LLM_ENABLED=False,
        OPENAI_API_KEY=os.environ["OPENAI_API_KEY"],
        OPENAI_MODEL=model,
    )

    provider = RealLLMProvider(settings_provider=settings)

    prompt = (
        "Return a JSON object with keys market_potential, export_readiness, "
        "recommended_markets, risk_assessment (with overall_risk) and "
        "confidence_score, all numbers between 0 and 1 where applicable, for an "
        "electronics exporter targeting Germany."
    )

    content = provider.generate(LEAD, prompt)

    # The API returned some content (structured JSON output mode).
    assert isinstance(content, str)
    assert content.strip() != ""

    # model_name is captured from the response and is consistent with config.
    assert provider.model_name
    family = model.split("-")[0]  # e.g. "gpt"
    assert family in provider.model_name
