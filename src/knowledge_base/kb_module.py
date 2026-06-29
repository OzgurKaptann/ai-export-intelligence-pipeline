"""
Knowledge base module — stub implementation (Task 16).

`KnowledgeBaseModule` is the seam the enrichment stage will eventually call to
fetch domain-specific context (product and export information) for a lead.  For
now it is a deliberate **stub**: :meth:`retrieve_context` always returns
``None`` so the enrichment path can already treat "no context available" as a
first-class case, while real retrieval (embeddings, a vector store, document
loading, RAG) is left for a future task.

Design notes
------------
* Importing this module has **no side effects**.  ``get_settings()`` is *not*
  called at import time; settings are read lazily, only when
  :attr:`is_enabled` is accessed.
* The settings source is injectable.  ``settings_provider`` defaults to
  :func:`src.config.get_settings`, but tests pass a fake callable so they can
  read ``KB_ENABLED`` without a real ``DATABASE_URL`` or any environment setup.
* :meth:`retrieve_context` is pure and offline: it makes no external API call,
  opens no database session, reads no files, performs no network I/O and never
  requires ``OPENAI_API_KEY``.  It does not mutate its inputs.
"""

from __future__ import annotations

from typing import Callable, Optional

from src.config import Settings, get_settings

# Callable returning the application settings; overridable for testing.
SettingsProvider = Callable[[], Settings]


class KnowledgeBaseModule:
    """Optional knowledge-base context provider (stub).

    Parameters
    ----------
    settings_provider:
        Zero-argument callable returning a settings object exposing
        ``KB_ENABLED``.  Defaults to :func:`src.config.get_settings`.  It is
        only invoked when :attr:`is_enabled` is read, never at construction or
        import time, so tests can inject a fake and avoid environment-heavy
        setup (no real ``DATABASE_URL`` required).
    """

    def __init__(self, settings_provider: Optional[SettingsProvider] = None) -> None:
        self._settings_provider = settings_provider or get_settings

    @property
    def is_enabled(self) -> bool:
        """Whether knowledge-base retrieval is enabled (reads ``KB_ENABLED``).

        The settings provider is consulted lazily on each access, so the flag
        reflects the configuration at call time rather than at import time.
        """
        settings = self._settings_provider()
        return bool(settings.KB_ENABLED)

    def retrieve_context(
        self,
        product_category: str,
        target_market: str,
    ) -> Optional[str]:
        """Return context for a lead — always ``None`` in this stub.

        Real retrieval (vector search / RAG over a knowledge base) is not
        implemented yet.  The method is intentionally inert: it returns ``None``
        for any normal string input, performs no I/O, and does not mutate its
        arguments — so the enrichment path can proceed with lead data only,
        whether or not the knowledge base is enabled.
        """
        return None
