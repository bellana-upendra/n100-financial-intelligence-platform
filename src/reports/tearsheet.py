"""Sprint 5 Day 33: two-page company tearsheet PDF.

Run from the project root:

    python -m src.reports.tearsheet --ticker TCS

Generate the five required test companies:

    python -m src.reports.tearsheet --test-five

Default PDF location:

    reports/tearsheets/<TICKER>_tearsheet.pdf

The report is created with a fixed two-page ReportLab canvas, so content
cannot accidentally flow onto a third page. Long pros and cons are rendered
with Paragraph objects inside fixed-width tables.
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import sqlite3
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from src.config import get_settings


# =============================================================================
# PATHS AND CONSTANTS
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = get_settings()


def resolve_project_path(value: object) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


DATABASE_PATH = resolve_project_path(SETTINGS.database_path)
OUTPUT_DIR = resolve_project_path(SETTINGS.output_dir)

REPORTS_DIR = PROJECT_ROOT / "reports"
TEARSHEET_DIR = REPORTS_DIR / "tearsheets"
TEMP_CHART_DIR = OUTPUT_DIR / "temp_charts"
SKIPPED_TEARSHEETS_PATH = OUTPUT_DIR / "skipped_tearsheets.csv"

MINIMUM_HISTORY_YEARS = 3
MINIMUM_PDF_BYTES = 30 * 1024

CASHFLOW_INTELLIGENCE_PATH = OUTPUT_DIR / "cashflow_intelligence.xlsx"
PROS_CONS_PATH = OUTPUT_DIR / "pros_cons_generated.csv"
CAPITAL_ALLOCATION_PATH = OUTPUT_DIR / "capital_allocation.csv"

TEST_TICKERS = (
    "TCS",
    "HDFCBANK",
    "RELIANCE",
    "SUNPHARMA",
    "TATASTEEL",
)

PAGE_WIDTH, PAGE_HEIGHT = A4

NAVY = colors.HexColor("#0B1F3A")
NAVY_2 = colors.HexColor("#173B63")
BLUE = colors.HexColor("#2F6B9A")
LIGHT_BLUE = colors.HexColor("#EAF2F8")
PALE_BLUE = colors.HexColor("#F5F8FC")
TEXT_DARK = colors.HexColor("#17202A")
TEXT_MUTED = colors.HexColor("#5D6D7E")
BORDER = colors.HexColor("#D5DDE5")
PRO_BG = colors.HexColor("#EAF7EF")
PRO_BORDER = colors.HexColor("#7ABF8E")
CON_BG = colors.HexColor("#FDEEEE")
CON_BORDER = colors.HexColor("#D98C8C")
WHITE = colors.white

CHART_NAVY = "#173B63"
CHART_BLUE = "#4B86B4"
CHART_GREEN = "#2E8B57"
CHART_RED = "#C0504D"
CHART_GOLD = "#C89B3C"
CHART_GREY = "#8B95A1"
CHART_LIGHT = "#D7E4F0"


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass(frozen=True)
class CompanyProfile:
    company_id: str
    company_name: str
    sector: str
    sub_sector: str


@dataclass(frozen=True)
class TearsheetData:
    company: CompanyProfile
    history: pd.DataFrame
    kpis: dict[str, object]
    pros: list[dict[str, object]]
    cons: list[dict[str, object]]
    capital_allocation_label: str


# =============================================================================
# GENERAL HELPERS
# =============================================================================


def normalise_column_name(value: object) -> str:
    """Convert a source column name to lowercase snake_case."""

    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return cleaned.strip("_")


def normalise_company_id(value: object) -> str:
    """Strip whitespace and uppercase a company identifier."""

    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def normalise_financial_year(value: object) -> int | None:
    """Convert common financial-year labels to a four-digit year."""

    if pd.isna(value):
        return None

    if isinstance(value, (int, float)):
        numeric = int(value)
        if 1900 <= numeric <= 2100:
            return numeric

    text = str(value).strip()

    four_digit = re.search(r"(?:19|20)\d{2}", text)
    if four_digit:
        return int(four_digit.group(0))

    two_digit = re.search(r"(?<!\d)(\d{2})(?!\d)", text)
    if two_digit:
        year = int(two_digit.group(1))
        return 2000 + year if year <= 79 else 1900 + year

    return None


def first_existing_column(
    columns: Iterable[str],
    candidates: Iterable[str],
) -> str | None:
    """Return the first candidate column present."""

    available = set(columns)

    for candidate in candidates:
        if candidate in available:
            return candidate

    return None


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    """Return True when the requested SQLite table exists."""

    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def read_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> pd.DataFrame:
    """Read a table and normalise its column names."""

    if not table_exists(connection, table_name):
        return pd.DataFrame()

    frame = pd.read_sql_query(
        f'SELECT * FROM "{table_name}"',
        connection,
    )

    frame.columns = [
        normalise_column_name(column)
        for column in frame.columns
    ]

    return frame


def clean_nullable_text(series: pd.Series) -> pd.Series:
    """Trim text and convert common null-like strings to missing."""

    cleaned = series.astype("string").str.strip()

    return cleaned.replace(
        {
            "": pd.NA,
            "nan": pd.NA,
            "NaN": pd.NA,
            "None": pd.NA,
            "<NA>": pd.NA,
        }
    )


def numeric_series(
    frame: pd.DataFrame,
    column: str,
) -> pd.Series:
    """Return a numeric series or an all-NaN fallback."""

    if column not in frame.columns:
        return pd.Series(
            np.nan,
            index=frame.index,
            dtype="float64",
        )

    return pd.to_numeric(
        frame[column],
        errors="coerce",
    )


def prepare_time_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise company/year and keep one row per company-year."""

    if frame.empty:
        return frame.copy()

    company_column = first_existing_column(
        frame.columns,
        (
            "company_id",
            "ticker",
            "symbol",
            "company",
        ),
    )
    year_column = first_existing_column(
        frame.columns,
        (
            "financial_year",
            "year",
            "fy",
            "report_year",
        ),
    )

    if company_column is None or year_column is None:
        return pd.DataFrame()

    result = frame.copy()
    result["company_id"] = result[company_column].map(
        normalise_company_id
    )
    result["financial_year"] = result[year_column].map(
        normalise_financial_year
    )

    result = result[
        (result["company_id"] != "")
        & result["financial_year"].notna()
    ].copy()

    result["financial_year"] = result["financial_year"].astype(int)
    result["_source_order"] = range(len(result))

    sort_columns = [
        "company_id",
        "financial_year",
    ]

    if "id" in result.columns:
        sort_columns.append("id")

    sort_columns.append("_source_order")

    result = result.sort_values(
        sort_columns,
        kind="stable",
    )

    result = result.drop_duplicates(
        [
            "company_id",
            "financial_year",
        ],
        keep="last",
    )

    return result.drop(
        columns=["_source_order"],
        errors="ignore",
    )


def select_metrics(
    frame: pd.DataFrame,
    mapping: dict[str, Sequence[str]],
) -> pd.DataFrame:
    """Select and rename metrics from a time-series table."""

    prepared = prepare_time_table(frame)

    columns = [
        "company_id",
        "financial_year",
        *mapping.keys(),
    ]

    if prepared.empty:
        return pd.DataFrame(columns=columns)

    result = prepared[
        [
            "company_id",
            "financial_year",
        ]
    ].copy()

    for target, candidates in mapping.items():
        source = first_existing_column(
            prepared.columns,
            candidates,
        )

        result[target] = (
            prepared[source]
            if source is not None
            else pd.NA
        )

    return result


def latest_numeric(
    frame: pd.DataFrame,
    column: str,
) -> float | None:
    """Return the latest available numeric value."""

    if column not in frame.columns or frame.empty:
        return None

    values = frame[
        [
            "financial_year",
            column,
        ]
    ].copy()

    values[column] = pd.to_numeric(
        values[column],
        errors="coerce",
    )
    values = values.dropna(subset=[column])

    if values.empty:
        return None

    values = values.sort_values(
        "financial_year",
        kind="stable",
    )

    return float(values.iloc[-1][column])


