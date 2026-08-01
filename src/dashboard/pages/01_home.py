from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard.utils.db import get_companies, get_home_data
from src.dashboard.utils.formatting import format_metric

st.title("Home / Overview")
st.caption("Nifty 100 market summary, sector distribution and quality leaders")

year = st.sidebar.selectbox("Financial year", list(range(2024, 2018, -1)), key="home_year")
data = get_home_data(year)

if data.empty:
    st.warning("No dashboard data is available for the selected year.")
    st.stop()

avg_roe = pd.to_numeric(data["return_on_equity_pct"], errors="coerce").mean()
median_pe = pd.to_numeric(data["pe_ratio"], errors="coerce").median()
median_de = pd.to_numeric(data["debt_to_equity"], errors="coerce").median()
total_companies = data["company_id"].nunique()
median_cagr = pd.to_numeric(data["revenue_cagr_5yr"], errors="coerce").median()
debt_free = int((pd.to_numeric(data["debt_to_equity"], errors="coerce") == 0).sum())

row1 = st.columns(3)
row2 = st.columns(3)
row1[0].metric("Average ROE", format_metric(avg_roe, "%"))
row1[1].metric("Median P/E", format_metric(median_pe, "x"))
row1[2].metric("Median D/E", format_metric(median_de, "x"))
row2[0].metric("Total Companies", f"{total_companies}")
row2[1].metric("Median Revenue CAGR 5yr", format_metric(median_cagr, "%"))
row2[2].metric("Debt-Free Companies", f"{debt_free}")

left, right = st.columns([1.15, 1])
with left:
    companies = get_companies()
    sector_counts = (
        companies.assign(broad_sector=companies["broad_sector"].fillna("Unclassified"))
        .groupby("broad_sector", as_index=False)
        .agg(company_count=("company_id", "nunique"))
    )
    fig = px.pie(
        sector_counts,
        names="broad_sector",
        values="company_count",
        hole=0.55,
        title="Sector Breakdown",
    )
    fig.update_layout(margin=dict(l=10, r=10, t=55, b=10))
    st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Top 5 by Composite Quality Score")
    top = data.copy()
    top["composite_quality_score"] = pd.to_numeric(
        top["composite_quality_score"], errors="coerce"
    )
    top = top.dropna(subset=["composite_quality_score"]).nlargest(
        5, "composite_quality_score"
    )
    display = top[
        ["company_id", "company_name", "broad_sector", "composite_quality_score"]
    ].rename(
        columns={
            "company_id": "Ticker",
            "company_name": "Company",
            "broad_sector": "Sector",
            "composite_quality_score": "Quality Score",
        }
    )
    st.dataframe(display, width="stretch", hide_index=True)

missing_ratio = int(data["return_on_equity_pct"].isna().sum())
if missing_ratio:
    st.info(
        f"{missing_ratio} companies do not have a ratio record for {year}; "
        "available companies are used in calculated KPIs."
    )
