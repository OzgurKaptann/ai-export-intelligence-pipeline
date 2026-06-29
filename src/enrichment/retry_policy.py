"""
Retry policy classifier for the enrichment failure taxonomy.

The enrichment step records one of nine ``enrichment_status`` values for every
attempt (see the failure taxonomy in the design / requirements).  This module
is the single source of truth for *which* of those statuses may be retried and
*whether* a given attempt should be retried again, given how many retries have
already happened.

Both functions are **pure and deterministic**: they take only their arguments,
return a ``bool``, never mutate their inputs, and perform no I/O — no
environment reads, no database access, no network calls, no file access.  This
keeps the retry decision trivially testable and lets the enrichment module (and
its retry loop, added in a later task) depend on a stable, side-effect-free
contract.
"""

from __future__ import annotations

from typing import FrozenSet

# The three transient failure modes worth retrying.  Everything else in the
# taxonomy — including ``success`` and unknown/never-seen statuses — is treated
# as terminal, because retrying it cannot change the outcome.
RETRYABLE_STATUSES: FrozenSet[str] = frozenset(
    {
        "timeout",
        "network_error",
        "rate_limited",
    }
)


def is_retryable(enrichment_status: str) -> bool:
    """Return ``True`` if ``enrichment_status`` is a transient, retryable error.

    Only ``timeout``, ``network_error`` and ``rate_limited`` are retryable.
    Every other enrichment status — ``success``, ``validation_failed``,
    ``empty_response``, ``invalid_json``, ``context_retrieval_failed``,
    ``unknown_error`` and any value outside the known taxonomy — is treated as
    non-retryable.

    The check is exact and case-sensitive: statuses are stored as a fixed
    lowercase vocabulary, so an unexpected value is conservatively classified
    as non-retryable rather than guessed at.
    """
    return enrichment_status in RETRYABLE_STATUSES


def should_retry(
    enrichment_status: str,
    retry_count: int,
    max_retries: int,
) -> bool:
    """Decide whether another enrichment attempt should be made.

    Parameters
    ----------
    enrichment_status:
        The status recorded for the most recent attempt.
    retry_count:
        How many retries have already been performed for this lead.
    max_retries:
        The maximum number of retries allowed.

    Returns
    -------
    bool
        ``True`` only when *all* of the following hold:

        * ``enrichment_status`` is retryable (see :func:`is_retryable`), and
        * ``max_retries`` is positive, and
        * ``retry_count`` is strictly less than ``max_retries``.

        Otherwise ``False`` — i.e. for non-retryable statuses, for a
        non-positive ``max_retries``, and once ``retry_count`` has reached or
        exceeded ``max_retries`` (so the count can never exceed the ceiling).
    """
    if not is_retryable(enrichment_status):
        return False
    if max_retries <= 0:
        return False
    return retry_count < max_retries
