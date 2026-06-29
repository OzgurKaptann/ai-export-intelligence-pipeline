"""
Deterministic prompt builder for export-lead LLM enrichment.

`build_enrichment_prompt` turns a single lead (a :class:`RawLeadSchema` or a
plain mapping) into the natural-language instruction sent to an LLM during the
enrichment stage.  The prompt:

* presents the lead's fields in a clear, structured block,
* optionally appends a separated context section (e.g. knowledge-base
  retrieval output) when one is supplied,
* and spells out exactly what JSON the model must return — the field names,
  numeric ranges and shape that :class:`EnrichmentOutputSchema` will later
  validate against.

The function is **pure and offline**.  It builds a string and nothing else: it
never calls an external API, never reads ``OPENAI_API_KEY``, never opens a
database session or the repository layer, writes no files and performs no
network I/O.  Output is fully deterministic — the same lead and context always
produce byte-for-byte the same prompt, with no timestamps or random values — so
it is safe to use in tests and to feed into a deterministic enrichment path.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Union

from src.validation.input_schemas import RawLeadSchema

# Identifies the prompt template so enrichment records can store which prompt
# produced an output.  Bump this when the wording below changes materially.
PROMPT_VERSION = "v1.0"

# Required lead fields — always rendered, since RawLeadSchema guarantees them.
# (label, key) pairs keep the rendered order stable and human-readable.
_REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("Company name", "company_name"),
    ("Contact email", "contact_email"),
    ("Product category", "product_category"),
)

# Optional lead fields — rendered only when present, so a missing value never
# leaks a literal "None" into the prompt.
_OPTIONAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("Target market", "target_market"),
    ("Annual revenue", "annual_revenue"),
    ("Contact phone", "contact_phone"),
)


def _to_dict(lead: Union[RawLeadSchema, Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return a mapping view of ``lead`` without mutating the input."""
    if isinstance(lead, RawLeadSchema):
        return lead.model_dump()
    return lead


def _present(value: Any) -> Optional[str]:
    """Return a cleaned string for ``value``, or ``None`` if it is absent.

    ``None`` and empty / whitespace-only values are treated as absent so that
    optional fields are simply omitted rather than rendered as ``"None"`` or a
    blank line.  Non-string values (e.g. ``annual_revenue`` floats) are
    stringified.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_enrichment_prompt(
    lead: Union[RawLeadSchema, Mapping[str, Any]],
    context: Optional[str] = None,
) -> str:
    """Build the deterministic enrichment prompt for a single export lead.

    Parameters
    ----------
    lead:
        Either a :class:`RawLeadSchema` instance or a plain mapping carrying
        the lead's fields (``company_name``, ``contact_email``,
        ``product_category`` and the optional ``target_market``,
        ``annual_revenue``, ``contact_phone``).
    context:
        Optional supporting context (for example knowledge-base retrieval
        output).  When provided and non-empty it is included in its own clearly
        separated section; when ``None`` or blank, no context section is
        emitted at all.

    Returns
    -------
    str
        A multi-line prompt string.  The same ``lead`` and ``context`` always
        produce the same string — there are no timestamps or random values.
    """
    data = _to_dict(lead)

    lines: list[str] = [
        "You are an export-market analyst enriching B2B export leads.",
        "Assess the lead below using realistic export and international-trade "
        "reasoning, then return your assessment as JSON.",
        "",
        "Lead:",
    ]

    for label, key in _REQUIRED_FIELDS:
        value = _present(data.get(key))
        # Required fields are guaranteed by RawLeadSchema; fall back gracefully
        # for raw dicts that omit them rather than emitting "None".
        lines.append(f"- {label}: {value if value is not None else 'unknown'}")

    for label, key in _OPTIONAL_FIELDS:
        value = _present(data.get(key))
        if value is not None:
            lines.append(f"- {label}: {value}")

    context_text = _present(context)
    if context_text is not None:
        lines.extend(
            [
                "",
                "Additional context:",
                context_text,
            ]
        )

    lines.extend(
        [
            "",
            "Instructions:",
            "- Base your assessment only on the information provided above; do "
            "not invent or assume unsupported facts about the company.",
            "- Apply realistic export, market-entry and business reasoning.",
            "",
            "Return ONLY a single JSON object compatible with the "
            "EnrichmentOutputSchema, with exactly these fields:",
            "- market_potential: number between 0 and 1",
            "- export_readiness: number between 0 and 1",
            "- recommended_markets: array of country/market name strings",
            "- risk_assessment: object containing overall_risk, a number "
            "between 0 and 1",
            "- confidence_score: number between 0 and 1",
            "",
            "All numeric values must lie within their stated ranges. Do not "
            "include any text outside the JSON object.",
        ]
    )

    return "\n".join(lines)


class EnrichmentPromptBuilder:
    """Thin object wrapper around :func:`build_enrichment_prompt`.

    Provided for call sites that prefer an injectable collaborator over a bare
    function.  It holds no state and simply delegates to the module-level
    function, so both entry points produce identical output.
    """

    prompt_version = PROMPT_VERSION

    def build(
        self,
        lead: Union[RawLeadSchema, Mapping[str, Any]],
        context: Optional[str] = None,
    ) -> str:
        """Delegate to :func:`build_enrichment_prompt`."""
        return build_enrichment_prompt(lead, context)
