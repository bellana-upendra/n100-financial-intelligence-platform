"""Cached SQLite data-access layer for all Streamlit screens."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from src.config import get_settings

SETTINGS = get_settings()
DATABASE_PATH = Path(SETTINGS.database_path)


def _read_query(query: str, params: tuple = ()) -> pd.DataFrame:
    """Execute a parameterised query using a fresh SQLite connection."""
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DATABASE_PATH}")
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        return pd.read_sql_query(query, connection, params=params)


@st.cache_data(ttl=600)
def get_companies() -> pd.DataFrame:
    return _read_query(
        """
        SELECT
            c.company_id AS company_id,
            c.company_name,
            c.company_logo,
            c.about_company,
            c.website,
            c.nse_profile,
            c.bse_profile,
            c.face_value,
            c.book_value,
            c.roce_percentage AS source_roce_percentage,
            c.roe_percentage AS source_roe_percentage,
            c.broad_sector,
            c.sub_sector,
            c.index_weight_pct,
            c.market_cap_category
        FROM companies c
        ORDER BY c.company_name
        """
    )


@st.cache_data(ttl=600)
def get_company(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT
            c.company_id AS company_id,
            c.company_name,
            c.company_logo,
            c.about_company,
            c.website,
            c.nse_profile,
            c.bse_profile,
            c.face_value,
            c.book_value,
            c.broad_sector,
            c.sub_sector,
            c.market_cap_category
        FROM companies c
        WHERE c.company_id = ?
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_ratios(ticker: str, year: int | None = None) -> pd.DataFrame:
    ticker = ticker.strip().upper()
    if year is None:
        return _read_query(
            """
            SELECT *
            FROM financial_ratios
            WHERE company_id = ?
            ORDER BY CAST(year AS INTEGER)
            """,
            (ticker,),
        )
    return _read_query(
        """
        SELECT *
        FROM financial_ratios
        WHERE company_id = ? AND CAST(year AS INTEGER) = ?
        ORDER BY id
        """,
        (ticker, int(year)),
    )


@st.cache_data(ttl=600)
def get_pl(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT * FROM profitandloss
        WHERE company_id = ?
        ORDER BY CAST(year AS INTEGER)
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_bs(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT * FROM balancesheet
        WHERE company_id = ?
        ORDER BY CAST(year AS INTEGER)
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_cf(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT * FROM cashflow
        WHERE company_id = ?
        ORDER BY CAST(year AS INTEGER)
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_sectors() -> pd.DataFrame:
    return _read_query(
        """
        SELECT
	    company_id,
	    broad_sector,
	    sub_sector,
	    index_weight_pct,
	    market_cap_category
	FROM companies
        ORDER BY broad_sector, sub_sector, company_id
        """
    )


@st.cache_data(ttl=600)
def get_peer_group_names() -> list[str]:
    data = _read_query(
        """
        SELECT DISTINCT peer_group_name
        FROM peer_groups
        ORDER BY peer_group_name
        """
    )
    return data["peer_group_name"].dropna().tolist()


@st.cache_data(ttl=600)
def get_peers(group_name: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT pg.*, c.company_name
        FROM peer_groups pg
        LEFT JOIN companies c ON pg.company_id = c.company_id
        WHERE pg.peer_group_name = ?
        ORDER BY pg.is_benchmark DESC, c.company_name
        """,
        (group_name,),
    )


@st.cache_data(ttl=600)
def get_peer_years(group_name: str) -> list[int]:
    data = _read_query(
        """
        SELECT DISTINCT CAST(r.year AS INTEGER) AS year
        FROM financial_ratios r
        JOIN peer_groups pg ON r.company_id = pg.company_id
        WHERE pg.peer_group_name = ?
        ORDER BY year DESC
        """,
        (group_name,),
    )
    return [int(v) for v in data["year"].dropna().tolist()]


