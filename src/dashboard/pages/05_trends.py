from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from functools import reduce

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.utils.db import get_bs, get_cf, get_companies, get_pl, get_ratios
from src.dashboard.utils.formatting import clean_year

st.title("Trend Analysis")
st.caption("Overlay up to three metrics with ten-year YoY annotations")

companies = get_companies()
labels = {row.company_id: f"{row.company_name} — {row.company_id}" for row in companies.itertuples()}
ticker = st.selectbox(
    "Company",
    companies["company_id"].tolist(),
    format_func=lambda value: labels.get(value, value),
    key="trend_company",
)

frames = []
for frame in [clean_year(get_pl(ticker)), clean_year(get_bs(ticker)), clean_year(get_cf(ticker)), clean_year(get_ratios(ticker))]:
    if not frame.empty:
        frame = frame.sort_values("year").drop_duplicates("year", keep="last")
        frames.append(frame)
if not frames:
    st.warning("No trend data are available for this company.")
    st.stop()

prepared_frames: list[pd.DataFrame] = []

for frame in frames:
    if frame is None or frame.empty:
        continue

    current = frame.copy()

    if "year" not in current.columns:
        continue

    current["year"] = pd.to_numeric(
        current["year"],
        errors="coerce",
    )

    current = current.dropna(subset=["year"])
    current["year"] = current["year"].astype(int)

    current = current.drop(
        columns=["company_id", "id"],
        errors="ignore",
    )

    current = current.sort_values("year")
    current = current.drop_duplicates(
        subset=["year"],
        keep="last",
    )

    current = current.loc[
        :,
        ~current.columns.duplicated(),
    ]

    prepared_frames.append(
        current.set_index("year")
    )

if not prepared_frames:
    st.warning(
        "No financial history is available "
        "for the selected company."
    )
    st.stop()

data = pd.concat(
    prepared_frames,
    axis=1,
)

data = data.loc[
    :,
    ~data.columns.duplicated(keep="first"),
]

data = (
    data
    .reset_index()
    .sort_values("year")
    .reset_index(drop=True)
)
data = data.loc[:, ~data.columns.str.endswith("_dup")].sort_values("year").tail(10)

metric_map = {
    "Revenue": "sales",
    "Net Profit": "net_profit",
    "Operating Profit": "operating_profit",
    "ROE": "return_on_equity_pct",
    "ROCE": "return_on_capital_employed_pct",
    "Free Cash Flow": "free_cash_flow_cr",
    "Debt-to-Equity": "debt_to_equity",
    "Operating Profit Margin": "operating_profit_margin_pct",
    "Total Assets": "total_assets",
    "Borrowings": "borrowings",
}
available = [label for label, column in metric_map.items() if column in data.columns and pd.to_numeric(data[column], errors="coerce").notna().any()]
default = available[:2]
selected = st.multiselect("Metrics — select up to 3", available, default=default, key="trend_metrics")
if len(selected) > 3:
    st.warning("Select a maximum of three metrics.")
    st.stop()
if not selected:
    st.info("Select at least one metric.")
    st.stop()

normalise = st.checkbox("Normalise each metric to an index of 100", value=False)
fig = go.Figure()
for label in selected:
    column = metric_map[label]
    values = pd.to_numeric(data[column], errors="coerce")
    yoy = values.pct_change(fill_method=None) * 100
    plotted = values.copy()
    if normalise:
        valid = values.dropna()
        base = valid.iloc[0] if not valid.empty else np.nan
        plotted = values / base * 100 if pd.notna(base) and base != 0 else values
    text = ["" if pd.isna(value) else f"{value:+.1f}%" for value in yoy]
    fig.add_trace(
        go.Scatter(
            x=data["year"],
            y=plotted,
            mode="lines+markers+text",
            text=text,
            textposition="top center",
            name=label,
            connectgaps=False,
        )
    )
fig.update_layout(
    title=f"{ticker} — Latest {data['year'].nunique()} Available Years",
    xaxis_title="Financial Year",
    yaxis_title="Index (100 = first year)" if normalise else "Metric Value",
    hovermode="x unified",
    margin=dict(l=25, r=25, t=65, b=25),
)
st.plotly_chart(fig, use_container_width=True)
if data["year"].nunique() < 10:
    st.info(f"Only {data['year'].nunique()} years of combined data are available for this company.")
