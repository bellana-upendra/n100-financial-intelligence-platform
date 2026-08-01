"""Cached SQLite query functions used by Streamlit pages."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.config import get_settings


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

SETTINGS = get_settings()
DATABASE_PATH = Path(SETTINGS.database_path)


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _read_query(
    query: str,
    params: tuple[Any, ...] = (),
) -> pd.DataFrame:
    """Run a parameterised SQLite query and return a DataFrame."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DATABASE_PATH}"
        )

    with sqlite3.connect(DATABASE_PATH) as connection:
        return pd.read_sql_query(
            sql=query,
            con=connection,
            params=params,
        )


def _normalise_ticker(ticker: str) -> str:
    """Return a clean uppercase company ticker."""

    return ticker.strip().upper()


# ============================================================
# COMPANY QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_companies() -> pd.DataFrame:
    """Return all companies and classification information."""

    return _read_query(
        """
        SELECT
            company_id,
            company_logo,
            company_name,
            chart_link,
            about_company,
            website,
            nse_profile,
            bse_profile,
            face_value,
            book_value,
            roce_percentage,
            roe_percentage,
            broad_sector,
            sub_sector,
            index_weight_pct,
            market_cap_category
        FROM companies
        ORDER BY company_name
        """
    )


@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_company(ticker: str) -> pd.DataFrame:
    """Return one company using its ticker."""

    return _read_query(
        """
        SELECT
            company_id,
            company_logo,
            company_name,
            chart_link,
            about_company,
            website,
            nse_profile,
            bse_profile,
            face_value,
            book_value,
            roce_percentage,
            roe_percentage,
            broad_sector,
            sub_sector,
            index_weight_pct,
            market_cap_category
        FROM companies
        WHERE UPPER(TRIM(company_id)) = ?
        LIMIT 1
        """,
        (_normalise_ticker(ticker),),
    )


# ============================================================
# FINANCIAL RATIO QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_ratios(
    ticker: str,
    year: int | None = None,
) -> pd.DataFrame:
    """Return financial ratios for a company."""

    company_id = _normalise_ticker(ticker)

    if year is None:
        return _read_query(
            """
            SELECT *
            FROM financial_ratios
            WHERE UPPER(TRIM(company_id)) = ?
            ORDER BY year
            """,
            (company_id,),
        )

    return _read_query(
        """
        SELECT *
        FROM financial_ratios
        WHERE UPPER(TRIM(company_id)) = ?
          AND CAST(year AS INTEGER) = ?
        ORDER BY year
        """,
        (
            company_id,
            year,
        ),
    )


# ============================================================
# PROFIT AND LOSS QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_pl(ticker: str) -> pd.DataFrame:
    """Return profit-and-loss history for a company."""

    return _read_query(
        """
        SELECT *
        FROM profitandloss
        WHERE UPPER(TRIM(company_id)) = ?
        ORDER BY year
        """,
        (_normalise_ticker(ticker),),
    )


# ============================================================
# BALANCE SHEET QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_bs(ticker: str) -> pd.DataFrame:
    """Return balance-sheet history for a company."""

    return _read_query(
        """
        SELECT *
        FROM balancesheet
        WHERE UPPER(TRIM(company_id)) = ?
        ORDER BY year
        """,
        (_normalise_ticker(ticker),),
    )


# ============================================================
# CASH-FLOW QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_cf(ticker: str) -> pd.DataFrame:
    """Return cash-flow history for a company."""

    return _read_query(
        """
        SELECT *
        FROM cashflow
        WHERE UPPER(TRIM(company_id)) = ?
        ORDER BY year
        """,
        (_normalise_ticker(ticker),),
    )


