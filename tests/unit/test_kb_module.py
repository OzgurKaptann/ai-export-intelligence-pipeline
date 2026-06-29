"""
Unit tests for ``KnowledgeBaseModule`` (Task 16 — stub).

The knowledge base is a stub for now: :meth:`retrieve_context` always returns
``None`` and :attr:`is_enabled` reads ``KB_ENABLED`` from config.  These tests
verify that contract in complete isolation:

* No live database / SQLite — settings are supplied through an injected fake
  provider, so no real ``DATABASE_URL`` is required.
* No OpenAI, no network and no ``OPENAI_API_KEY``.
* No file I/O.
* Inputs are never mutated.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.knowledge_base.kb_module import KnowledgeBaseModule


def _fake_settings_provider(kb_enabled: bool):
    """Return a zero-arg provider yielding a settings-like object.

    The object only exposes ``KB_ENABLED`` — deliberately *not* a real
    ``Settings`` instance, so the test needs no environment or database URL.
    """
    return lambda: SimpleNamespace(KB_ENABLED=kb_enabled)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_can_be_instantiated_with_injected_provider():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(False))
    assert isinstance(module, KnowledgeBaseModule)


def test_can_be_instantiated_without_arguments():
    # Construction must not call get_settings(), so this needs no DATABASE_URL.
    module = KnowledgeBaseModule()
    assert isinstance(module, KnowledgeBaseModule)


# ---------------------------------------------------------------------------
# retrieve_context — always None
# ---------------------------------------------------------------------------

def test_retrieve_context_returns_none_acceptance_case():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(False))
    assert module.retrieve_context("electronics", "EU") is None


def test_retrieve_context_returns_none_when_kb_enabled():
    # Still a stub: enabling the KB does not change the result.
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(True))
    assert module.is_enabled is True
    assert module.retrieve_context("electronics", "EU") is None


def test_retrieve_context_returns_none_when_kb_disabled():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(False))
    assert module.is_enabled is False
    assert module.retrieve_context("electronics", "EU") is None


def test_retrieve_context_returns_none_for_various_inputs():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(True))
    assert module.retrieve_context("", "") is None
    assert module.retrieve_context("machinery", "APAC") is None
    assert module.retrieve_context("textiles", "North America") is None


def test_retrieve_context_does_not_mutate_inputs():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(True))
    category = "electronics"
    market = "EU"
    module.retrieve_context(category, market)
    assert category == "electronics"
    assert market == "EU"


# ---------------------------------------------------------------------------
# is_enabled — reads config lazily
# ---------------------------------------------------------------------------

def test_is_enabled_reads_true_from_settings():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(True))
    assert module.is_enabled is True


def test_is_enabled_reads_false_from_settings():
    module = KnowledgeBaseModule(settings_provider=_fake_settings_provider(False))
    assert module.is_enabled is False


def test_is_enabled_is_lazy_and_needs_no_database_url():
    # The provider must only be consulted when is_enabled is accessed, never at
    # construction — and the injected fake carries no DATABASE_URL.
    calls = {"count": 0}

    def provider():
        calls["count"] += 1
        return SimpleNamespace(KB_ENABLED=True)

    module = KnowledgeBaseModule(settings_provider=provider)
    assert calls["count"] == 0  # not called during construction

    assert module.is_enabled is True
    assert calls["count"] == 1  # consulted on access

    # Re-reads on each access (no stale caching).
    assert module.is_enabled is True
    assert calls["count"] == 2


def test_retrieve_context_does_not_consult_settings_provider():
    # The stub returns None without ever reading config or touching any service.
    def exploding_provider():
        raise AssertionError("retrieve_context must not read settings")

    module = KnowledgeBaseModule(settings_provider=exploding_provider)
    assert module.retrieve_context("electronics", "EU") is None
