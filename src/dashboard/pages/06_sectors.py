from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard.utils.db import get_sector_data, get_sector_names

st.title("Sector Analysis")
st.caption("Revenue vs ROE bubble map and sector median KPI profile")

sectors = get_sector_names()
sector = st.selectbox("Sector", sectors, key="sector_name")
year = st.sidebar.selectbox("Financial year", list(range(2024, 2018, -1)), key="sector_year")
data = get_sector_data(sector, year).copy()

if data.empty:
    st.warning("No sector data are available for this selection.")
    st.stop()

for column in ["sales", "return_on_equity_pct", "market_cap_crore", "free_cash_flow_cr", "pe_ratio"]:
    data[column] = pd.to_numeric(data[column], errors="coerce")

bubble = data.dropna(subset=["sales", "return_on_equity_pct", "market_cap_crore"])
bubble = bubble[bubble["market_cap_crore"] > 0]
if bubble.empty:
    st.info("The selected sector does not have enough data for the bubble chart.")
else:
    fig = px.scatter(
        bubble,
        x="sales",
        y="return_on_equity_pct",
        size="market_cap_crore",
        color="sub_sector",
        hover_name="company_name",
        hover_data={"company_id": True, "market_cap_crore": ":,.0f", "sales": ":,.0f"},
        size_max=65,
        title=f"{sector}: Revenue vs ROE ({year})",
        labels={"sales": "Revenue (₹ Cr)", "return_on_equity_pct": "ROE (%)"},
    )
    st.plotly_chart(fig, use_container_width=True)

if (data["market_cap_crore"] > 0).any():
    data["fcf_yield_pct"] = data["free_cash_flow_cr"] / data["market_cap_crore"] * 100
else:
    data["fcf_yield_pct"] = pd.NA

metrics = {
    "ROE %": "return_on_equity_pct",
    "ROCE %": "return_on_capital_employed_pct",
    "Net Profit Margin %": "net_profit_margin_pct",
    "Revenue CAGR 5yr %": "revenue_cagr_5yr",
    "PAT CAGR 5yr %": "pat_cagr_5yr",
    "Debt-to-Equity": "debt_to_equity",
    "P/E": "pe_ratio",
    "FCF Yield %": "fcf_yield_pct",
}
medians = []
for label, column in metrics.items():
    value = pd.to_numeric(data[column], errors="coerce").median()
    medians.append({"Metric": label, "Sector Median": value})
median_df = pd.DataFrame(medians).dropna(subset=["Sector Median"])
if median_df.empty:
    st.info("Sector median KPIs are unavailable.")
else:
    fig = px.bar(
        median_df,
        x="Metric",
        y="Sector Median",
        text_auto=".2f",
        title=f"{sector} Median KPIs — {year}",
    )
    fig.update_layout(xaxis_tickangle=-25)
    st.plotly_chart(fig, use_container_width=True)