def safe_divide(
    numerator: object,
    denominator: object,
    multiplier: float = 1.0,
) -> float | None:
    """Divide safely and return None for zero/missing denominator."""

    numerator_value = pd.to_numeric(
        pd.Series([numerator]),
        errors="coerce",
    ).iloc[0]
    denominator_value = pd.to_numeric(
        pd.Series([denominator]),
        errors="coerce",
    ).iloc[0]

    if pd.isna(numerator_value) or pd.isna(denominator_value):
        return None

    denominator_float = float(denominator_value)

    if abs(denominator_float) < 1e-12:
        return None

    return (
        float(numerator_value)
        / denominator_float
        * multiplier
    )


def calculate_exact_cagr(
    frame: pd.DataFrame,
    value_column: str,
    years: int = 5,
) -> float | None:
    """Calculate exact-period CAGR when start/end values are positive."""

    if frame.empty or value_column not in frame.columns:
        return None

    values = frame[
        [
            "financial_year",
            value_column,
        ]
    ].copy()

    values[value_column] = pd.to_numeric(
        values[value_column],
        errors="coerce",
    )
    values = values.dropna(subset=[value_column])
    values = values.sort_values(
        "financial_year",
        kind="stable",
    )

    if values.empty:
        return None

    latest = values.iloc[-1]
    end_year = int(latest["financial_year"])
    start_rows = values[
        values["financial_year"] == end_year - years
    ]

    if start_rows.empty:
        return None

    start_value = float(start_rows.iloc[-1][value_column])
    end_value = float(latest[value_column])

    if start_value <= 0.0 or end_value <= 0.0:
        return None

    return (
        (end_value / start_value) ** (1.0 / years)
        - 1.0
    ) * 100.0


def truncate_text(
    value: object,
    maximum: int = 190,
) -> str:
    """Trim whitespace and limit very long pros/cons safely."""

    text = re.sub(
        r"\s+",
        " ",
        str(value or "").strip(),
    )

    if len(text) <= maximum:
        return text

    shortened = text[: maximum - 3].rsplit(" ", 1)[0]
    return shortened.rstrip(" ,.;:") + "..."


def format_percent(
    value: object,
    decimals: int = 1,
) -> str:
    """Format a percentage value or show N/A."""

    numeric = pd.to_numeric(
        pd.Series([value]),
        errors="coerce",
    ).iloc[0]

    if pd.isna(numeric):
        return "N/A"

    return f"{float(numeric):,.{decimals}f}%"


def format_ratio(
    value: object,
    decimals: int = 2,
) -> str:
    """Format a ratio value with x suffix."""

    numeric = pd.to_numeric(
        pd.Series([value]),
        errors="coerce",
    ).iloc[0]

    if pd.isna(numeric):
        return "N/A"

    return f"{float(numeric):,.{decimals}f}x"


def axis_crore_formatter(value: float, _: int) -> str:
    """Compact chart tick formatter for crore values."""

    absolute = abs(value)

    if absolute >= 100000:
        return f"{value / 100000:.1f}L"
    if absolute >= 1000:
        return f"{value / 1000:.0f}K"
    return f"{value:.0f}"


def sanitise_filename(value: str) -> str:
    """Convert a ticker into a safe filename component."""

    cleaned = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        value.strip().upper(),
    )
    return cleaned.strip("_") or "company"


# =============================================================================
# COMPANY AND FINANCIAL DATA LOADING
# =============================================================================


def load_sector_fallback() -> pd.DataFrame:
    """Load sector data from the original workbook when database fields are blank."""

    raw_dir = PROJECT_ROOT / "data" / "raw"
    candidates = [
        raw_dir / "sectors.xlsx",
        raw_dir / "companies.xlsx",
    ]

    if raw_dir.exists():
        candidates.extend(
            sorted(raw_dir.glob("*sector*.xlsx"))
        )

    seen: set[Path] = set()

    for path in candidates:
        if not path.exists():
            continue

        resolved = path.resolve()

        if resolved in seen:
            continue

        seen.add(resolved)

        for header_row in (0, 1):
            try:
                frame = pd.read_excel(
                    path,
                    header=header_row,
                )
            except Exception:
                continue

            frame.columns = [
                normalise_column_name(column)
                for column in frame.columns
            ]

            company_column = first_existing_column(
                frame.columns,
                (
                    "company_id",
                    "ticker",
                    "symbol",
                ),
            )
            sector_column = first_existing_column(
                frame.columns,
                (
                    "broad_sector",
                    "sector",
                    "sector_name",
                ),
            )
            sub_sector_column = first_existing_column(
                frame.columns,
                (
                    "sub_sector",
                    "subsector",
                    "industry",
                ),
            )

            if company_column is None or (
                sector_column is None
                and sub_sector_column is None
            ):
                continue

            result = pd.DataFrame(
                {
                    "company_id": frame[company_column].map(
                        normalise_company_id
                    ),
                    "fallback_sector": (
                        clean_nullable_text(
                            frame[sector_column]
                        )
                        if sector_column is not None
                        else pd.Series(
                            pd.NA,
                            index=frame.index,
                            dtype="string",
                        )
                    ),
                    "fallback_sub_sector": (
                        clean_nullable_text(
                            frame[sub_sector_column]
                        )
                        if sub_sector_column is not None
                        else pd.Series(
                            pd.NA,
                            index=frame.index,
                            dtype="string",
                        )
                    ),
                }
            )

            result = result[
                result["company_id"] != ""
            ]

            if not result.empty:
                return result.drop_duplicates(
                    "company_id",
                    keep="last",
                )

    return pd.DataFrame(
        columns=[
            "company_id",
            "fallback_sector",
            "fallback_sub_sector",
        ]
    )


def load_company_profile(
    connection: sqlite3.Connection,
    ticker: str,
) -> CompanyProfile:
    """Load one company and recover its sector when required."""

    companies = read_table(
        connection,
        "companies",
    )

    if companies.empty:
        raise RuntimeError(
            "The companies table is missing or empty."
        )

    company_column = first_existing_column(
        companies.columns,
        (
            "company_id",
            "ticker",
            "symbol",
        ),
    )
    name_column = first_existing_column(
        companies.columns,
        (
            "company_name",
            "name",
            "company",
        ),
    )
    sector_column = first_existing_column(
        companies.columns,
        (
            "broad_sector",
            "sector",
            "sector_name",
        ),
    )
    sub_sector_column = first_existing_column(
        companies.columns,
        (
            "sub_sector",
            "subsector",
            "industry",
        ),
    )

    if company_column is None:
        raise RuntimeError(
            "The companies table has no company identifier."
        )

    companies["company_id"] = companies[
        company_column
    ].map(normalise_company_id)

    row_frame = companies[
        companies["company_id"] == ticker
    ].copy()

    if row_frame.empty:
        available = sorted(
            companies["company_id"]
            .dropna()
            .astype(str)
            .unique()
        )

        raise ValueError(
            f"Ticker {ticker!r} was not found. "
            f"Available company count: {len(available)}."
        )

    row = row_frame.iloc[-1]

    name = (
        str(row[name_column]).strip()
        if name_column is not None
        and pd.notna(row[name_column])
        else ticker
    )

    sector = (
        str(row[sector_column]).strip()
        if sector_column is not None
        and pd.notna(row[sector_column])
        and str(row[sector_column]).strip()
        not in {"", "nan", "None", "<NA>"}
        else ""
    )

    sub_sector = (
        str(row[sub_sector_column]).strip()
        if sub_sector_column is not None
        and pd.notna(row[sub_sector_column])
        and str(row[sub_sector_column]).strip()
        not in {"", "nan", "None", "<NA>"}
        else ""
    )

    if not sector or not sub_sector:
        fallback = load_sector_fallback()

        fallback_row = fallback[
            fallback["company_id"] == ticker
        ]

        if not fallback_row.empty:
            fallback_value = fallback_row.iloc[-1]

            if not sector and pd.notna(
                fallback_value["fallback_sector"]
            ):
                sector = str(
                    fallback_value["fallback_sector"]
                )

            if not sub_sector and pd.notna(
                fallback_value["fallback_sub_sector"]
            ):
                sub_sector = str(
                    fallback_value["fallback_sub_sector"]
                )

    return CompanyProfile(
        company_id=ticker,
        company_name=name,
        sector=sector or "Unclassified",
        sub_sector=sub_sector or "Unclassified",
    )


