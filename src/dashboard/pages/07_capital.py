from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from src.dashboard.utils.db import get_capital_allocation_data

st.title("Capital Allocation Map")
st.caption("Latest capital allocation pattern for all 92 companies")

data = get_capital_allocation_data().copy()
if data.empty:
    st.warning("Capital allocation data are unavailable.")
    st.stop()

data["capital_allocation_pattern"] = data["capital_allocation_pattern"].fillna("Unclassified")
data["market_cap_crore"] = pd.to_numeric(data["market_cap_crore"], errors="coerce")
data["treemap_size"] = data["market_cap_crore"].where(data["market_cap_crore"] > 0, 1.0).fillna(1.0)

fig = px.treemap(
    data,
    path=["capital_allocation_pattern", "company_name"],
    values="treemap_size",
    hover_data=["company_id", "broad_sector", "free_cash_flow_cr", "cfo_quality_label"],
    title="Nifty 100 Capital Allocation Patterns",
)
fig.update_layout(margin=dict(l=5, r=5, t=55, b=5))
st.plotly_chart(fig, width="stretch")

patterns = sorted(data["capital_allocation_pattern"].dropna().unique().tolist())
selected_pattern = st.selectbox(
    "Select a pattern to view its companies",
    patterns,
    key="capital_pattern",
)
selected = data[data["capital_allocation_pattern"] == selected_pattern]
st.subheader(f"{selected_pattern}: {len(selected)} companies")
st.dataframe(
    selected[
        ["company_id", "company_name", "broad_sector", "free_cash_flow_cr", "cfo_quality_label", "market_cap_crore"]
    ].rename(
        columns={
            "company_id": "Ticker", "company_name": "Company", "broad_sector": "Sector",
            "free_cash_flow_cr": "FCF ₹ Cr", "cfo_quality_label": "CFO Quality",
            "market_cap_crore": "Market Cap ₹ Cr",
        }
    ),
    width="stretch",
    hide_index=True,
)
if "Unclassified" in patterns:
    count = int((data["capital_allocation_pattern"] == "Unclassified").sum())
    st.info(f"{count} companies do not have a latest capital-allocation classification.")
