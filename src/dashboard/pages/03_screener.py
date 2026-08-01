from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
import streamlit as st

from src.dashboard.utils.db import get_screener_data

st.title("Financial Screener")
st.caption("Live ten-metric screening with six ready-to-use presets and CSV export")

data = get_screener_data().copy()

PRESETS = {
    "Quality": dict(roe_min=15.0, de_max=1.0, fcf_min=0.0, revenue_cagr_min=10.0,
                    pat_cagr_min=-100.0, opm_min=0.0, pe_max=200.0, pb_max=30.0,
                    dividend_yield_min=0.0, icr_min=0.0),
    "Value": dict(roe_min=-100.0, de_max=2.0, fcf_min=-200000.0, revenue_cagr_min=-100.0,
                  pat_cagr_min=-100.0, opm_min=-100.0, pe_max=20.0, pb_max=3.0,
                  dividend_yield_min=1.0, icr_min=0.0),
    "Growth": dict(roe_min=-100.0, de_max=2.0, fcf_min=-200000.0, revenue_cagr_min=15.0,
                   pat_cagr_min=20.0, opm_min=-100.0, pe_max=200.0, pb_max=30.0,
                   dividend_yield_min=0.0, icr_min=0.0),
    "Dividend": dict(roe_min=-100.0, de_max=20.0, fcf_min=0.0, revenue_cagr_min=-100.0,
                     pat_cagr_min=-100.0, opm_min=-100.0, pe_max=200.0, pb_max=30.0,
                     dividend_yield_min=2.0, icr_min=0.0),
    "Debt-Free": dict(roe_min=12.0, de_max=0.0, fcf_min=-200000.0, revenue_cagr_min=-100.0,
                      pat_cagr_min=-100.0, opm_min=-100.0, pe_max=200.0, pb_max=30.0,
                      dividend_yield_min=0.0, icr_min=0.0),
    "Turnaround": dict(roe_min=-100.0, de_max=20.0, fcf_min=0.0, revenue_cagr_min=0.0,
                       pat_cagr_min=-100.0, opm_min=-100.0, pe_max=200.0, pb_max=30.0,
                       dividend_yield_min=0.0, icr_min=0.0),
}

DEFAULTS = dict(roe_min=-100.0, de_max=20.0, fcf_min=-200000.0, revenue_cagr_min=-100.0,
                pat_cagr_min=-100.0, opm_min=-100.0, pe_max=200.0, pb_max=30.0,
                dividend_yield_min=0.0, icr_min=0.0)
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)
st.session_state.setdefault("active_preset", "Custom")

st.sidebar.markdown("### Presets")
button_cols = st.sidebar.columns(2)
for index, (preset_name, values) in enumerate(PRESETS.items()):
    if button_cols[index % 2].button(preset_name, width="stretch", key=f"preset_{preset_name}"):
        for key, value in values.items():
            st.session_state[key] = value
        st.session_state["active_preset"] = preset_name
        st.rerun()
if st.sidebar.button("Reset", width="stretch"):
    for key, value in DEFAULTS.items():
        st.session_state[key] = value
    st.session_state["active_preset"] = "Custom"
    st.rerun()

st.sidebar.caption(f"Active preset: {st.session_state['active_preset']}")
st.sidebar.markdown("### Metric Filters")
roe_min = st.sidebar.slider("ROE minimum (%)", -100.0, 200.0, key="roe_min")
de_max = st.sidebar.slider("D/E maximum", 0.0, 20.0, key="de_max")
fcf_min = st.sidebar.slider("FCF minimum (₹ Cr)", -200000.0, 200000.0, step=1000.0, key="fcf_min")
revenue_cagr_min = st.sidebar.slider("Revenue CAGR 5yr minimum (%)", -100.0, 200.0, key="revenue_cagr_min")
pat_cagr_min = st.sidebar.slider("PAT CAGR 5yr minimum (%)", -100.0, 200.0, key="pat_cagr_min")
opm_min = st.sidebar.slider("OPM minimum (%)", -100.0, 100.0, key="opm_min")
pe_max = st.sidebar.slider("P/E maximum", 0.0, 200.0, key="pe_max")
pb_max = st.sidebar.slider("P/B maximum", 0.0, 30.0, key="pb_max")
dividend_yield_min = st.sidebar.slider("Dividend Yield minimum (%)", 0.0, 10.0, key="dividend_yield_min")
icr_min = st.sidebar.slider("ICR minimum", 0.0, 100.0, key="icr_min")