def build_financial_history(
    connection: sqlite3.Connection,
    ticker: str,
) -> pd.DataFrame:
    """Build one company-year history for all tearsheet charts and KPIs."""

    pl_raw = read_table(
        connection,
        "profitandloss",
    )
    ratios_raw = read_table(
        connection,
        "financial_ratios",
    )
    balance_raw = read_table(
        connection,
        "balancesheet",
    )
    cashflow_raw = read_table(
        connection,
        "cashflow",
    )

    profit_loss = select_metrics(
        pl_raw,
        {
            "sales": (
                "sales",
                "revenue",
                "total_revenue",
            ),
            "net_profit": (
                "net_profit",
                "profit_after_tax",
                "pat",
            ),
        },
    )

    ratios = select_metrics(
        ratios_raw,
        {
            "revenue_cagr_5yr": (
                "revenue_cagr_5yr",
                "sales_cagr_5yr",
            ),
            "pat_cagr_5yr": (
                "pat_cagr_5yr",
                "profit_cagr_5yr",
            ),
            "roe": (
                "return_on_equity_pct",
                "roe_pct",
                "roe",
            ),
            "roce": (
                "return_on_capital_employed_pct",
                "roce_pct",
                "roce",
            ),
            "debt_equity": (
                "debt_to_equity",
                "debt_equity",
                "de_ratio",
            ),
        },
    )

    balance_sheet = select_metrics(
        balance_raw,
        {
            "equity_share_capital": (
                "equity_share_capital",
                "share_capital",
                "equity",
            ),
            "reserves": (
                "reserves",
                "reserves_and_surplus",
            ),
            "borrowings": (
                "borrowings",
                "total_borrowings",
                "total_debt",
                "debt",
            ),
            "total_assets": (
                "total_assets",
            ),
            "other_liabilities_explicit": (
                "other_liabilities",
                "other_liability",
                "other_non_current_liabilities",
            ),
        },
    )

    cashflow = select_metrics(
        cashflow_raw,
        {
            "cfo": (
                "operating_activity",
                "cash_from_operating_activity",
                "cash_flow_from_operating_activities",
                "cfo",
            ),
            "cfi": (
                "investing_activity",
                "cash_from_investing_activity",
                "cash_flow_from_investing_activities",
                "cfi",
            ),
            "cff": (
                "financing_activity",
                "cash_from_financing_activity",
                "cash_flow_from_financing_activities",
                "cff",
            ),
            "net_cash_flow": (
                "net_cash_flow",
                "net_change_in_cash",
            ),
        },
    )

    frames = [
        profit_loss,
        ratios,
        balance_sheet,
        cashflow,
    ]

    key_frames = [
        frame[
            [
                "company_id",
                "financial_year",
            ]
        ]
        for frame in frames
        if not frame.empty
    ]

    if not key_frames:
        raise RuntimeError(
            f"No financial history was found for {ticker}."
        )

    history = pd.concat(
        key_frames,
        ignore_index=True,
    ).drop_duplicates()

    for frame in frames:
        if frame.empty:
            continue

        value_columns = [
            column
            for column in frame.columns
            if column
            not in {
                "company_id",
                "financial_year",
            }
        ]

        history = history.merge(
            frame[
                [
                    "company_id",
                    "financial_year",
                    *value_columns,
                ]
            ],
            on=[
                "company_id",
                "financial_year",
            ],
            how="left",
        )

    history = history[
        history["company_id"] == ticker
    ].copy()

    if history.empty:
        raise RuntimeError(
            f"No company-year rows were found for {ticker}."
        )

    numeric_columns = [
        "sales",
        "net_profit",
        "revenue_cagr_5yr",
        "pat_cagr_5yr",
        "roe",
        "roce",
        "debt_equity",
        "equity_share_capital",
        "reserves",
        "borrowings",
        "total_assets",
        "other_liabilities_explicit",
        "cfo",
        "cfi",
        "cff",
        "net_cash_flow",
    ]

    for column in numeric_columns:
        if column in history.columns:
            history[column] = pd.to_numeric(
                history[column],
                errors="coerce",
            )

    history["equity"] = history[
        [
            "equity_share_capital",
            "reserves",
        ]
    ].sum(
        axis=1,
        min_count=1,
    )

    derived_other_liabilities = (
        numeric_series(
            history,
            "total_assets",
        )
        - numeric_series(
            history,
            "equity",
        )
        - numeric_series(
            history,
            "borrowings",
        )
    )

    history["other_liabilities"] = numeric_series(
        history,
        "other_liabilities_explicit",
    ).combine_first(
        derived_other_liabilities
    )

    calculated_net_cash = (
        numeric_series(
            history,
            "cfo",
        )
        + numeric_series(
            history,
            "cfi",
        )
        + numeric_series(
            history,
            "cff",
        )
    )

    history["net_cash_flow"] = numeric_series(
        history,
        "net_cash_flow",
    ).combine_first(
        calculated_net_cash
    )

    return history.sort_values(
        "financial_year",
        kind="stable",
    ).reset_index(drop=True)


def load_cashflow_intelligence(
    ticker: str,
) -> dict[str, object]:
    """Load the Day 31/32 company-level cash-flow record."""

    if not CASHFLOW_INTELLIGENCE_PATH.exists():
        return {}

    frame = pd.read_excel(
        CASHFLOW_INTELLIGENCE_PATH
    )

    frame.columns = [
        normalise_column_name(column)
        for column in frame.columns
    ]

    company_column = first_existing_column(
        frame.columns,
        (
            "company_id",
            "ticker",
        ),
    )

    if company_column is None:
        return {}

    frame["company_id"] = frame[
        company_column
    ].map(normalise_company_id)

    company_rows = frame[
        frame["company_id"] == ticker
    ]

    if company_rows.empty:
        return {}

    return company_rows.iloc[-1].to_dict()


def calculate_cfo_quality(
    history: pd.DataFrame,
) -> tuple[float | None, str]:
    """Calculate the latest five-row average CFO/PAT ratio."""

    recent = history.tail(5).copy()

    recent["quality"] = recent.apply(
        lambda row: safe_divide(
            row.get("cfo"),
            row.get("net_profit"),
        ),
        axis=1,
    )

    valid = pd.to_numeric(
        recent["quality"],
        errors="coerce",
    ).dropna()

    if valid.empty:
        return None, "Insufficient Data"

    score = float(valid.mean())

    if score > 1.0:
        label = "High Quality"
    elif score >= 0.5:
        label = "Moderate"
    else:
        label = "Accrual Risk"

    return score, label


def load_pros_cons(
    connection: sqlite3.Connection,
    ticker: str,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
]:
    """Load ranked generated pros and cons, with database fallback."""

    if PROS_CONS_PATH.exists():
        frame = pd.read_csv(PROS_CONS_PATH)
        frame.columns = [
            normalise_column_name(column)
            for column in frame.columns
        ]

        if {
            "company_id",
            "type",
            "text",
        }.issubset(frame.columns):
            frame["company_id"] = frame[
                "company_id"
            ].map(normalise_company_id)

            frame = frame[
                frame["company_id"] == ticker
            ].copy()

            if "confidence_pct" not in frame.columns:
                frame["confidence_pct"] = np.nan

            frame["confidence_pct"] = pd.to_numeric(
                frame["confidence_pct"],
                errors="coerce",
            )

            frame = frame.sort_values(
                [
                    "type",
                    "confidence_pct",
                ],
                ascending=[
                    True,
                    False,
                ],
                kind="stable",
            )

            pros = (
                frame[frame["type"] == "pro"]
                .head(5)
                .to_dict("records")
            )
            cons = (
                frame[frame["type"] == "con"]
                .head(5)
                .to_dict("records")
            )

            if pros or cons:
                return pros, cons

    fallback = read_table(
        connection,
        "prosandcons",
    )

    if fallback.empty or "company_id" not in fallback.columns:
        return [], []

    fallback["company_id"] = fallback[
        "company_id"
    ].map(normalise_company_id)

    fallback = fallback[
        fallback["company_id"] == ticker
    ]

    pros: list[dict[str, object]] = []
    cons: list[dict[str, object]] = []

    for _, row in fallback.iterrows():
        pro_text = row.get("pros")
        con_text = row.get("cons")

        if pd.notna(pro_text) and str(pro_text).strip():
            pros.append(
                {
                    "text": str(pro_text).strip(),
                    "confidence_pct": np.nan,
                }
            )

        if pd.notna(con_text) and str(con_text).strip():
            cons.append(
                {
                    "text": str(con_text).strip(),
                    "confidence_pct": np.nan,
                }
            )

    return pros[:5], cons[:5]