# ============================================================
# PEER GROUP QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_peers(group_name: str) -> pd.DataFrame:
    """Return companies belonging to a peer group."""

    return _read_query(
        """
        SELECT
            pg.*,
            c.company_name,
            c.broad_sector,
            c.sub_sector,
            c.market_cap_category,
            c.index_weight_pct
        FROM peer_groups AS pg
        LEFT JOIN companies AS c
            ON UPPER(TRIM(pg.company_id))
             = UPPER(TRIM(c.company_id))
        WHERE TRIM(pg.peer_group_name) = ?
        ORDER BY c.company_name
        """,
        (group_name.strip(),),
    )


@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_peer_group_names() -> pd.DataFrame:
    """Return all available peer-group names."""

    return _read_query(
        """
        SELECT DISTINCT
            peer_group_name
        FROM peer_groups
        WHERE peer_group_name IS NOT NULL
          AND TRIM(peer_group_name) <> ''
        ORDER BY peer_group_name
        """
    )


# ============================================================
# VALUATION QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_valuation(ticker: str) -> pd.DataFrame:
    """Return market-cap and valuation history for a company."""

    return _read_query(
        """
        SELECT
            m.*,
            c.company_name,
            c.broad_sector,
            c.sub_sector,
            c.market_cap_category,
            c.index_weight_pct
        FROM market_cap AS m
        LEFT JOIN companies AS c
            ON UPPER(TRIM(m.company_id))
             = UPPER(TRIM(c.company_id))
        WHERE UPPER(TRIM(m.company_id)) = ?
        ORDER BY m.year
        """,
        (_normalise_ticker(ticker),),
    )


# ============================================================
# ANNUAL REPORT QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_annual_reports(
    ticker: str | None = None,
    year: int | None = None,
) -> pd.DataFrame:
    """Return available annual-report links."""

    query = """
        SELECT
            UPPER(TRIM(d.company_id)) AS company_id,
            c.company_name,
            CAST(d.year AS INTEGER) AS year,
            TRIM(d.annual_report) AS annual_report
        FROM documents AS d
        INNER JOIN companies AS c
            ON UPPER(TRIM(d.company_id))
             = UPPER(TRIM(c.company_id))
        WHERE d.annual_report IS NOT NULL
          AND TRIM(d.annual_report) <> ''
          AND LOWER(TRIM(d.annual_report)) NOT IN (
              'null',
              'none',
              'nan',
              'na',
              'n/a',
              '-'
          )
    """

    params: list[Any] = []

    if ticker:
        query += """
          AND UPPER(TRIM(d.company_id)) = ?
        """
        params.append(_normalise_ticker(ticker))

    if year is not None:
        query += """
          AND CAST(d.year AS INTEGER) = ?
        """
        params.append(year)

    query += """
        ORDER BY
            c.company_name,
            CAST(d.year AS INTEGER) DESC
    """

    return _read_query(
        query,
        tuple(params),
    )


# ============================================================
# SECTOR QUERIES
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_sector_summary() -> pd.DataFrame:
    """Return company counts and total weights by broad sector."""

    return _read_query(
        """
        SELECT
            broad_sector,
            COUNT(*) AS company_count,
            SUM(COALESCE(index_weight_pct, 0)) AS total_index_weight_pct
        FROM companies
        WHERE broad_sector IS NOT NULL
          AND TRIM(broad_sector) <> ''
        GROUP BY broad_sector
        ORDER BY total_index_weight_pct DESC
        """
    )


@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_sector_companies(
    broad_sector: str,
) -> pd.DataFrame:
    """Return companies belonging to a broad sector."""

    return _read_query(
        """
        SELECT
            company_id,
            company_name,
            broad_sector,
            sub_sector,
            index_weight_pct,
            market_cap_category,
            roce_percentage,
            roe_percentage
        FROM companies
        WHERE TRIM(broad_sector) = ?
        ORDER BY index_weight_pct DESC, company_name
        """,
        (broad_sector.strip(),),
    )


# ============================================================
# CACHE CONTROL
# ============================================================

def clear_dashboard_cache() -> None:
    """Clear cached dashboard query results."""

    st.cache_data.clear()