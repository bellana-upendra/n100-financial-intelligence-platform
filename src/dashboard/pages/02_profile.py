from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.dashboard.utils.db import (
    get_cf,
    get_companies,
    get_company,
    get_pl,
    get_pros_cons,
    get_ratios,
)
from src.dashboard.utils.formatting import clean_year, format_metric, latest_value

started = time.perf_counter()
st.title("Company Profile")
st.caption("Company overview, financial KPIs, ten-year performance and qualitative insights")

companies = get_companies()
labels = {
    row.company_id: f"{row.company_name} — {row.company_id}"
    for row in companies.itertuples()
}
tickers = companies["company_id"].tolist()
selected = st.selectbox(
    "Search company name or ticker",
    options=tickers,
    format_func=lambda ticker: labels.get(ticker, ticker),
    key="profile_company",
)

company_df = get_company(selected)
if company_df.empty:
    st.warning("Ticker not found — please try another.")
    st.stop()
company = company_df.iloc[0]

ratios = clean_year(get_ratios(selected))
pl = clean_year(get_pl(selected))
cf = clean_year(get_cf(selected))
pros_cons = get_pros_cons(selected)

header_left, header_right = st.columns([1, 5])
with header_left:
    logo = company.get("company_logo")
    if isinstance(logo, str) and logo.strip():
        st.image(logo, width=110)
with header_right:
    st.subheader(str(company.get("company_name", selected)))
    st.markdown(
        f"**Ticker:** {selected}  |  **Sector:** {company.get('broad_sector') or 'N/A'}  "
        f"|  **Sub-sector:** {company.get('sub_sector') or 'N/A'}"
    )
    description = company.get("about_company")
    st.write(description if isinstance(description, str) and description.strip() else "Description unavailable.")
    website = company.get("website")
    if isinstance(website, str) and website.strip():
        st.link_button("Company Website", website)

latest_ratios = ratios.sort_values("year").tail(1)
latest = latest_ratios.iloc[0] if not latest_ratios.empty else pd.Series(dtype=object)

kpis = [
    ("ROE", latest.get("return_on_equity_pct"), "%"),
    ("ROCE", latest.get("return_on_capital_employed_pct"), "%"),
    ("Net Profit Margin", latest.get("net_profit_margin_pct"), "%"),
    ("Debt-to-Equity", latest.get("debt_to_equity"), "x"),
    ("Revenue CAGR 5yr", latest.get("revenue_cagr_5yr"), "%"),
    ("Free Cash Flow", latest.get("free_cash_flow_cr"), " Cr"),
]
cols = st.columns(6)
for col, (label, value, suffix) in zip(cols, kpis):
    col.metric(label, format_metric(value, suffix))

chart_pl = pl.sort_values("year").drop_duplicates("year", keep="last").tail(10)
if chart_pl.empty:
    st.info("Revenue and profit data are unavailable for this company.")
else:
    available_years = chart_pl["year"].nunique()
    if available_years < 10:
        st.info(f"Only {available_years} years of P&L data are available for this company.")
    long_pl = chart_pl.melt(
        id_vars="year",
        value_vars=["sales", "net_profit"],
        var_name="Metric",
        value_name="₹ Crore",
    )
    long_pl["Metric"] = long_pl["Metric"].map(
        {"sales": "Revenue", "net_profit": "Net Profit"}
    )
    fig = px.bar(
        long_pl,
        x="year",
        y="₹ Crore",
        color="Metric",
        barmode="group",
        title="Revenue and Net Profit — Latest 10 Years",
    )
    st.plotly_chart(fig, use_container_width=True)

trend = ratios.sort_values("year").drop_duplicates("year", keep="last").tail(10)
if not trend.empty:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend["year"],
            y=trend["return_on_equity_pct"],
            name="ROE",
            mode="lines+markers",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=trend["year"],
            y=trend["return_on_capital_employed_pct"],
            name="ROCE",
            mode="lines+markers",
            yaxis="y2",
        )
    )
    fig.update_layout(
        title="ROE and ROCE Trend",
        xaxis_title="Financial Year",
        yaxis=dict(title="ROE (%)"),
        yaxis2=dict(title="ROCE (%)", overlaying="y", side="right"),
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("ROE and ROCE history are unavailable.")

pros = [str(v).strip() for v in pros_cons.get("pros", pd.Series(dtype=str)).dropna() if str(v).strip()]
cons = [str(v).strip() for v in pros_cons.get("cons", pd.Series(dtype=str)).dropna() if str(v).strip()]

roe = latest_value(latest_ratios, "return_on_equity_pct")
de = latest_value(latest_ratios, "debt_to_equity")
fcf = latest_value(latest_ratios, "free_cash_flow_cr")
rev_cagr = latest_value(latest_ratios, "revenue_cagr_5yr")
sector = company.get("broad_sector")

if not pros:
    if roe is not None and roe >= 15:
        pros.append("Strong latest-year return on equity")
    if fcf is not None and fcf > 0:
        pros.append("Positive free cash flow in the latest available year")
    if de is not None and de <= 1:
        pros.append("Conservative debt-to-equity level")
    if rev_cagr is not None and rev_cagr >= 10:
        pros.append("Healthy five-year revenue growth")
if not cons:
    if de is not None and de > 2 and sector != "Financials":
        cons.append("Elevated debt-to-equity level")
    if fcf is not None and fcf < 0:
        cons.append("Negative free cash flow in the latest available year")
    if rev_cagr is not None and rev_cagr < 5:
        cons.append("Five-year revenue growth is below 5%")
    if roe is not None and roe < 10:
        cons.append("Latest return on equity is below 10%")

st.subheader("Pros and Cons")
pro_col, con_col = st.columns(2)
with pro_col:
    st.markdown("#### Pros")
    if pros:
        for item in pros:
            st.success(f"✅ {item}")
    else:
        st.info("No positive observations are available.")
with con_col:
    st.markdown("#### Cons")
    if cons:
        for item in cons:
            st.error(f"❌ {item}")
    else:
        st.info("No risk observations are available.")

st.caption(f"Profile loaded in {time.perf_counter() - started:.2f} seconds")