def load_capital_allocation_label(
    ticker: str,
    cashflow_record: dict[str, object],
) -> str:
    """Load latest capital-allocation label from Day 32/Day 31 outputs."""

    for candidate in (
        "capital_allocation_label",
        "pattern_label",
        "capital_allocation_pattern",
    ):
        value = cashflow_record.get(candidate)

        if pd.notna(value) and str(value).strip():
            return str(value).strip()

    if CAPITAL_ALLOCATION_PATH.exists():
        frame = pd.read_csv(
            CAPITAL_ALLOCATION_PATH
        )
        frame.columns = [
            normalise_column_name(column)
            for column in frame.columns
        ]

        company_column = first_existing_column(
            frame.columns,
            (
                "company_id",
                "ticker",
            ),
        )
        year_column = first_existing_column(
            frame.columns,
            (
                "financial_year",
                "year",
            ),
        )
        label_column = first_existing_column(
            frame.columns,
            (
                "capital_allocation_label",
                "pattern_label",
                "pattern",
            ),
        )

        if (
            company_column is not None
            and year_column is not None
            and label_column is not None
        ):
            frame["company_id"] = frame[
                company_column
            ].map(normalise_company_id)
            frame["financial_year"] = frame[
                year_column
            ].map(normalise_financial_year)

            rows = frame[
                frame["company_id"] == ticker
            ].dropna(
                subset=["financial_year"]
            )

            if not rows.empty:
                rows = rows.sort_values(
                    "financial_year",
                    kind="stable",
                )
                value = rows.iloc[-1][label_column]

                if pd.notna(value) and str(value).strip():
                    return str(value).strip()

    return "Insufficient Data"


def assemble_tearsheet_data(
    ticker: str,
) -> TearsheetData:
    """Load all information required for one tearsheet."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"Configured database was not found: {DATABASE_PATH}"
        )

    with sqlite3.connect(
        DATABASE_PATH
    ) as connection:
        company = load_company_profile(
            connection,
            ticker,
        )
        history = build_financial_history(
            connection,
            ticker,
        )
        pros, cons = load_pros_cons(
            connection,
            ticker,
        )

    cashflow_record = load_cashflow_intelligence(
        ticker
    )

    cfo_score = cashflow_record.get(
        "cfo_quality_score"
    )
    cfo_label = cashflow_record.get(
        "cfo_quality_label"
    )

    if pd.isna(cfo_score) or not str(
        cfo_label or ""
    ).strip():
        cfo_score, cfo_label = calculate_cfo_quality(
            history
        )

    revenue_cagr = latest_numeric(
        history,
        "revenue_cagr_5yr",
    )

    if revenue_cagr is None:
        revenue_cagr = calculate_exact_cagr(
            history,
            "sales",
            years=5,
        )

    pat_cagr = latest_numeric(
        history,
        "pat_cagr_5yr",
    )

    if pat_cagr is None:
        pat_cagr = calculate_exact_cagr(
            history,
            "net_profit",
            years=5,
        )

    kpis = {
        "revenue_cagr": revenue_cagr,
        "pat_cagr": pat_cagr,
        "roe": latest_numeric(history, "roe"),
        "roce": latest_numeric(history, "roce"),
        "debt_equity": latest_numeric(
            history,
            "debt_equity",
        ),
        "cfo_quality_score": cfo_score,
        "cfo_quality_label": (
            str(cfo_label)
            if cfo_label is not None
            else "Insufficient Data"
        ),
    }

    allocation = load_capital_allocation_label(
        ticker,
        cashflow_record,
    )

    return TearsheetData(
        company=company,
        history=history,
        kpis=kpis,
        pros=pros,
        cons=cons,
        capital_allocation_label=allocation,
    )


# =============================================================================
# CHART CREATION
# =============================================================================


def configure_chart_axes(ax: plt.Axes) -> None:
    """Apply consistent chart styling."""

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#AAB4BE")
    ax.spines["bottom"].set_color("#AAB4BE")
    ax.tick_params(
        axis="both",
        labelsize=8,
        colors="#38434F",
    )
    ax.grid(
        axis="y",
        linestyle="--",
        alpha=0.25,
        linewidth=0.7,
    )
    ax.set_axisbelow(True)


def save_figure(
    fig: plt.Figure,
    path: Path,
) -> Path:
    """Save and close a matplotlib chart."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fig.savefig(
        path,
        dpi=180,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(fig)

    return path


def no_data_chart(
    title: str,
    path: Path,
    message: str = "Data not available",
) -> Path:
    """Create a readable placeholder chart."""

    fig, ax = plt.subplots(
        figsize=(5.2, 3.0)
    )
    ax.axis("off")
    ax.set_title(
        title,
        fontsize=11,
        fontweight="bold",
        color=CHART_NAVY,
        pad=10,
    )
    ax.text(
        0.5,
        0.5,
        message,
        ha="center",
        va="center",
        fontsize=10,
        color=CHART_GREY,
        transform=ax.transAxes,
    )

    return save_figure(fig, path)