@st.cache_data(ttl=600)
def get_peer_group_data(group_name: str, year: int | None = None) -> pd.DataFrame:
    if year is None:
        return _read_query(
            """
            WITH ranked AS (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.company_id
                           ORDER BY CAST(r.year AS INTEGER) DESC, r.id DESC
                       ) AS rn
                FROM financial_ratios r
            )
            SELECT
                pg.peer_group_name,
                pg.company_id,
                pg.is_benchmark,
                c.company_name,
                CAST(ranked.year AS INTEGER) AS financial_year,
                ranked.return_on_equity_pct,
                ranked.return_on_capital_employed_pct,
                ranked.net_profit_margin_pct,
                ranked.debt_to_equity,
                ranked.free_cash_flow_cr,
                ranked.pat_cagr_5yr,
                ranked.revenue_cagr_5yr,
                ranked.eps_cagr_5yr,
                ranked.interest_coverage,
                ranked.asset_turnover,
                ranked.composite_quality_score
            FROM peer_groups pg
            JOIN companies c ON pg.company_id = c.company_id
            LEFT JOIN ranked ON pg.company_id = ranked.company_id AND ranked.rn = 1
            WHERE pg.peer_group_name = ?
            ORDER BY pg.is_benchmark DESC, c.company_name
            """,
            (group_name,),
        )
    return _read_query(
        """
        WITH ratios AS (
            SELECT *
            FROM financial_ratios
            WHERE CAST(year AS INTEGER) = ?
        )
        SELECT
            pg.peer_group_name,
            pg.company_id,
            pg.is_benchmark,
            c.company_name,
            CAST(ratios.year AS INTEGER) AS financial_year,
            ratios.return_on_equity_pct,
            ratios.return_on_capital_employed_pct,
            ratios.net_profit_margin_pct,
            ratios.debt_to_equity,
            ratios.free_cash_flow_cr,
            ratios.pat_cagr_5yr,
            ratios.revenue_cagr_5yr,
            ratios.eps_cagr_5yr,
            ratios.interest_coverage,
            ratios.asset_turnover,
            ratios.composite_quality_score
        FROM peer_groups pg
        JOIN companies c ON pg.company_id = c.company_id
        LEFT JOIN ratios ON pg.company_id = ratios.company_id
        WHERE pg.peer_group_name = ?
        ORDER BY pg.is_benchmark DESC, c.company_name
        """,
        (int(year), group_name),
    )


