"""
Streamlit dashboard for the AI Export Intelligence Pipeline (Task 24).

A lightweight, read-only dashboard over the existing FastAPI service. It is
purely a presentation layer: it never touches the database, the pipeline, or any
LLM directly — every piece of data is fetched over HTTP from the API.

Run it with::

    # 1. start the API (separate terminal)
    uvicorn src.api.main:app --reload

    # 2. start the dashboard
    #    PowerShell: $env:API_BASE_URL="http://localhost:8000"
    streamlit run dashboard/app.py

Design notes:

* The API base URL is read from the ``API_BASE_URL`` environment variable and
  defaults to ``http://localhost:8000``.
* No API call is made at import time. The API is only contacted while a page is
  being rendered, so the module imports cleanly even when the API is down.
* All requests go through :func:`api_get`, which applies a timeout, swallows
  ``requests`` exceptions, and shows a friendly message instead of a stack trace.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests
import streamlit as st

# Read the API base URL once at import time (this is a pure ``os.getenv`` call,
# not a network call) and normalise the trailing slash so paths concatenate
# cleanly into ``{base}/leads`` etc.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

# A short timeout keeps the dashboard responsive when the API is unreachable.
REQUEST_TIMEOUT_SECONDS = 10


def api_get(path: str, params: Optional[dict[str, Any]] = None) -> Optional[Any]:
    """GET ``{API_BASE_URL}{path}`` and return parsed JSON, or ``None`` on error.

    Network and HTTP errors are handled here so individual pages never see a
    raw exception: a user-friendly ``st.warning``/``st.error`` is shown and
    ``None`` is returned. Stack traces are never surfaced to the user.
    """
    url = f"{API_BASE_URL}{path}"
    try:
        response = requests.get(
            url, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error(
            f"Could not reach the API at {API_BASE_URL}. "
            "Is the FastAPI server running? "
            "Start it with `uvicorn src.api.main:app --reload`."
        )
    except requests.exceptions.Timeout:
        st.error(
            f"The API at {API_BASE_URL} did not respond in time. "
            "Please try again in a moment."
        )
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        st.warning(f"The API returned an error (HTTP {status}) for {path}.")
    except requests.exceptions.RequestException:
        st.error(f"Something went wrong while calling the API at {url}.")
    return None


def page_overview() -> None:
    """Overview page: total leads, average score, and score distribution."""
    st.header("Overview")

    leads = api_get("/leads")
    if leads is None:
        # api_get already showed a friendly error message.
        return
    if not leads:
        st.info("No scored leads yet. Run the pipeline to populate data.")
        return

    scores = [lead["score"] for lead in leads if lead.get("score") is not None]

    col1, col2 = st.columns(2)
    col1.metric("Total leads", len(leads))
    if scores:
        col2.metric("Average score", f"{sum(scores) / len(scores):.1f}")

    if scores:
        st.subheader("Score distribution")
        # Bucket scores into 0-9, 10-19, ... 90-100 bins and chart the counts.
        bins = {f"{b}-{b + 9}": 0 for b in range(0, 100, 10)}
        for score in scores:
            bucket = min(int(score) // 10 * 10, 90)
            bins[f"{bucket}-{bucket + 9}"] += 1
        st.bar_chart(bins)


def page_lead_list() -> None:
    """Lead List page: a score slider that filters the scored-lead table."""
    st.header("Lead List")

    min_score = st.slider(
        "Minimum score",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=1.0,
    )

    leads = api_get("/leads", params={"min_score": min_score})
    if leads is None:
        return
    if not leads:
        st.info("No leads match the selected minimum score.")
        return

    st.caption(f"{len(leads)} lead(s) with score >= {min_score:.0f}")
    st.dataframe(leads, use_container_width=True)


def page_lead_detail() -> None:
    """Lead Detail page: pick a lead and show its full record as JSON."""
    st.header("Lead Detail")

    leads = api_get("/leads")
    if leads is None:
        return
    if not leads:
        st.info("No scored leads yet. Run the pipeline to populate data.")
        return

    # Map a human-readable label to each lead id for the selectbox.
    options = {
        (
            f"{lead.get('company_name', 'Unknown')} "
            f"(score {lead.get('score', 0):.1f}) "
            f"[{lead.get('scored_lead_id', '?')}]"
        ): lead.get("scored_lead_id")
        for lead in leads
    }

    selected_label = st.selectbox("Select a lead", list(options.keys()))
    lead_id = options.get(selected_label)
    if not lead_id:
        return

    detail = api_get(f"/leads/{lead_id}")
    if detail is None:
        return
    st.json(detail)


def page_pipeline_runs() -> None:
    """Pipeline Runs page: run summaries plus their data quality reports."""
    st.header("Pipeline Runs")

    runs = api_get("/pipeline-runs")
    if runs is None:
        return
    if not runs:
        st.info("No pipeline runs recorded yet.")
        return

    st.subheader("Runs")
    st.dataframe(runs, use_container_width=True)

    # Best-effort: fetch each run's data quality report. A run may not have a
    # report yet, so a 404 is expected and must not break the page.
    reports = []
    for run in runs:
        run_id = run.get("pipeline_run_id")
        if not run_id:
            continue
        report = api_get(f"/pipeline-runs/{run_id}/report")
        if report is not None:
            reports.append(report)

    st.subheader("Data quality reports")
    if reports:
        st.dataframe(reports, use_container_width=True)
    else:
        st.info("No data quality reports available for these runs yet.")


PAGES = {
    "Overview": page_overview,
    "Lead List": page_lead_list,
    "Lead Detail": page_lead_detail,
    "Pipeline Runs": page_pipeline_runs,
}


def main() -> None:
    """Render the dashboard: sidebar navigation + the selected page."""
    st.set_page_config(page_title="AI Export Intelligence", layout="wide")
    st.sidebar.title("AI Export Intelligence")
    st.sidebar.caption(f"API: {API_BASE_URL}")
    choice = st.sidebar.radio("Navigate", list(PAGES.keys()))
    PAGES[choice]()


if __name__ == "__main__":
    main()