numeric_columns = [
    "return_on_equity_pct", "debt_to_equity", "free_cash_flow_cr",
    "revenue_cagr_5yr", "pat_cagr_5yr", "operating_profit_margin_pct",
    "pe_ratio", "pb_ratio", "dividend_yield_pct", "interest_coverage",
]
for column in numeric_columns:
    data[column] = pd.to_numeric(data[column], errors="coerce")

mask = pd.Series(True, index=data.index)
mask &= data["return_on_equity_pct"].notna() & (data["return_on_equity_pct"] >= roe_min)

if st.session_state["active_preset"] == "Debt-Free":
    mask &= data["debt_to_equity"].notna() & np.isclose(data["debt_to_equity"], 0.0)
else:
    financials = data["broad_sector"].eq("Financials")
    mask &= financials | (data["debt_to_equity"].notna() & (data["debt_to_equity"] <= de_max))

mask &= data["free_cash_flow_cr"].notna() & (data["free_cash_flow_cr"] >= fcf_min)
mask &= data["revenue_cagr_5yr"].notna() & (data["revenue_cagr_5yr"] >= revenue_cagr_min)
mask &= data["pat_cagr_5yr"].notna() & (data["pat_cagr_5yr"] >= pat_cagr_min)
mask &= data["operating_profit_margin_pct"].notna() & (data["operating_profit_margin_pct"] >= opm_min)
mask &= data["pe_ratio"].notna() & (data["pe_ratio"] <= pe_max)
mask &= data["pb_ratio"].notna() & (data["pb_ratio"] <= pb_max)
mask &= data["dividend_yield_pct"].notna() & (data["dividend_yield_pct"] >= dividend_yield_min)

debt_free_icr = data["debt_to_equity"].fillna(np.inf).eq(0) | data["icr_label"].eq("Debt Free")
mask &= debt_free_icr | (data["interest_coverage"].notna() & (data["interest_coverage"] >= icr_min))

results = data.loc[mask].copy()
results["composite_quality_score"] = pd.to_numeric(results["composite_quality_score"], errors="coerce")
results = results.sort_values("composite_quality_score", ascending=False, na_position="last")

st.subheader(f"{len(results)} companies match your filters")
visible = results[
    [
        "company_id", "company_name", "broad_sector", "composite_quality_score",
        "return_on_equity_pct", "debt_to_equity", "free_cash_flow_cr",
        "revenue_cagr_5yr", "pat_cagr_5yr", "operating_profit_margin_pct",
        "pe_ratio", "pb_ratio", "dividend_yield_pct", "interest_coverage",
    ]
].rename(
    columns={
        "company_id": "Ticker", "company_name": "Company", "broad_sector": "Sector",
        "composite_quality_score": "Composite Score", "return_on_equity_pct": "ROE %",
        "debt_to_equity": "D/E", "free_cash_flow_cr": "FCF ₹ Cr",
        "revenue_cagr_5yr": "Revenue CAGR 5yr %", "pat_cagr_5yr": "PAT CAGR 5yr %",
        "operating_profit_margin_pct": "OPM %", "pe_ratio": "P/E", "pb_ratio": "P/B",
        "dividend_yield_pct": "Dividend Yield %", "interest_coverage": "ICR",
    }
)

if visible.empty:
    st.warning("No companies match the selected filters.")
else:
    st.dataframe(visible, width="stretch", hide_index=True, height=520)

csv_data = visible.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "Download visible results as CSV",
    data=csv_data,
    file_name="nifty100_screener_results.csv",
    mime="text/csv",
    disabled=visible.empty,
)
