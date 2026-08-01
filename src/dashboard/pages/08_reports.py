from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

from src.dashboard.utils.db import get_companies, get_documents

st.title("Annual Reports")
st.caption("BSE annual-report PDF repository with cached availability checks")

companies = get_companies()
labels = {row.company_id: f"{row.company_name} — {row.company_id}" for row in companies.itertuples()}
ticker = st.selectbox(
    "Company",
    companies["company_id"].tolist(),
    format_func=lambda value: labels.get(value, value),
    key="report_company",
)
reports = get_documents(ticker)
if reports.empty:
    st.warning("No annual-report records are available for this company.")
    st.stop()

HEADERS = {"User-Agent": "Mozilla/5.0 N100-Analytics/1.0"}


def _check_one(url: str) -> tuple[str, int | None]:
    if not isinstance(url, str) or not url.strip():
        return "unavailable", None
    try:
        response = requests.head(url, allow_redirects=True, timeout=6, headers=HEADERS)
        status = response.status_code
        if status in (403, 405) or status >= 500:
            with requests.get(url, allow_redirects=True, timeout=7, headers=HEADERS, stream=True) as fallback:
                status = fallback.status_code
        if 200 <= status < 400:
            return "available", status
        if status == 404:
            return "unavailable", status
        return "unverified", status
    except requests.RequestException:
        return "unverified", None


@st.cache_data(ttl=3600, show_spinner=False)
def check_urls(urls: tuple[str, ...]) -> dict[str, tuple[str, int | None]]:
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as executor:
        results = list(executor.map(_check_one, urls))
    return dict(zip(urls, results))

urls = tuple(reports["report_url"].fillna("").astype(str).tolist())
with st.spinner("Checking report links..."):
    availability = check_urls(urls)

for row in reports.itertuples():
    year = int(row.report_year) if pd.notna(row.report_year) else "Unknown year"
    url = str(row.report_url or "")
    state, status = availability.get(url, ("unverified", None))
    year_col, action_col = st.columns([1, 4])
    year_col.markdown(f"### {year}")
    if state == "available":
        action_col.link_button(f"Open {year} Annual Report", url, use_container_width=True)
    elif state == "unavailable":
        action_col.error(f"Report unavailable (HTTP {status or 404})")
    else:
        if url:
            action_col.markdown(f"[Open report without verification]({url})")
            action_col.warning(f"Availability could not be verified{f' (HTTP {status})' if status else ''}.")
        else:
            action_col.error("Report unavailable")
