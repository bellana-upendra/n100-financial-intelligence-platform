from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.utils.db import (
    get_peer_group_data,
    get_peer_group_names,
    get_peer_years,
)

st.title("Peer Comparison")
st.caption("Eight-metric radar chart and side-by-side KPI comparison for 11 peer groups")

groups = get_peer_group_names()
if not groups:
    st.warning("No peer groups are available.")
    st.stop()

group = st.selectbox("Peer group", groups, key="peer_group")
years = get_peer_years(group)
year_options = ["Latest available"] + years
year_choice = st.selectbox("Financial year", year_options, key="peer_year")
year = None if year_choice == "Latest available" else int(year_choice)
data = get_peer_group_data(group, year)

if data.empty:
    st.warning("No peer data are available for the selected group and year.")
    st.stop()

data = data.drop_duplicates(subset=["company_id"], keep="last").copy()
labels = {row.company_id: f"{row.company_name} — {row.company_id}" for row in data.itertuples()}
selected = st.selectbox(
    "Benchmark/selected company",
    data["company_id"].tolist(),
    format_func=lambda ticker: labels.get(ticker, ticker),
    key="peer_company",
)

metrics = {
    "ROE": "return_on_equity_pct",
    "ROCE": "return_on_capital_employed_pct",
    "Net Profit Margin": "net_profit_margin_pct",
    "D/E Score": "debt_to_equity",
    "FCF Score": "free_cash_flow_cr",
    "PAT CAGR 5yr": "pat_cagr_5yr",
    "Revenue CAGR 5yr": "revenue_cagr_5yr",
    "Composite Score": "composite_quality_score",
}

scores = pd.DataFrame(index=data.index)
for label, column in metrics.items():
    values = pd.to_numeric(data[column], errors="coerce")
    if values.notna().sum() <= 1:
        scores[label] = 50.0
    elif label == "D/E Score":
        scores[label] = values.rank(pct=True, ascending=False) * 100
    else:
        scores[label] = values.rank(pct=True, ascending=True) * 100
scores["company_id"] = data["company_id"].values

selected_row = scores[scores["company_id"] == selected]
if selected_row.empty:
    st.warning("Selected company has no peer metrics for this year.")
    st.stop()

theta = list(metrics.keys())
company_values = selected_row[theta].iloc[0].fillna(50).tolist()
peer_average = scores[theta].mean(skipna=True).fillna(50).tolist()

fig = go.Figure()
fig.add_trace(
    go.Scatterpolar(
        r=company_values + [company_values[0]],
        theta=theta + [theta[0]],
        fill="toself",
        name=selected,
    )
)
fig.add_trace(
    go.Scatterpolar(
        r=peer_average + [peer_average[0]],
        theta=theta + [theta[0]],
        mode="lines",
        line=dict(dash="dash"),
        name="Peer Group Average",
    )
)
fig.update_layout(
    title=f"{selected} vs {group} Average",
    polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
    showlegend=True,
    margin=dict(l=45, r=45, t=70, b=45),
)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Peer KPI Table")
columns = [
    "company_id", "company_name", "is_benchmark", "return_on_equity_pct",
    "return_on_capital_employed_pct", "net_profit_margin_pct", "debt_to_equity",
    "free_cash_flow_cr", "pat_cagr_5yr", "revenue_cagr_5yr", "eps_cagr_5yr",
    "interest_coverage", "asset_turnover", "composite_quality_score",
]
columns = [column for column in columns if column in data.columns]
table = data[columns].rename(
    columns={
        "company_id": "Ticker", "company_name": "Company", "is_benchmark": "Benchmark",
        "return_on_equity_pct": "ROE %", "return_on_capital_employed_pct": "ROCE %",
        "net_profit_margin_pct": "NPM %", "debt_to_equity": "D/E",
        "free_cash_flow_cr": "FCF ₹ Cr", "pat_cagr_5yr": "PAT CAGR 5yr %",
        "revenue_cagr_5yr": "Revenue CAGR 5yr %", "eps_cagr_5yr": "EPS CAGR 5yr %",
        "interest_coverage": "ICR", "asset_turnover": "Asset Turnover",
        "composite_quality_score": "Composite Score",
    }
)

def highlight(row):
    return ["background-color: #ffe69c" if bool(row.get("Benchmark", False)) else "" for _ in row]

st.dataframe(table.style.apply(highlight, axis=1), use_container_width=True, hide_index=True)
