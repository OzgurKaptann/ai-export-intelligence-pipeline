"""
Property 15 — Retry count never exceeds max.

``should_retry`` is the single gate that decides whether another enrichment
attempt is made.  It must never allow the retry count to be pushed past the
configured ceiling.  For arbitrary statuses (known taxonomy plus unknown
strings) and arbitrary integer counters:

    * it is ``False`` once ``current_retry_count >= max_retries``
    * it is ``False`` when ``max_retries <= 0``
    * it is ``True`` only for a retryable status with a positive ceiling and
      ``current_retry_count`` strictly below it

Equivalently, ``should_retry`` equals exactly::

    is_retryable(status) and max_retries > 0 and current_retry_count < max_retries

and whenever it permits a retry, performing that retry (count + 1) still stays
within the ceiling — so no status can bypass the limit.

Pure, deterministic property test: no database, no network, no OpenAI key.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from src.enrichment.retry_policy import is_retryable, should_retry

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

_unknown_status = st.text(min_size=0, max_size=20).filter(lambda s: s not in KNOWN_STATUSES)

# Any status the pipeline might encounter: the whole taxonomy plus junk.
_any_status = st.one_of(st.sampled_from(RETRYABLE + NON_RETRYABLE), _unknown_status)

# Counters span well below zero and above any realistic ceiling.
_count = st.integers(min_value=-5, max_value=12)


@settings(max_examples=100)
@given(status=_any_status, current_retry_count=_count, max_retries=_count)
def test_should_retry_matches_exact_specification(status, current_retry_count, max_retries):
    expected = (
        is_retryable(status)
        and max_retries > 0
        and current_retry_count < max_retries
    )
    assert should_retry(status, current_retry_count, max_retries) is expected


@settings(max_examples=100)
@given(status=_any_status, current_retry_count=_count, max_retries=_count)
def test_no_status_bypasses_the_ceiling(status, current_retry_count, max_retries):
    # At or above the ceiling, no retry is ever permitted.
    if current_retry_count >= max_retries:
        assert should_retry(status, current_retry_count, max_retries) is False
    # A non-positive ceiling never permits a retry.
    if max_retries <= 0:
        assert should_retry(status, current_retry_count, max_retries) is False
    # Whenever a retry is permitted, taking it keeps the count within the limit.
    if should_retry(status, current_retry_count, max_retries):
        assert current_retry_count + 1 <= max_retries


@settings(max_examples=100)
@given(
    status=st.one_of(st.sampled_from(NON_RETRYABLE), _unknown_status),
    current_retry_count=_count,
    max_retries=_count,
)
def test_non_retryable_statuses_never_retry(status, current_retry_count, max_retries):
    assert should_retry(status, current_retry_count, max_retries) is False
