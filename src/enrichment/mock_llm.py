"""
Deterministic mock LLM enrichment provider.

`MockLLMProvider` produces synthetic, schema-valid enrichment output for a
lead **without** calling any external API, requiring an ``OPENAI_API_KEY``,
touching the database, or using the repository layer.  It exists so the
enrichment stage — and eventually the whole pipeline — can be developed,
tested and demonstrated locally with no credentials and no network.

Determinism
-----------
Output is a pure function of the lead's content (and the optional ``context``
string).  The lead is normalized and serialized into a canonical string, that
string is SHA-256 hashed, and the hash seeds a :class:`random.Random`
instance.  The same input therefore always yields the same enrichment, while
meaningfully different leads yield different — but equally stable — output.

The module is side-effect free: it never mutates its input, never writes
files, and performs no I/O of any kind.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Mapping, Optional, Union

from src.validation.enrichment_schemas import (
    EnrichmentOutputSchema,
    RiskAssessmentSchema,
)
from src.validation.input_schemas import RawLeadSchema

# Hardcoded provider identity, mirroring the real LLM provider's contract so
# callers can record which "model" produced an enrichment.
MODEL_NAME = "mock-llm-v1"

# Fields that influence the generated enrichment, in canonical order.  These
# are the lead attributes a real enrichment model would reason over; including
# them all means two leads that differ in any of them produce different output.
_RELEVANT_FIELDS = (
    "company_name",
    "contact_email",
    "product_category",
    "annual_revenue",
    "target_market",
)

# Delimiter between canonical field values; a non-printable control character
# avoids accidental collisions when a value contains the delimiter itself.
_FIELD_DELIMITER = "\x1f"  # ASCII unit separator

# Synthetic pool of plausible export markets the mock can recommend.  These are
# fixed so output stays stable across runs and machines.
_MARKET_POOL = (
    "Germany",
    "United States",
    "United Kingdom",
    "France",
    "Netherlands",
    "United Arab Emirates",
    "Japan",
    "Canada",
    "Australia",
    "Spain",
)


def _normalize(value: Any) -> str:
    """Reduce a single field value to a canonical string form.

    ``None`` and empty / whitespace-only values collapse to ``""`` so that a
    missing, empty and ``None`` field all behave identically; every other
    value is stripped and lowercased so case and padding differences do not
    change the seed.
    """
    if value is None:
        return ""
    return str(value).strip().lower()


def _to_dict(lead: Union[RawLeadSchema, Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return a mapping view of ``lead`` without mutating the input."""
    if isinstance(lead, RawLeadSchema):
        return lead.model_dump()
    return lead


def _seed_from_lead(
    lead: Union[RawLeadSchema, Mapping[str, Any]],
    context: Optional[str],
) -> int:
    """Derive a stable integer seed from the lead's content and ``context``.

    The serialization is deterministic and cross-process stable (SHA-256, not
    the salted built-in ``hash()``), so the same lead always seeds the same
    pseudo-random sequence.
    """
    data = _to_dict(lead)
    parts = [_normalize(data.get(name)) for name in _RELEVANT_FIELDS]
    parts.append(_normalize(context))
    canonical = _FIELD_DELIMITER.join(parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return int(digest, 16)


def _round01(value: float) -> float:
    """Clamp to [0, 1] and round to 4 decimals for tidy, stable output."""
    return round(min(1.0, max(0.0, value)), 4)


class MockLLMProvider:
    """Deterministic, offline stand-in for a real LLM enrichment provider.

    Construct once and call :meth:`enrich_lead` per lead.  The provider holds
    no mutable state between calls, so a single instance can be reused freely
    and is safe to share.
    """

    model_name = MODEL_NAME

    def enrich_lead(
        self,
        lead: Union[RawLeadSchema, Mapping[str, Any]],
        context: Optional[str] = None,
    ) -> EnrichmentOutputSchema:
        """Generate synthetic, schema-valid enrichment for ``lead``.

        Parameters
        ----------
        lead:
            Either a :class:`RawLeadSchema` instance or a plain mapping with
            the lead's fields.
        context:
            Optional retrieved-context string (e.g. from a knowledge base).
            It participates in the seed, so different context yields different
            — but still deterministic — output for the same lead.

        Returns
        -------
        EnrichmentOutputSchema
            A validated enrichment instance.  All numeric fields lie in
            ``[0, 1]`` and ``recommended_markets`` is a list of strings.
        """
        rng = random.Random(_seed_from_lead(lead, context))

        market_potential = _round01(rng.uniform(0.05, 0.95))
        export_readiness = _round01(rng.uniform(0.05, 0.95))
        overall_risk = _round01(rng.uniform(0.05, 0.95))

        # Confidence is derived from the other signals (lower when risk is high)
        # plus a little deterministic jitter, then clamped — realistic-looking
        # without ever leaving the valid range.
        confidence_score = _round01(
            (market_potential + export_readiness + (1.0 - overall_risk)) / 3.0
            + rng.uniform(-0.1, 0.1)
        )

        recommended_markets = self._recommend_markets(lead, rng)

        risk_assessment = RiskAssessmentSchema(
            overall_risk=overall_risk,
            regulatory_risk=_round01(rng.uniform(0.0, 1.0)),
            market_volatility=_round01(rng.uniform(0.0, 1.0)),
        )

        return EnrichmentOutputSchema(
            market_potential=market_potential,
            export_readiness=export_readiness,
            risk_assessment=risk_assessment,
            recommended_markets=recommended_markets,
            confidence_score=confidence_score,
        )

    def _recommend_markets(
        self,
        lead: Union[RawLeadSchema, Mapping[str, Any]],
        rng: random.Random,
    ) -> list[str]:
        """Build a stable, deduplicated list of recommended markets.

        The lead's ``target_market`` is always included first when present, so
        a caller-supplied market surfaces in the recommendation.  The rest are
        drawn deterministically from a synthetic pool using ``rng``.
        """
        data = _to_dict(lead)
        target_market = data.get("target_market")

        markets: list[str] = []
        if target_market is not None and str(target_market).strip():
            markets.append(str(target_market).strip())

        # Pick 2–3 additional markets from the pool, preserving determinism.
        count = rng.randint(2, 3)
        pool = list(_MARKET_POOL)
        rng.shuffle(pool)
        for market in pool:
            if len(markets) >= count + (1 if markets else 0):
                break
            if market not in markets:
                markets.append(market)

        return markets
