"""
Unit tests for src/enrichment/retry_policy.py.

These tests pin down the retry classifier against the nine-value enrichment
failure taxonomy: which statuses are retryable, and whether another attempt
should be made given the current retry count and ceiling.  They require nothing
external — no database, no network, no ``OPENAI_API_KEY`` — and are fully
deterministic.
"""

from __future__ import annotations

import pytest

from src.enrichment.retry_policy import (
    RETRYABLE_STATUSES,
    is_retryable,
    should_retry,
)

# ---------------------------------------------------------------------------
# The complete enrichment_status taxonomy (9 values) as defined by the spec,
# the migration CHECK constraint and the ORM model.
# ---------------------------------------------------------------------------
RETRYABLE = (
    "timeout",
    "network_error",
    "rate_limited",
)

NON_RETRYABLE = (
    "success",
    "validation_failed",
    "empty_response",
    "invalid_json",
    "context_retrieval_failed",
    "unknown_error",
)

ALL_STATUSES = RETRYABLE + NON_RETRYABLE


def test_taxonomy_has_exactly_nine_statuses():
    """The project defines exactly 9 enrichment_status values; cover them all."""
    assert len(ALL_STATUSES) == 9
    assert len(set(ALL_STATUSES)) == 9


# ---------------------------------------------------------------------------
# is_retryable — explicit acceptance criteria
# ---------------------------------------------------------------------------

def test_is_retryable_timeout_true():
    assert is_retryable("timeout") is True


def test_is_retryable_network_error_true():
    assert is_retryable("network_error") is True


def test_is_retryable_rate_limited_true():
    assert is_retryable("rate_limited") is True


def test_is_retryable_validation_failed_false():
    assert is_retryable("validation_failed") is False


# ---------------------------------------------------------------------------
# is_retryable — full taxonomy coverage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", RETRYABLE)
def test_all_retryable_statuses_are_retryable(status):
    assert is_retryable(status) is True


@pytest.mark.parametrize("status", NON_RETRYABLE)
def test_all_non_retryable_statuses_are_not_retryable(status):
    assert is_retryable(status) is False


@pytest.mark.parametrize(
    "status",
    ["", "TIMEOUT", "Timeout", "time_out", "retry", "completed", "error", "none"],
)
def test_unknown_statuses_are_not_retryable(status):
    """Anything outside the known vocabulary is conservatively non-retryable."""
    assert is_retryable(status) is False


def test_retryable_statuses_constant_matches_expectation():
    assert RETRYABLE_STATUSES == frozenset(RETRYABLE)


# ---------------------------------------------------------------------------
# should_retry — explicit acceptance criteria
# ---------------------------------------------------------------------------

def test_should_retry_timeout_at_ceiling_is_false():
    # Acceptance criterion: should_retry("timeout", 3, 3) is False.
    assert should_retry("timeout", 3, 3) is False


# ---------------------------------------------------------------------------
# should_retry — retry-count behavior for retryable statuses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", RETRYABLE)
def test_should_retry_true_when_count_below_max(status):
    assert should_retry(status, 0, 3) is True
    assert should_retry(status, 1, 3) is True
    assert should_retry(status, 2, 3) is True


@pytest.mark.parametrize("status", RETRYABLE)
def test_should_retry_false_when_count_equals_max(status):
    assert should_retry(status, 3, 3) is False


@pytest.mark.parametrize("status", RETRYABLE)
def test_should_retry_false_when_count_exceeds_max(status):
    assert should_retry(status, 4, 3) is False
    assert should_retry(status, 10, 3) is False


# ---------------------------------------------------------------------------
# should_retry — non-retryable statuses never retry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", NON_RETRYABLE)
def test_should_retry_false_for_non_retryable_even_below_max(status):
    assert should_retry(status, 0, 3) is False


# ---------------------------------------------------------------------------
# should_retry — non-positive max_retries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", RETRYABLE)
@pytest.mark.parametrize("max_retries", [0, -1, -5])
def test_should_retry_false_when_max_retries_non_positive(status, max_retries):
    assert should_retry(status, 0, max_retries) is False


# ---------------------------------------------------------------------------
# Purity — inputs are not mutated
# ---------------------------------------------------------------------------

def test_functions_do_not_mutate_shared_constant():
    before = set(RETRYABLE_STATUSES)
    is_retryable("timeout")
    should_retry("timeout", 0, 3)
    assert set(RETRYABLE_STATUSES) == before


# ---------------------------------------------------------------------------
# Property-style tests (deterministic, no hypothesis dependency)
# ---------------------------------------------------------------------------

def test_property_status_classification_partitions_taxonomy():
    """Property P13: every status is classified, and exactly the three
    transient errors are retryable across the whole taxonomy."""
    retryable = {s for s in ALL_STATUSES if is_retryable(s)}
    assert retryable == set(RETRYABLE)
    # The two sets fully partition the taxonomy with no overlap.
    assert retryable.isdisjoint(set(NON_RETRYABLE))
    assert retryable | set(NON_RETRYABLE) == set(ALL_STATUSES)


def test_property_retry_count_ceiling_never_exceeded():
    """Property P15: should_retry is True only while retry_count < max_retries
    for retryable statuses, so the count can never be pushed past the ceiling."""
    for status in ALL_STATUSES:
        for max_retries in range(0, 6):
            for retry_count in range(0, 8):
                result = should_retry(status, retry_count, max_retries)
                if not is_retryable(status) or max_retries <= 0:
                    assert result is False
                else:
                    assert result is (retry_count < max_retries)
                # Whenever a retry is permitted, the resulting count stays
                # within the ceiling.
                if result:
                    assert retry_count + 1 <= max_retries