def create_bar_chart(
    history: pd.DataFrame,
    value_column: str,
    title: str,
    color: str,
    path: Path,
) -> Path:
    """Create a latest-ten-year financial bar chart."""

    data = history[
        [
            "financial_year",
            value_column,
        ]
    ].copy()

    data[value_column] = pd.to_numeric(
        data[value_column],
        errors="coerce",
    )
    data = data.dropna(
        subset=[value_column]
    ).tail(10)

    if data.empty:
        return no_data_chart(
            title,
            path,
        )

    fig, ax = plt.subplots(
        figsize=(5.2, 3.0)
    )

    x = np.arange(len(data))
    values = data[value_column].astype(float)

    ax.bar(
        x,
        values,
        color=color,
        width=0.68,
    )
    ax.axhline(
        0,
        color="#68737D",
        linewidth=0.8,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(
        data["financial_year"].astype(int),
        rotation=45,
        ha="right",
    )
    ax.yaxis.set_major_formatter(
        FuncFormatter(axis_crore_formatter)
    )
    ax.set_title(
        title,
        fontsize=11,
        fontweight="bold",
        color=CHART_NAVY,
        pad=9,
    )
    ax.set_ylabel(
        "Rs Cr",
        fontsize=8,
        color="#38434F",
    )

    configure_chart_axes(ax)
    fig.tight_layout()

    return save_figure(fig, path)


def create_roe_roce_chart(
    history: pd.DataFrame,
    path: Path,
) -> Path:
    """Create the historical ROE and ROCE line chart."""

    data = history[
        [
            "financial_year",
            "roe",
            "roce",
        ]
    ].copy()

    data["roe"] = pd.to_numeric(
        data["roe"],
        errors="coerce",
    )
    data["roce"] = pd.to_numeric(
        data["roce"],
        errors="coerce",
    )

    data = data[
        data[["roe", "roce"]].notna().any(axis=1)
    ].tail(10)

    if data.empty:
        return no_data_chart(
            "ROE and ROCE trend",
            path,
        )

    fig, ax = plt.subplots(
        figsize=(10.8, 3.4)
    )

    years = data["financial_year"].astype(int)

    ax.plot(
        years,
        data["roe"],
        marker="o",
        linewidth=2.0,
        markersize=4.5,
        label="ROE",
        color=CHART_BLUE,
    )
    ax.plot(
        years,
        data["roce"],
        marker="o",
        linewidth=2.0,
        markersize=4.5,
        label="ROCE",
        color=CHART_GREEN,
    )

    ax.axhline(
        0,
        color="#68737D",
        linewidth=0.8,
    )
    ax.set_title(
        "ROE and ROCE trend",
        fontsize=11,
        fontweight="bold",
        color=CHART_NAVY,
        pad=9,
    )
    ax.set_ylabel(
        "%",
        fontsize=8,
        color="#38434F",
    )
    ax.set_xticks(years)
    ax.set_xticklabels(
        years,
        rotation=45,
        ha="right",
    )
    ax.legend(
        loc="best",
        frameon=False,
        fontsize=8,
    )

    configure_chart_axes(ax)
    fig.tight_layout()

    return save_figure(fig, path)


def create_balance_sheet_chart(
    history: pd.DataFrame,
    path: Path,
) -> Path:
    """Create a stacked balance-sheet composition chart."""

    data = history[
        [
            "financial_year",
            "equity",
            "borrowings",
            "other_liabilities",
        ]
    ].copy()

    for column in (
        "equity",
        "borrowings",
        "other_liabilities",
    ):
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data[
        data[
            [
                "equity",
                "borrowings",
                "other_liabilities",
            ]
        ].notna().any(axis=1)
    ].tail(6)

    if data.empty:
        return no_data_chart(
            "Balance-sheet composition",
            path,
        )

    data[
        [
            "equity",
            "borrowings",
            "other_liabilities",
        ]
    ] = data[
        [
            "equity",
            "borrowings",
            "other_liabilities",
        ]
    ].fillna(0.0)

    fig, ax = plt.subplots(
        figsize=(5.4, 3.6)
    )

    x = np.arange(len(data))
    equity = data["equity"].astype(float).to_numpy()
    borrowings = data[
        "borrowings"
    ].astype(float).to_numpy()
    other = data[
        "other_liabilities"
    ].astype(float).to_numpy()

    ax.bar(
        x,
        equity,
        label="Equity",
        color=CHART_BLUE,
        width=0.7,
    )
    ax.bar(
        x,
        borrowings,
        bottom=equity,
        label="Borrowings",
        color=CHART_GOLD,
        width=0.7,
    )
    ax.bar(
        x,
        other,
        bottom=equity + borrowings,
        label="Other liabilities",
        color=CHART_GREY,
        width=0.7,
    )

    ax.set_xticks(x)
    ax.set_xticklabels(
        data["financial_year"].astype(int),
        rotation=45,
        ha="right",
    )
    ax.yaxis.set_major_formatter(
        FuncFormatter(axis_crore_formatter)
    )
    ax.set_ylabel(
        "Rs Cr",
        fontsize=8,
        color="#38434F",
    )
    ax.set_title(
        "Balance-sheet composition",
        fontsize=11,
        fontweight="bold",
        color=CHART_NAVY,
        pad=9,
    )
    ax.legend(
        loc="upper left",
        fontsize=7.2,
        frameon=False,
        ncol=1,
    )

    configure_chart_axes(ax)
    fig.tight_layout()

    return save_figure(fig, path)


def create_cashflow_waterfall(
    history: pd.DataFrame,
    path: Path,
) -> Path:
    """Create a latest-year CFO/CFI/CFF/net-cash waterfall."""

    data = history[
        [
            "financial_year",
            "cfo",
            "cfi",
            "cff",
            "net_cash_flow",
        ]
    ].copy()

    for column in (
        "cfo",
        "cfi",
        "cff",
        "net_cash_flow",
    ):
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data[
        data[
            [
                "cfo",
                "cfi",
                "cff",
                "net_cash_flow",
            ]
        ].notna().any(axis=1)
    ]

    if data.empty:
        return no_data_chart(
            "Cash-flow waterfall",
            path,
        )

    latest = data.sort_values(
        "financial_year",
        kind="stable",
    ).iloc[-1]

    cfo = (
        float(latest["cfo"])
        if pd.notna(latest["cfo"])
        else 0.0
    )
    cfi = (
        float(latest["cfi"])
        if pd.notna(latest["cfi"])
        else 0.0
    )
    cff = (
        float(latest["cff"])
        if pd.notna(latest["cff"])
        else 0.0
    )
    net_cash = (
        float(latest["net_cash_flow"])
        if pd.notna(latest["net_cash_flow"])
        else cfo + cfi + cff
    )

    deltas = [
        cfo,
        cfi,
        cff,
    ]

    bases: list[float] = []
    running = 0.0

    for value in deltas:
        bases.append(
            running if value >= 0 else running + value
        )
        running += value

    labels = [
        "CFO",
        "CFI",
        "CFF",
        "Net Cash",
    ]
    values = [
        cfo,
        cfi,
        cff,
        net_cash,
    ]
    bottoms = [
        *bases,
        0.0 if net_cash >= 0 else net_cash,
    ]
    heights = [
        abs(cfo),
        abs(cfi),
        abs(cff),
        abs(net_cash),
    ]
    bar_colors = [
        CHART_GREEN if value >= 0 else CHART_RED
        for value in deltas
    ] + [CHART_NAVY]

    fig, ax = plt.subplots(
        figsize=(5.4, 3.6)
    )

    x = np.arange(4)

    ax.bar(
        x,
        heights,
        bottom=bottoms,
        color=bar_colors,
        width=0.66,
    )

    cumulative = 0.0

    for index, value in enumerate(deltas):
        cumulative += value

        if index < len(deltas) - 1:
            ax.plot(
                [
                    index + 0.33,
                    index + 1 - 0.33,
                ],
                [
                    cumulative,
                    cumulative,
                ],
                color="#7B8792",
                linewidth=0.8,
            )

    maximum = max(
        [
            abs(value)
            for value in values
        ]
        + [1.0]
    )

    offset = maximum * 0.04

    for index, value in enumerate(values):
        y = (
            bottoms[index]
            + heights[index]
            + offset
            if value >= 0
            else bottoms[index] - offset
        )

        ax.text(
            index,
            y,
            f"{value:,.0f}",
            ha="center",
            va=(
                "bottom"
                if value >= 0
                else "top"
            ),
            fontsize=7.5,
            color="#28333D",
        )

    ax.axhline(
        0,
        color="#68737D",
        linewidth=0.8,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.yaxis.set_major_formatter(
        FuncFormatter(axis_crore_formatter)
    )
    ax.set_ylabel(
        "Rs Cr",
        fontsize=8,
        color="#38434F",
    )
    ax.set_title(
        f"Cash-flow waterfall - FY{int(latest['financial_year'])}",
        fontsize=11,
        fontweight="bold",
        color=CHART_NAVY,
        pad=9,
    )

    configure_chart_axes(ax)
    fig.tight_layout()

    return save_figure(fig, path)


def create_temp_charts(
    data: TearsheetData,
) -> list[Path]:
    """Create all chart images with unique ticker-based filenames."""

    ticker = sanitise_filename(
        data.company.company_id
    )

    TEMP_CHART_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    paths = [
        create_bar_chart(
            data.history,
            "sales",
            "Revenue - latest 10 years",
            CHART_BLUE,
            TEMP_CHART_DIR / f"{ticker}_revenue.png",
        ),
        create_bar_chart(
            data.history,
            "net_profit",
            "Net Profit - latest 10 years",
            CHART_GREEN,
            TEMP_CHART_DIR / f"{ticker}_net_profit.png",
        ),
        create_roe_roce_chart(
            data.history,
            TEMP_CHART_DIR / f"{ticker}_roe_roce.png",
        ),
        create_balance_sheet_chart(
            data.history,
            TEMP_CHART_DIR / f"{ticker}_balance_sheet.png",
        ),
        create_cashflow_waterfall(
            data.history,
            TEMP_CHART_DIR / f"{ticker}_cashflow_waterfall.png",
        ),
    ]

    return paths


# =============================================================================
# REPORTLAB STYLES AND DRAWING HELPERS
# =============================================================================


def build_paragraph_styles() -> dict[str, ParagraphStyle]:
    """Create all paragraph styles used in the fixed layout."""

    styles = getSampleStyleSheet()

    return {
        "tile_label": ParagraphStyle(
            "TileLabel",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.7,
            leading=9,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "tile_value": ParagraphStyle(
            "TileValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=16,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "tile_note": ParagraphStyle(
            "TileNote",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.8,
            leading=8,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "section_title": ParagraphStyle(
            "SectionTitle",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=5,
        ),
        "list_item": ParagraphStyle(
            "ListItem",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.7,
            leading=10,
            textColor=TEXT_DARK,
            alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "badge_label": ParagraphStyle(
            "BadgeLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=TEXT_MUTED,
            alignment=TA_LEFT,
        ),
        "badge_value": ParagraphStyle(
            "BadgeValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=15,
            textColor=NAVY,
            alignment=TA_LEFT,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
        ),
    }


PARAGRAPH_STYLES = build_paragraph_styles()


def draw_paragraph(
    pdf: canvas.Canvas,
    text: str,
    style: ParagraphStyle,
    x: float,
    top_y: float,
    width: float,
    maximum_height: float,
) -> float:
    """Wrap and draw a Paragraph from the supplied top coordinate."""

    paragraph = Paragraph(
        escape(str(text)),
        style,
    )
    _, height = paragraph.wrap(
        width,
        maximum_height,
    )
    paragraph.drawOn(
        pdf,
        x,
        top_y - height,
    )

    return height


def draw_header(
    pdf: canvas.Canvas,
    data: TearsheetData,
    page_number: int,
) -> None:
    """Draw the navy company header."""

    pdf.setFillColor(NAVY)
    pdf.rect(
        0,
        PAGE_HEIGHT - 76,
        PAGE_WIDTH,
        76,
        stroke=0,
        fill=1,
    )

    pdf.setFillColor(WHITE)
    pdf.setFont(
        "Helvetica-Bold",
        20,
    )

    company_name = truncate_text(
        data.company.company_name,
        maximum=55,
    )

    pdf.drawString(
        30,
        PAGE_HEIGHT - 37,
        company_name,
    )

    pdf.setFont(
        "Helvetica",
        8.5,
    )
    pdf.setFillColor(
        colors.HexColor("#D6E3F0")
    )
    pdf.drawString(
        30,
        PAGE_HEIGHT - 56,
        (
            f"{data.company.sector} | "
            f"{data.company.sub_sector}"
        ),
    )

    ticker_text = data.company.company_id
    ticker_width = max(
        62,
        pdf.stringWidth(
            ticker_text,
            "Helvetica-Bold",
            10,
        )
        + 24,
    )

    pdf.setFillColor(NAVY_2)
    pdf.roundRect(
        PAGE_WIDTH - 30 - ticker_width,
        PAGE_HEIGHT - 57,
        ticker_width,
        28,
        7,
        stroke=0,
        fill=1,
    )

    pdf.setFillColor(WHITE)
    pdf.setFont(
        "Helvetica-Bold",
        10,
    )
    pdf.drawCentredString(
        PAGE_WIDTH - 30 - ticker_width / 2,
        PAGE_HEIGHT - 47,
        ticker_text,
    )

    pdf.setFont(
        "Helvetica",
        7,
    )
    pdf.setFillColor(
        colors.HexColor("#D6E3F0")
    )
    pdf.drawRightString(
        PAGE_WIDTH - 30,
        PAGE_HEIGHT - 68,
        f"Company Tearsheet | Page {page_number} of 2",
    )


def draw_footer(
    pdf: canvas.Canvas,
) -> None:
    """Draw a small fixed footer."""

    pdf.setStrokeColor(BORDER)
    pdf.setLineWidth(0.5)
    pdf.line(
        30,
        30,
        PAGE_WIDTH - 30,
        30,
    )

    footer = Paragraph(
        (
            "Figures are sourced from the project database and generated "
            "analytics. Amounts are in Rs crore unless noted."
        ),
        PARAGRAPH_STYLES["footer"],
    )

    _, height = footer.wrap(
        PAGE_WIDTH - 60,
        18,
    )

    footer.drawOn(
        pdf,
        30,
        10 + (16 - height) / 2,
    )


def draw_kpi_tile(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str,
    value: str,
    note: str = "",
) -> None:
    """Draw one fixed-size KPI tile using Paragraphs."""

    pdf.setFillColor(PALE_BLUE)
    pdf.setStrokeColor(BORDER)
    pdf.setLineWidth(0.7)
    pdf.roundRect(
        x,
        y,
        width,
        height,
        7,
        stroke=1,
        fill=1,
    )

    label_p = Paragraph(
        escape(label),
        PARAGRAPH_STYLES["tile_label"],
    )
    value_p = Paragraph(
        escape(value),
        PARAGRAPH_STYLES["tile_value"],
    )
    note_p = Paragraph(
        escape(note),
        PARAGRAPH_STYLES["tile_note"],
    )

    available_width = width - 12

    _, label_h = label_p.wrap(
        available_width,
        14,
    )
    _, value_h = value_p.wrap(
        available_width,
        20,
    )
    _, note_h = note_p.wrap(
        available_width,
        12,
    )

    total_h = label_h + value_h + note_h + 3
    current_y = y + (height + total_h) / 2

    label_p.drawOn(
        pdf,
        x + 6,
        current_y - label_h,
    )
    current_y -= label_h + 1

    value_p.drawOn(
        pdf,
        x + 6,
        current_y - value_h,
    )
    current_y -= value_h + 2

    note_p.drawOn(
        pdf,
        x + 6,
        current_y - note_h,
    )


def draw_image_box(
    pdf: canvas.Canvas,
    image_path: Path,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    """Draw a chart image inside a bordered fixed box."""

    pdf.setFillColor(WHITE)
    pdf.setStrokeColor(BORDER)
    pdf.setLineWidth(0.6)
    pdf.roundRect(
        x,
        y,
        width,
        height,
        5,
        stroke=1,
        fill=1,
    )

    padding = 5
    available_width = width - 2 * padding
    available_height = height - 2 * padding

    image = ImageReader(
        str(image_path)
    )
    image_width, image_height = image.getSize()

    scale = min(
        available_width / image_width,
        available_height / image_height,
    )

    draw_width = image_width * scale
    draw_height = image_height * scale

    draw_x = x + (width - draw_width) / 2
    draw_y = y + (height - draw_height) / 2

    pdf.drawImage(
        image,
        draw_x,
        draw_y,
        width=draw_width,
        height=draw_height,
        preserveAspectRatio=True,
        mask="auto",
    )


def confidence_suffix(
    item: dict[str, object],
) -> str:
    """Format confidence when available."""

    confidence = pd.to_numeric(
        pd.Series(
            [
                item.get(
                    "confidence_pct"
                )
            ]
        ),
        errors="coerce",
    ).iloc[0]

    if pd.isna(confidence):
        return ""

    return f" <font color='#5D6D7E'>({float(confidence):.0f}% confidence)</font>"


def build_list_table(
    items: list[dict[str, object]],
    width: float,
    maximum_height: float,
    background: colors.Color,
    border: colors.Color,
) -> tuple[Table, float]:
    """Build a wrapped pros/cons table guaranteed to fit the panel."""

    limited = items[:5]

    if not limited:
        limited = [
            {
                "text": (
                    "No qualifying automatically generated item "
                    "was available."
                ),
                "confidence_pct": np.nan,
            }
        ]

    for item_count in range(
        len(limited),
        0,
        -1,
    ):
        rows: list[list[Paragraph]] = []

        for index, item in enumerate(
            limited[:item_count],
            start=1,
        ):
            text = truncate_text(
                item.get("text", ""),
                maximum=190,
            )

            paragraph_text = (
                f"<b>{index}.</b> "
                f"{escape(text)}"
                f"{confidence_suffix(item)}"
            )

            rows.append(
                [
                    Paragraph(
                        paragraph_text,
                        PARAGRAPH_STYLES["list_item"],
                    )
                ]
            )

        table = Table(
            rows,
            colWidths=[width],
            hAlign="LEFT",
        )

        table.setStyle(
            TableStyle(
                [
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "TOP",
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        7,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        7,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, -1),
                        background,
                    ),
                    (
                        "BOX",
                        (0, 0),
                        (-1, -1),
                        0.7,
                        border,
                    ),
                    (
                        "INNERGRID",
                        (0, 0),
                        (-1, -1),
                        0.35,
                        border,
                    ),
                ]
            )
        )

        _, table_height = table.wrap(
            width,
            maximum_height,
        )

        if table_height <= maximum_height:
            return table, table_height

    return table, table_height


def draw_list_panel(
    pdf: canvas.Canvas,
    title: str,
    items: list[dict[str, object]],
    x: float,
    y: float,
    width: float,
    height: float,
    background: colors.Color,
    border: colors.Color,
) -> None:
    """Draw a fixed pros or cons panel."""

    title_p = Paragraph(
        escape(title),
        PARAGRAPH_STYLES["section_title"],
    )

    _, title_height = title_p.wrap(
        width,
        20,
    )

    title_p.drawOn(
        pdf,
        x,
        y + height - title_height,
    )

    table_height_available = (
        height - title_height - 7
    )

    table, table_height = build_list_table(
        items,
        width,
        table_height_available,
        background,
        border,
    )

    table.wrapOn(
        pdf,
        width,
        table_height_available,
    )
    table.drawOn(
        pdf,
        x,
        y + table_height_available - table_height,
    )


def draw_allocation_badge(
    pdf: canvas.Canvas,
    label: str,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    """Draw the latest capital-allocation badge."""

    table = Table(
        [
            [
                Paragraph(
                    "LATEST CAPITAL-ALLOCATION PATTERN",
                    PARAGRAPH_STYLES["badge_label"],
                ),
                Paragraph(
                    escape(label),
                    PARAGRAPH_STYLES["badge_value"],
                ),
            ]
        ],
        colWidths=[
            width * 0.40,
            width * 0.60,
        ],
        rowHeights=[height],
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    LIGHT_BLUE,
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.8,
                    BLUE,
                ),
                (
                    "LINEAFTER",
                    (0, 0),
                    (0, 0),
                    0.5,
                    BORDER,
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    10,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    10,
                ),
            ]
        )
    )

    table.wrapOn(
        pdf,
        width,
        height,
    )
    table.drawOn(
        pdf,
        x,
        y,
    )


# =============================================================================
# PDF GENERATION
# =============================================================================


def draw_page_one(
    pdf: canvas.Canvas,
    data: TearsheetData,
    chart_paths: list[Path],
) -> None:
    """Draw page 1: header, KPI tiles, revenue/profit and return charts."""

    draw_header(
        pdf,
        data,
        page_number=1,
    )

    kpis = data.kpis

    tile_width = 165
    tile_height = 53
    horizontal_gap = 17.5
    x_positions = [
        30,
        30 + tile_width + horizontal_gap,
        30 + 2 * (
            tile_width + horizontal_gap
        ),
    ]
    y_positions = [
        690,
        625,
    ]

    tiles = [
        (
            "Revenue CAGR",
            format_percent(
                kpis.get("revenue_cagr")
            ),
            "5-year",
        ),
        (
            "PAT CAGR",
            format_percent(
                kpis.get("pat_cagr")
            ),
            "5-year",
        ),
        (
            "ROE",
            format_percent(
                kpis.get("roe")
            ),
            "latest year",
        ),
        (
            "ROCE",
            format_percent(
                kpis.get("roce")
            ),
            "latest year",
        ),
        (
            "Debt-to-Equity",
            format_ratio(
                kpis.get("debt_equity")
            ),
            "latest year",
        ),
        (
            "CFO Quality",
            str(
                kpis.get(
                    "cfo_quality_label",
                    "N/A",
                )
            ),
            (
                f"5Y score "
                f"{float(kpis['cfo_quality_score']):.2f}x"
                if pd.notna(
                    kpis.get(
                        "cfo_quality_score"
                    )
                )
                else "5-year average"
            ),
        ),
    ]

    for index, tile in enumerate(tiles):
        row = index // 3
        column = index % 3

        draw_kpi_tile(
            pdf,
            x_positions[column],
            y_positions[row],
            tile_width,
            tile_height,
            *tile,
        )

    draw_image_box(
        pdf,
        chart_paths[0],
        30,
        367,
        257,
        235,
    )
    draw_image_box(
        pdf,
        chart_paths[1],
        308,
        367,
        257,
        235,
    )
    draw_image_box(
        pdf,
        chart_paths[2],
        30,
        55,
        PAGE_WIDTH - 60,
        292,
    )

    draw_footer(pdf)


def draw_page_two(
    pdf: canvas.Canvas,
    data: TearsheetData,
    chart_paths: list[Path],
) -> None:
    """Draw page 2: composition, waterfall, pros, cons and allocation."""

    draw_header(
        pdf,
        data,
        page_number=2,
    )

    draw_allocation_badge(
        pdf,
        data.capital_allocation_label,
        30,
        710,
        PAGE_WIDTH - 60,
        43,
    )

    draw_image_box(
        pdf,
        chart_paths[3],
        30,
        455,
        257,
        235,
    )
    draw_image_box(
        pdf,
        chart_paths[4],
        308,
        455,
        257,
        235,
    )

    panel_width = 257
    panel_height = 365

    draw_list_panel(
        pdf,
        "Pros",
        data.pros,
        30,
        55,
        panel_width,
        panel_height,
        PRO_BG,
        PRO_BORDER,
    )

    draw_list_panel(
        pdf,
        "Cons",
        data.cons,
        308,
        55,
        panel_width,
        panel_height,
        CON_BG,
        CON_BORDER,
    )

    draw_footer(pdf)


def validate_pdf_page_count(
    pdf_path: Path,
    expected_pages: int = 2,
) -> int:
    """Validate the generated PDF page count with pypdf when available."""

    try:
        from pypdf import PdfReader
    except ImportError:
        return expected_pages

    reader = PdfReader(
        str(pdf_path)
    )
    page_count = len(reader.pages)

    if page_count != expected_pages:
        raise RuntimeError(
            f"Expected {expected_pages} PDF pages, "
            f"but generated {page_count}."
        )

    return page_count


def generate_tearsheet(
    ticker: str,
    output_path: Path | None = None,
    keep_temp: bool = False,
    prepared_data: TearsheetData | None = None,
) -> Path:
    """Generate and validate one two-page company tearsheet."""

    ticker = normalise_company_id(ticker)

    if not ticker:
        raise ValueError(
            "Ticker cannot be blank."
        )

    data = (
        prepared_data
        if prepared_data is not None
        else assemble_tearsheet_data(ticker)
    )

    if data.company.company_id != ticker:
        raise ValueError(
            "Prepared tearsheet data does not match "
            f"the requested ticker {ticker}."
        )

    TEARSHEET_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    pdf_path = (
        output_path
        if output_path is not None
        else TEARSHEET_DIR
        / f"{sanitise_filename(ticker)}_tearsheet.pdf"
    )

    pdf_path = (
        pdf_path
        if pdf_path.is_absolute()
        else PROJECT_ROOT / pdf_path
    )

    pdf_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    chart_paths = create_temp_charts(
        data
    )
    successful = False

    try:
        pdf = canvas.Canvas(
            str(pdf_path),
            pagesize=A4,
            pageCompression=1,
        )

        pdf.setTitle(
            f"{data.company.company_name} - Company Tearsheet"
        )
        pdf.setAuthor(
            "N100 Financial Intelligence Platform"
        )
        pdf.setSubject(
            "Two-page company financial tearsheet"
        )

        draw_page_one(
            pdf,
            data,
            chart_paths,
        )

        pdf.showPage()

        draw_page_two(
            pdf,
            data,
            chart_paths,
        )

        pdf.save()

        page_count = validate_pdf_page_count(
            pdf_path,
            expected_pages=2,
        )

        pdf_size = pdf_path.stat().st_size

        if pdf_size < MINIMUM_PDF_BYTES:
            raise RuntimeError(
                f"Generated PDF is only {pdf_size:,} bytes; "
                f"minimum required size is {MINIMUM_PDF_BYTES:,} bytes."
            )

        successful = True

        print()
        print("Company tearsheet generated")
        print("=" * 60)
        print(f"Company:                 {data.company.company_name}")
        print(f"Ticker:                  {ticker}")
        print(f"Financial rows:          {len(data.history)}")
        print(f"Pros included:           {min(len(data.pros), 5)}")
        print(f"Cons included:           {min(len(data.cons), 5)}")
        print(f"Capital allocation:      {data.capital_allocation_label}")
        print(f"PDF pages:               {page_count}")
        print(f"PDF size:                {pdf_size:,} bytes")
        print(f"PDF output:              {pdf_path}")

        return pdf_path

    finally:
        if successful and not keep_temp:
            for chart_path in chart_paths:
                try:
                    chart_path.unlink(
                        missing_ok=True
                    )
                except OSError:
                    pass

            try:
                if (
                    TEMP_CHART_DIR.exists()
                    and not any(
                        TEMP_CHART_DIR.iterdir()
                    )
                ):
                    TEMP_CHART_DIR.rmdir()
            except OSError:
                pass



def load_company_directory() -> pd.DataFrame:
    """Load the complete company directory used by the batch generator."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"Configured database was not found: {DATABASE_PATH}"
        )

    with sqlite3.connect(DATABASE_PATH) as connection:
        companies = read_table(
            connection,
            "companies",
        )

    if companies.empty:
        raise RuntimeError(
            "The companies table is missing or empty."
        )

    company_column = first_existing_column(
        companies.columns,
        (
            "company_id",
            "ticker",
            "symbol",
        ),
    )
    name_column = first_existing_column(
        companies.columns,
        (
            "company_name",
            "name",
            "company",
        ),
    )

    if company_column is None:
        raise RuntimeError(
            "The companies table has no company identifier."
        )

    directory = pd.DataFrame(
        {
            "company_id": companies[company_column].map(
                normalise_company_id
            ),
            "company_name": (
                clean_nullable_text(
                    companies[name_column]
                )
                if name_column is not None
                else pd.Series(
                    pd.NA,
                    index=companies.index,
                    dtype="string",
                )
            ),
        }
    )

    directory = directory[
        directory["company_id"] != ""
    ].copy()

    directory["company_name"] = directory[
        "company_name"
    ].fillna(
        directory["company_id"]
    )

    return directory.drop_duplicates(
        "company_id",
        keep="last",
    ).sort_values(
        "company_id",
        kind="stable",
    ).reset_index(drop=True)


def clear_existing_tearsheets() -> int:
    """Remove stale generated tearsheets before a full batch run."""

    TEARSHEET_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    removed = 0

    for pdf_path in TEARSHEET_DIR.glob(
        "*_tearsheet.pdf"
    ):
        pdf_path.unlink()
        removed += 1

    return removed


def generate_all_tearsheets(
    keep_temp: bool = False,
) -> tuple[int, pd.DataFrame]:
    """Generate all eligible company tearsheets and record skipped companies."""

    company_directory = load_company_directory()
    removed = clear_existing_tearsheets()

    skipped_rows: list[dict[str, object]] = []
    failures: list[str] = []
    generated = 0

    print("Company tearsheet batch")
    print("=" * 60)
    print(f"Companies found:          {len(company_directory)}")
    print(f"Stale PDFs removed:       {removed}")
    print(f"Minimum history years:    {MINIMUM_HISTORY_YEARS}")
    print(f"Minimum PDF size:         {MINIMUM_PDF_BYTES:,} bytes")

    for position, company_row in enumerate(
        company_directory.itertuples(index=False),
        start=1,
    ):
        ticker = normalise_company_id(
            company_row.company_id
        )
        company_name = str(
            company_row.company_name
        ).strip() or ticker

        print()
        print(
            f"[{position}/{len(company_directory)}] "
            f"{ticker} - {company_name}"
        )

        try:
            data = assemble_tearsheet_data(
                ticker
            )

            available_years = int(
                data.history["financial_year"]
                .dropna()
                .nunique()
            )

            if available_years < MINIMUM_HISTORY_YEARS:
                skipped_rows.append(
                    {
                        "company_id": ticker,
                        "ticker": ticker,
                        "company_name": company_name,
                        "available_years": available_years,
                        "skip_reason": (
                            "Insufficient financial history: "
                            f"{available_years} year(s) available; "
                            f"minimum is {MINIMUM_HISTORY_YEARS}."
                        ),
                    }
                )

                print(
                    "SKIPPED: insufficient financial history "
                    f"({available_years} year(s))."
                )
                continue

            pdf_path = generate_tearsheet(
                ticker,
                keep_temp=keep_temp,
                prepared_data=data,
            )

            if pdf_path.stat().st_size < MINIMUM_PDF_BYTES:
                raise RuntimeError(
                    "PDF failed the 30 KB minimum-size check."
                )

            generated += 1

        except Exception as exc:
            failures.append(
                f"{ticker}: {exc}"
            )
            print(
                f"ERROR: {ticker}: {exc}"
            )

    skipped = pd.DataFrame(
        skipped_rows,
        columns=[
            "company_id",
            "ticker",
            "company_name",
            "available_years",
            "skip_reason",
        ],
    )

    SKIPPED_TEARSHEETS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    skipped.to_csv(
        SKIPPED_TEARSHEETS_PATH,
        index=False,
    )

    expected_generated = (
        len(company_directory) - len(skipped)
    )
    actual_pdf_count = len(
        list(
            TEARSHEET_DIR.glob(
                "*_tearsheet.pdf"
            )
        )
    )

    print()
    print("Company tearsheet batch summary")
    print("=" * 60)
    print(f"Companies found:          {len(company_directory)}")
    print(f"Generated:                {generated}")
    print(f"Skipped:                  {len(skipped)}")
    print(f"Expected PDFs:            {expected_generated}")
    print(f"Actual PDFs:              {actual_pdf_count}")
    print(f"Skipped output:           {SKIPPED_TEARSHEETS_PATH}")
    print(f"Tearsheet directory:      {TEARSHEET_DIR}")

    if failures:
        raise RuntimeError(
            "One or more company tearsheets failed:\n"
            + "\n".join(failures)
        )

    if generated != expected_generated:
        raise RuntimeError(
            "Generated count does not equal company count minus skips: "
            f"generated={generated}, expected={expected_generated}."
        )

    if actual_pdf_count != expected_generated:
        raise RuntimeError(
            "Tearsheet directory count failed: "
            f"actual={actual_pdf_count}, expected={expected_generated}."
        )

    small_pdfs = [
        path
        for path in TEARSHEET_DIR.glob(
            "*_tearsheet.pdf"
        )
        if path.stat().st_size < MINIMUM_PDF_BYTES
    ]

    if small_pdfs:
        raise RuntimeError(
            "Generated tearsheets below 30 KB:\n"
            + "\n".join(
                f"{path.name}: {path.stat().st_size:,} bytes"
                for path in small_pdfs
            )
        )

    print(
        "Batch validation: PASS - all eligible tearsheets "
        "exist and are at least 30 KB."
    )

    return generated, skipped

def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate a fixed two-page company tearsheet PDF."
        )
    )

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--ticker",
        help="Company ticker, for example TCS.",
    )
    group.add_argument(
        "--test-five",
        action="store_true",
        help=(
            "Generate TCS, HDFCBANK, RELIANCE, "
            "SUNPHARMA and TATASTEEL."
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        help=(
            "Generate all eligible company tearsheets and "
            "write output/skipped_tearsheets.csv."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Optional output PDF path. "
            "Only valid with --ticker."
        ),
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help=(
            "Keep output/temp_charts images "
            "for debugging."
        ),
    )

    return parser.parse_args()


def main() -> None:
    """Command-line entry point."""

    args = parse_arguments()

    if args.all:
        if args.output is not None:
            raise SystemExit(
                "--output cannot be combined with --all."
            )

        generate_all_tearsheets(
            keep_temp=args.keep_temp,
        )
        return

    if args.test_five:
        if args.output is not None:
            raise SystemExit(
                "--output cannot be combined with --test-five."
            )

        failures: list[str] = []

        for ticker in TEST_TICKERS:
            try:
                generate_tearsheet(
                    ticker,
                    keep_temp=args.keep_temp,
                )
            except Exception as exc:
                failures.append(
                    f"{ticker}: {exc}"
                )
                print(
                    f"ERROR generating {ticker}: {exc}"
                )

        if failures:
            raise SystemExit(
                "One or more test tearsheets failed:\n"
                + "\n".join(failures)
            )

        print()
        print(
            "Five-company tearsheet test completed successfully."
        )
        return

    generate_tearsheet(
        args.ticker,
        output_path=args.output,
        keep_temp=args.keep_temp,
    )


if __name__ == "__main__":
    main()