@st.cache_data(ttl=600)
def get_valuation(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT
            m.*,
            c.company_name,
            c.broad_sector,
            c.sub_sector
        FROM market_cap m
        LEFT JOIN companies c ON m.company_id = c.company_id
        WHERE m.company_id = ?
        ORDER BY CAST(m.year AS INTEGER)
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_home_data(year: int) -> pd.DataFrame:
    return _read_query(
        """
        WITH ratios AS (
            SELECT
                company_id,
                CAST(year AS INTEGER) AS year,
                AVG(return_on_equity_pct) AS return_on_equity_pct,
                AVG(debt_to_equity) AS debt_to_equity,
                AVG(revenue_cagr_5yr) AS revenue_cagr_5yr,
                AVG(composite_quality_score) AS composite_quality_score
            FROM financial_ratios
            WHERE CAST(year AS INTEGER) = ?
            GROUP BY company_id, CAST(year AS INTEGER)
        ), market AS (
            SELECT * FROM market_cap WHERE CAST(year AS INTEGER) = ?
        )
        SELECT
            c.company_id AS company_id,
            c.company_name,
            c.broad_sector,
            c.sub_sector,
            ratios.return_on_equity_pct,
            ratios.debt_to_equity,
            ratios.revenue_cagr_5yr,
            ratios.composite_quality_score,
            market.pe_ratio,
            market.pb_ratio,
            market.dividend_yield_pct,
            market.market_cap_crore
        FROM companies c
        LEFT JOIN ratios ON c.company_id = ratios.company_id
        LEFT JOIN market ON c.company_id = market.company_id
        ORDER BY c.company_name
        """,
        (int(year), int(year)),
    )


@st.cache_data(ttl=600)
def get_screener_data() -> pd.DataFrame:
    return _read_query(
        """
        WITH latest_ratio AS (
            SELECT * FROM (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.company_id
                           ORDER BY CAST(r.year AS INTEGER) DESC, r.id DESC
                       ) AS rn
                FROM financial_ratios r
            ) WHERE rn = 1
        ), latest_market AS (
            SELECT * FROM (
                SELECT m.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.company_id
                           ORDER BY CAST(m.year AS INTEGER) DESC, m.id DESC
                       ) AS rn
                FROM market_cap m
            ) WHERE rn = 1
        )
        SELECT
            c.company_id AS company_id,
            c.company_name,
            c.broad_sector,
            c.sub_sector,
            latest_ratio.year,
            latest_ratio.return_on_equity_pct,
            latest_ratio.return_on_capital_employed_pct,
            latest_ratio.net_profit_margin_pct,
            latest_ratio.debt_to_equity,
            latest_ratio.free_cash_flow_cr,
            latest_ratio.revenue_cagr_5yr,
            latest_ratio.pat_cagr_5yr,
            latest_ratio.operating_profit_margin_pct,
            latest_ratio.interest_coverage,
            latest_ratio.icr_label,
            latest_ratio.composite_quality_score,
            latest_ratio.dividend_payout_ratio_pct,
            latest_ratio.capital_allocation_pattern,
            latest_market.pe_ratio,
            latest_market.pb_ratio,
            latest_market.ev_ebitda,
            latest_market.dividend_yield_pct,
            latest_market.market_cap_crore
        FROM companies c
        LEFT JOIN latest_ratio ON c.company_id = latest_ratio.company_id
        LEFT JOIN latest_market ON c.company_id = latest_market.company_id
        ORDER BY c.company_name
        """
    )


@st.cache_data(ttl=600)
def get_pros_cons(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT pros, cons
        FROM prosandcons
        WHERE company_id = ?
        ORDER BY id
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_documents(ticker: str) -> pd.DataFrame:
    return _read_query(
        """
        SELECT id, company_id, Year AS report_year, Annual_Report AS report_url
        FROM documents
        WHERE company_id = ?
        ORDER BY Year DESC
        """,
        (ticker.strip().upper(),),
    )


@st.cache_data(ttl=600)
def get_sector_names() -> list[str]:
    data = _read_query(
        """
        SELECT DISTINCT broad_sector
        FROM companies
        WHERE broad_sector IS NOT NULL
          AND TRIM(broad_sector) <> ''
        ORDER BY broad_sector
        """
    )
    return data["broad_sector"].tolist()


@st.cache_data(ttl=600)
def get_sector_data(sector: str, year: int) -> pd.DataFrame:
    return _read_query(
        """
        WITH ratios AS (
            SELECT
                company_id,
                CAST(year AS INTEGER) AS year,
                AVG(return_on_equity_pct) AS return_on_equity_pct,
                AVG(return_on_capital_employed_pct) AS return_on_capital_employed_pct,
                AVG(net_profit_margin_pct) AS net_profit_margin_pct,
                AVG(debt_to_equity) AS debt_to_equity,
                AVG(revenue_cagr_5yr) AS revenue_cagr_5yr,
                AVG(pat_cagr_5yr) AS pat_cagr_5yr,
                AVG(free_cash_flow_cr) AS free_cash_flow_cr
            FROM financial_ratios
            WHERE CAST(year AS INTEGER) = ?
            GROUP BY company_id, CAST(year AS INTEGER)
        ), pl AS (
            SELECT company_id, CAST(year AS INTEGER) AS year, AVG(sales) AS sales
            FROM profitandloss
            WHERE CAST(year AS INTEGER) = ?
            GROUP BY company_id, CAST(year AS INTEGER)
        ), market AS (
            SELECT * FROM market_cap WHERE CAST(year AS INTEGER) = ?
        )
        SELECT
            c.company_id AS company_id,
            c.company_name,
            c.broad_sector,
            c.sub_sector,
            pl.sales,
            ratios.return_on_equity_pct,
            ratios.return_on_capital_employed_pct,
            ratios.net_profit_margin_pct,
            ratios.debt_to_equity,
            ratios.revenue_cagr_5yr,
            ratios.pat_cagr_5yr,
            ratios.free_cash_flow_cr,
            market.market_cap_crore,
            market.pe_ratio,
            market.pb_ratio,
            market.ev_ebitda,
            market.dividend_yield_pct
        FROM companies c
        LEFT JOIN ratios ON c.company_id = ratios.company_id
        LEFT JOIN pl ON c.company_id = pl.company_id
        LEFT JOIN market ON c.company_id = market.company_id
        WHERE c.broad_sector = ?
        ORDER BY c.company_name
        """,
        (int(year), int(year), int(year), sector),
    )


@st.cache_data(ttl=600)
def get_capital_allocation_data() -> pd.DataFrame:
    return _read_query(
        """
        WITH latest_ratio AS (
            SELECT * FROM (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.company_id
                           ORDER BY CAST(r.year AS INTEGER) DESC, r.id DESC
                       ) AS rn
                FROM financial_ratios r
            ) WHERE rn = 1
        ), latest_market AS (
            SELECT * FROM (
                SELECT m.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.company_id
                           ORDER BY CAST(m.year AS INTEGER) DESC, m.id DESC
                       ) AS rn
                FROM market_cap m
            ) WHERE rn = 1
        )
        SELECT
            c.company_id AS company_id,
            c.company_name,
            c.broad_sector,
            c.sub_sector,
            latest_ratio.year,
            latest_ratio.cfo_sign,
            latest_ratio.cfi_sign,
            latest_ratio.cff_sign,
            latest_ratio.capital_allocation_pattern,
            latest_ratio.free_cash_flow_cr,
            latest_ratio.cfo_quality_label,
            latest_market.market_cap_crore
        FROM companies c
        LEFT JOIN latest_ratio ON c.company_id = latest_ratio.company_id
        LEFT JOIN latest_market ON c.company_id = latest_market.company_id
        ORDER BY c.company_name
        """
    )
