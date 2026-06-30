"""
Property 13 — Enrichment status classification.

The enrichment failure taxonomy is a fixed, lowercase vocabulary.  The retry
classifier must keep classification inside that taxonomy:

    * the retryable statuses are exactly {timeout, network_error, rate_limited}
    * every other known status is non-retryable
    * any string outside the taxonomy is non-retryable
    * ``should_retry`` is never ``True`` for an unknown or non-retryable status,
      regardless of the retry counters

Pure, deterministic property test over ``src.enrichment.retry_policy``: no
database, no network, no OpenAI key.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from src.enrichment.retry_policy import (
    RETRYABLE_STATUSES,
    is_retryable,
    should_retry,
)

# The full nine-value taxonomy, mirrored from the spec / migration / ORM.
RETRYABLE = ("timeout", "network_error", "rate_limited")
NON_RETRYABLE = (
    "success",
    "validation_failed",
    "empty_response",
    "invalid_json",
    "context_retrieval_failed",
    "unknown_error",
)
KNOWN_STATUSES = frozenset(RETRYABLE + NON_RETRYABLE)

# Arbitrary strings that are not part of the known taxonomy.  The filter
# rejection rate is effectively zero (random text never equals a status word),
# so this triggers no Hypothesis health check.
_unknown_status = st.text(min_size=0, max_size=20).filter(lambda s: s not in KNOWN_STATUSES)

_counts = st.integers(min_value=-3, max_value=10)


def test_taxonomy_is_nine_values_and_constant_matches():
    """Sanity anchor: the taxonomy has exactly nine values and the module's
    retryable constant is exactly the three transient statuses."""
    assert len(KNOWN_STATUSES) == 9
    assert RETRYABLE_STATUSES == frozenset(RETRYABLE)


@settings(max_examples=100)
@given(status=st.sampled_from(RETRYABLE))
def test_retryable_statuses_are_retryable(status):
    assert is_retryable(status) is True


@settings(max_examples=100)
@given(status=st.sampled_from(NON_RETRYABLE))
def test_non_retryable_statuses_are_not_retryable(status):
    assert is_retryable(status) is False


@settings(max_examples=100)
@given(status=_unknown_status)
def test_unknown_statuses_are_not_retryable(status):
    assert is_retryable(status) is False


@settings(max_examples=100)
@given(
    status=st.one_of(st.sampled_from(NON_RETRYABLE), _unknown_status),
    retry_count=_counts,
    max_retries=_counts,
)
def test_should_retry_never_true_for_unknown_or_non_retryable(status, retry_count, max_retries):
    assert should_retry(status, retry_count, max_retries) is False


@settings(max_examples=100)
@given(status=st.sampled_from(tuple(sorted(KNOWN_STATUSES | {"", "weird-status", "RETRY"}))))
def test_classification_partitions_the_taxonomy(status):
    """Membership in the retryable set is the single source of truth: a status
    is retryable iff it is one of the three transient statuses."""
    assert is_retryable(status) is (status in RETRYABLE)
