"""Sprint 5 Day 34: sector-level PDF reports.

Run from the project root:

    python -m src.reports.sector_report --all

Generate one sector:

    python -m src.reports.sector_report --sector IT

Outputs:

    reports/sector/<SECTOR>_report.pdf

Each report contains:
    - sector name and company count
    - median values for eight project KPIs
    - company list
    - eight metrics per company

The eight company metrics are:
    1. Revenue CAGR (5Y)
    2. PAT CAGR (5Y)
    3. ROE
    4. ROCE
    5. Debt-to-Equity
    6. CFO Quality Score
    7. Capex Intensity
    8. FCF Conversion
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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
SECTOR_REPORT_DIR = REPORTS_DIR / "sector"

CASHFLOW_INTELLIGENCE_PATH = OUTPUT_DIR / "cashflow_intelligence.xlsx"

EXPECTED_SECTOR_COUNT = 11

EXPECTED_SECTORS = {
    "Communication Services",
    "Conglomerates",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Healthcare",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
}

PAGE_SIZE = landscape(A4)
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

NAVY = colors.HexColor("#0B1F3A")
NAVY_2 = colors.HexColor("#173B63")
BLUE = colors.HexColor("#2F6B9A")
LIGHT_BLUE = colors.HexColor("#EAF2F8")
PALE_BLUE = colors.HexColor("#F6F9FC")
TEXT_DARK = colors.HexColor("#17202A")
TEXT_MUTED = colors.HexColor("#5D6D7E")
BORDER = colors.HexColor("#D4DCE4")
WHITE = colors.white

METRIC_DEFINITIONS = (
    (
        "revenue_cagr_5yr",
        "Revenue CAGR",
        (
            "revenue_cagr_5yr",
            "sales_cagr_5yr",
        ),
        "percent",
    ),
    (
        "pat_cagr_5yr",
        "PAT CAGR",
        (
            "pat_cagr_5yr",
            "profit_cagr_5yr",
        ),
        "percent",
    ),
    (
        "roe",
        "ROE",
        (
            "return_on_equity_pct",
            "roe_pct",
            "roe",
        ),
        "percent",
    ),
    (
        "roce",
        "ROCE",
        (
            "return_on_capital_employed_pct",
            "roce_pct",
            "roce",
        ),
        "percent",
    ),
    (
        "debt_equity",
        "Debt-to-Equity",
        (
            "debt_to_equity",
            "debt_equity",
            "de_ratio",
        ),
        "ratio",
    ),
    (
        "cfo_quality_score",
        "CFO Quality",
        (
            "cfo_quality_score",
        ),
        "ratio",
    ),
    (
        "capex_intensity_pct",
        "Capex Intensity",
        (
            "capex_intensity_pct",
        ),
        "percent",
    ),
    (
        "fcf_conversion_pct",
        "FCF Conversion",
        (
            "fcf_conversion_pct",
        ),
        "percent",
    ),
)

METRIC_KEYS = tuple(
    definition[0]
    for definition in METRIC_DEFINITIONS
)


# =============================================================================
# DATA MODEL
# =============================================================================


@dataclass(frozen=True)
class SectorReportData:
    sector_name: str
    companies: pd.DataFrame
    medians: dict[str, float | None]


# =============================================================================
# GENERAL HELPERS
# =============================================================================


def normalise_column_name(value: object) -> str:
    """Convert a source column name to lowercase snake_case."""

    cleaned = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value).strip().lower(),
    )
    return cleaned.strip("_")


def normalise_company_id(value: object) -> str:
    """Strip whitespace and uppercase a company identifier."""

    if pd.isna(value):
        return ""

    return str(value).strip().upper()


def normalise_financial_year(value: object) -> int | None:
    """Convert a common year representation to a four-digit year."""

    if pd.isna(value):
        return None

    if isinstance(value, (int, float)):
        numeric = int(value)

        if 1900 <= numeric <= 2100:
            return numeric

    text = str(value).strip()

    match = re.search(
        r"(?:19|20)\d{2}",
        text,
    )

    if match:
        return int(match.group(0))

    match = re.search(
        r"(?<!\d)(\d{2})(?!\d)",
        text,
    )

    if match:
        year = int(match.group(1))
        return (
            2000 + year
            if year <= 79
            else 1900 + year
        )

    return None


def clean_nullable_text(
    series: pd.Series,
) -> pd.Series:
    """Trim text and convert common null-like strings to missing."""

    cleaned = series.astype(
        "string"
    ).str.strip()

    return cleaned.replace(
        {
            "": pd.NA,
            "nan": pd.NA,
            "NaN": pd.NA,
            "None": pd.NA,
            "<NA>": pd.NA,
        }
    )


def first_existing_column(
    columns: Iterable[str],
    candidates: Iterable[str],
) -> str | None:
    """Return the first candidate present in the available columns."""

    available = set(columns)

    for candidate in candidates:
        if candidate in available:
            return candidate

    return None


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    """Return True when a SQLite table exists."""

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
    """Read a SQLite table and normalise its column names."""

    if not table_exists(
        connection,
        table_name,
    ):
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


def prepare_time_table(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Normalise company/year fields and remove duplicate company-years."""

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

    result["company_id"] = result[
        company_column
    ].map(normalise_company_id)

    result["financial_year"] = result[
        year_column
    ].map(normalise_financial_year)

    result = result[
        (result["company_id"] != "")
        & result["financial_year"].notna()
    ].copy()

    result["financial_year"] = result[
        "financial_year"
    ].astype(int)

    result["_source_order"] = range(
        len(result)
    )

    sort_columns = [
        "company_id",
        "financial_year",
    ]

    if "id" in result.columns:
        sort_columns.append("id")

    sort_columns.append(
        "_source_order"
    )

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


def format_metric(
    value: object,
    metric_type: str,
    decimals: int = 1,
) -> str:
    """Format a KPI value for the PDF."""

    numeric = pd.to_numeric(
        pd.Series([value]),
        errors="coerce",
    ).iloc[0]

    if pd.isna(numeric):
        return "N/A"

    if metric_type == "ratio":
        return f"{float(numeric):,.2f}x"

    return f"{float(numeric):,.{decimals}f}%"


def safe_filename(value: str) -> str:
    """Convert a sector name to a stable Windows-safe filename."""

    text = str(value).strip()

    aliases = {
        "Information Technology": "IT",
        "Information Tech": "IT",
    }

    text = aliases.get(
        text,
        text,
    )

    text = text.replace(
        "&",
        " and ",
    )

    cleaned = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        text,
    ).strip("_")

    return cleaned or "Unclassified"


# =============================================================================
# SECTOR AND KPI DATA LOADING
# =============================================================================


def load_sector_fallback() -> pd.DataFrame:
    """Load broad-sector data from data/raw/sectors.xlsx."""

    raw_dir = PROJECT_ROOT / "data" / "raw"

    candidates = [
        raw_dir / "sectors.xlsx",
    ]

    if raw_dir.exists():
        candidates.extend(
            sorted(
                raw_dir.glob(
                    "*sector*.xlsx"
                )
            )
        )

    seen: set[Path] = set()

    for path in candidates:
        if not path.exists():
            continue

        resolved = path.resolve()

        if resolved in seen:
            continue

        seen.add(resolved)

        for header_row in (
            0,
            1,
        ):
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
                    "industry_name",
                ),
            )

            if (
                company_column is None
                or sector_column is None
            ):
                continue

            result = pd.DataFrame(
                {
                    "company_id": frame[
                        company_column
                    ].map(
                        normalise_company_id
                    ),
                    "fallback_sector": clean_nullable_text(
                        frame[sector_column]
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
            ].copy()

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



def apply_approved_sector_taxonomy(
    directory: pd.DataFrame,
) -> pd.DataFrame:
    """Restore the project-approved 11-sector taxonomy.

    The execution plan defines Conglomerates as a separate macro sector and
    assigns companies whose sub-sector is a conglomerate or holding-company
    classification to it. Some copies of sectors.xlsx retain those sub-sector
    labels but place the rows inside another broad sector. This function
    restores the approved macro classification without changing the workbook.
    """

    result = directory.copy()

    result["sector"] = clean_nullable_text(
        result["sector"]
    )
    result["sub_sector"] = clean_nullable_text(
        result["sub_sector"]
    )

    sub_sector_text = (
        result["sub_sector"]
        .fillna("")
        .astype(str)
        .str.casefold()
    )

    conglomerate_mask = (
        sub_sector_text.str.contains(
            r"conglomerate",
            regex=True,
        )
        | sub_sector_text.str.contains(
            r"holding compan(?:y|ies)",
            regex=True,
        )
        | sub_sector_text.str.contains(
            r"holding cos?",
            regex=True,
        )
    )

    result.loc[
        conglomerate_mask,
        "sector",
    ] = "Conglomerates"

    return result


def load_company_directory(
    connection: sqlite3.Connection,
) -> pd.DataFrame:
    """Load company names and broad sectors for all companies."""

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
            "industry_name",
        ),
    )

    if company_column is None:
        raise RuntimeError(
            "The companies table has no company identifier."
        )

    directory = pd.DataFrame(
        {
            "company_id": companies[
                company_column
            ].map(
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
            "database_sector": (
                clean_nullable_text(
                    companies[sector_column]
                )
                if sector_column is not None
                else pd.Series(
                    pd.NA,
                    index=companies.index,
                    dtype="string",
                )
            ),
            "database_sub_sector": (
                clean_nullable_text(
                    companies[sub_sector_column]
                )
                if sub_sector_column is not None
                else pd.Series(
                    pd.NA,
                    index=companies.index,
                    dtype="string",
                )
            ),
        }
    )

    fallback = load_sector_fallback()

    directory = directory.merge(
        fallback,
        on="company_id",
        how="left",
        validate="one_to_one",
    )

    directory["sector"] = directory[
        "database_sector"
    ].combine_first(
        directory["fallback_sector"]
    )

    directory["sub_sector"] = directory[
        "database_sub_sector"
    ].combine_first(
        directory["fallback_sub_sector"]
    )

    directory = apply_approved_sector_taxonomy(
        directory
    )

    directory["company_name"] = directory[
        "company_name"
    ].fillna(
        directory["company_id"]
    )

    directory = directory[
        directory["company_id"] != ""
    ].drop_duplicates(
        "company_id",
        keep="last",
    )

    missing_sector = directory[
        "sector"
    ].isna()

    if missing_sector.any():
        missing_companies = directory.loc[
            missing_sector,
            [
                "company_id",
                "company_name",
            ],
        ]

        raise RuntimeError(
            "Some companies have no broad-sector mapping:\n"
            + missing_companies.to_string(
                index=False
            )
        )

    directory["sector"] = clean_nullable_text(
        directory["sector"]
    )

    return directory[
        [
            "company_id",
            "company_name",
            "sector",
            "sub_sector",
        ]
    ].sort_values(
        [
            "sector",
            "company_id",
        ],
        kind="stable",
    ).reset_index(drop=True)


def latest_metric_by_company(
    prepared: pd.DataFrame,
    source_column: str,
    target_column: str,
) -> pd.DataFrame:
    """Select the latest non-null metric value for every company."""

    if (
        prepared.empty
        or source_column not in prepared.columns
    ):
        return pd.DataFrame(
            columns=[
                "company_id",
                target_column,
            ]
        )

    metric = prepared[
        [
            "company_id",
            "financial_year",
            source_column,
        ]
    ].copy()

    metric[target_column] = pd.to_numeric(
        metric[source_column],
        errors="coerce",
    )

    metric = metric.dropna(
        subset=[target_column]
    ).sort_values(
        [
            "company_id",
            "financial_year",
        ],
        kind="stable",
    )

    if metric.empty:
        return pd.DataFrame(
            columns=[
                "company_id",
                target_column,
            ]
        )

    return metric.groupby(
        "company_id",
        as_index=False,
        sort=False,
    ).tail(1)[
        [
            "company_id",
            target_column,
        ]
    ]


def load_ratio_metrics(
    connection: sqlite3.Connection,
) -> pd.DataFrame:
    """Load the five latest Ratio Engine KPIs for every company."""

    ratios = prepare_time_table(
        read_table(
            connection,
            "financial_ratios",
        )
    )

    company_ids = pd.DataFrame(
        {
            "company_id": (
                ratios["company_id"].drop_duplicates()
                if not ratios.empty
                else pd.Series(
                    dtype="string"
                )
            )
        }
    )

    result = company_ids.copy()

    for (
        target,
        _,
        candidates,
        _,
    ) in METRIC_DEFINITIONS[:5]:
        source = first_existing_column(
            ratios.columns,
            candidates,
        )

        if source is None:
            result[target] = np.nan
            continue

        latest = latest_metric_by_company(
            ratios,
            source,
            target,
        )

        result = result.merge(
            latest,
            on="company_id",
            how="outer",
            validate="one_to_one",
        )

    return result


def load_cashflow_metrics() -> pd.DataFrame:
    """Load the three latest company-level cash-flow KPIs."""

    columns = [
        "company_id",
        "cfo_quality_score",
        "cfo_quality_label",
        "capex_intensity_pct",
        "fcf_conversion_pct",
    ]

    if not CASHFLOW_INTELLIGENCE_PATH.exists():
        return pd.DataFrame(
            columns=columns
        )

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
            "symbol",
        ),
    )

    if company_column is None:
        raise RuntimeError(
            "cashflow_intelligence.xlsx has no company identifier."
        )

    result = pd.DataFrame(
        {
            "company_id": frame[
                company_column
            ].map(
                normalise_company_id
            ),
            "cfo_quality_score": pd.to_numeric(
                (
                    frame["cfo_quality_score"]
                    if "cfo_quality_score" in frame.columns
                    else pd.Series(
                        np.nan,
                        index=frame.index,
                    )
                ),
                errors="coerce",
            ),
            "cfo_quality_label": (
                clean_nullable_text(
                    frame[
                        "cfo_quality_label"
                    ]
                )
                if "cfo_quality_label" in frame.columns
                else pd.Series(
                    pd.NA,
                    index=frame.index,
                    dtype="string",
                )
            ),
            "capex_intensity_pct": pd.to_numeric(
                (
                    frame["capex_intensity_pct"]
                    if "capex_intensity_pct" in frame.columns
                    else pd.Series(
                        np.nan,
                        index=frame.index,
                    )
                ),
                errors="coerce",
            ),
            "fcf_conversion_pct": pd.to_numeric(
                (
                    frame["fcf_conversion_pct"]
                    if "fcf_conversion_pct" in frame.columns
                    else pd.Series(
                        np.nan,
                        index=frame.index,
                    )
                ),
                errors="coerce",
            ),
        }
    )

    return result[
        result["company_id"] != ""
    ].drop_duplicates(
        "company_id",
        keep="last",
    )


def load_company_metrics() -> pd.DataFrame:
    """Load company directory and all eight report metrics."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"Configured database was not found: {DATABASE_PATH}"
        )

    with sqlite3.connect(
        DATABASE_PATH
    ) as connection:
        directory = load_company_directory(
            connection
        )
        ratio_metrics = load_ratio_metrics(
            connection
        )

    cashflow_metrics = load_cashflow_metrics()

    metrics = directory.merge(
        ratio_metrics,
        on="company_id",
        how="left",
        validate="one_to_one",
    )

    metrics = metrics.merge(
        cashflow_metrics,
        on="company_id",
        how="left",
        validate="one_to_one",
    )

    for metric_key in METRIC_KEYS:
        if metric_key not in metrics.columns:
            metrics[metric_key] = np.nan

        metrics[metric_key] = pd.to_numeric(
            metrics[metric_key],
            errors="coerce",
        )

    return metrics.sort_values(
        [
            "sector",
            "company_name",
            "company_id",
        ],
        kind="stable",
    ).reset_index(drop=True)


def build_sector_data(
    metrics: pd.DataFrame,
    sector_name: str,
) -> SectorReportData:
    """Build one sector's company table and KPI medians."""

    companies = metrics[
        metrics["sector"] == sector_name
    ].copy()

    if companies.empty:
        raise ValueError(
            f"No companies were found for sector {sector_name!r}."
        )

    medians: dict[str, float | None] = {}

    for metric_key in METRIC_KEYS:
        values = pd.to_numeric(
            companies[metric_key],
            errors="coerce",
        ).dropna()

        medians[metric_key] = (
            float(values.median())
            if not values.empty
            else None
        )

    return SectorReportData(
        sector_name=sector_name,
        companies=companies,
        medians=medians,
    )


# =============================================================================
# REPORTLAB STYLES AND LAYOUT
# =============================================================================


def build_styles() -> dict[str, ParagraphStyle]:
    """Create all sector-report paragraph styles."""

    styles = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "SectorTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=23,
            textColor=NAVY,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "SectorSubtitle",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=TEXT_MUTED,
            spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "SectorSection",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=NAVY,
            spaceBefore=4,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "SectorBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=TEXT_DARK,
        ),
        "company": ParagraphStyle(
            "CompanyCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.8,
            leading=8.2,
            textColor=TEXT_DARK,
            alignment=TA_LEFT,
        ),
        "cell": ParagraphStyle(
            "MetricCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.6,
            leading=8,
            textColor=TEXT_DARK,
            alignment=TA_CENTER,
        ),
        "header_cell": ParagraphStyle(
            "MetricHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=6.4,
            leading=7.5,
            textColor=WHITE,
            alignment=TA_CENTER,
        ),
        "median_label": ParagraphStyle(
            "MedianLabel",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=8,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
        ),
        "median_value": ParagraphStyle(
            "MedianValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=13,
            textColor=NAVY,
            alignment=TA_CENTER,
        ),
        "footer": ParagraphStyle(
            "SectorFooter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
        ),
    }


STYLES = build_styles()


def report_header_footer(
    pdf,
    document,
) -> None:
    """Draw the repeating page header and footer."""

    pdf.saveState()

    pdf.setFillColor(NAVY)
    pdf.rect(
        0,
        PAGE_HEIGHT - 18 * mm,
        PAGE_WIDTH,
        18 * mm,
        stroke=0,
        fill=1,
    )

    pdf.setFillColor(WHITE)
    pdf.setFont(
        "Helvetica-Bold",
        10,
    )
    pdf.drawString(
        12 * mm,
        PAGE_HEIGHT - 11.5 * mm,
        "N100 Financial Intelligence Platform",
    )

    pdf.setFont(
        "Helvetica",
        7,
    )
    pdf.drawRightString(
        PAGE_WIDTH - 12 * mm,
        PAGE_HEIGHT - 11.5 * mm,
        f"Sector Report | Page {document.page}",
    )

    pdf.setStrokeColor(BORDER)
    pdf.setLineWidth(0.5)
    pdf.line(
        12 * mm,
        10 * mm,
        PAGE_WIDTH - 12 * mm,
        10 * mm,
    )

    footer = (
        "Medians exclude missing values. Revenue/PAT CAGR are 5-year "
        "metrics; ROE, ROCE and Debt-to-Equity use the latest available "
        "Ratio Engine values."
    )

    pdf.setFillColor(TEXT_MUTED)
    pdf.setFont(
        "Helvetica",
        6.2,
    )
    pdf.drawCentredString(
        PAGE_WIDTH / 2,
        5.8 * mm,
        footer,
    )

    pdf.restoreState()


def median_kpi_table(
    data: SectorReportData,
) -> Table:
    """Create a two-row table containing all eight median KPIs."""

    cells: list[Paragraph] = []

    for (
        metric_key,
        label,
        _,
        metric_type,
    ) in METRIC_DEFINITIONS:
        value = format_metric(
            data.medians.get(metric_key),
            metric_type,
        )

        cells.append(
            Paragraph(
                (
                    f"<font color='#5D6D7E' size='7'>"
                    f"{escape(label)}</font><br/>"
                    f"<font color='#0B1F3A' size='11'><b>"
                    f"{escape(value)}</b></font>"
                ),
                STYLES["median_value"],
            )
        )

    rows = [
        cells[:4],
        cells[4:],
    ]

    usable_width = (
        PAGE_WIDTH - 24 * mm
    )

    table = Table(
        rows,
        colWidths=[
            usable_width / 4
        ] * 4,
        rowHeights=[
            19 * mm,
            19 * mm,
        ],
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
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER",
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    PALE_BLUE,
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.7,
                    BORDER,
                ),
                (
                    "INNERGRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    BORDER,
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
            ]
        )
    )

    return table


def company_metrics_table(
    data: SectorReportData,
) -> Table:
    """Create the repeating company list and eight-metric table."""

    headers = [
        "Company",
        "Ticker",
        "Revenue<br/>CAGR",
        "PAT<br/>CAGR",
        "ROE",
        "ROCE",
        "Debt /<br/>Equity",
        "CFO<br/>Quality",
        "Capex<br/>Intensity",
        "FCF<br/>Conversion",
    ]

    rows: list[list[Paragraph]] = [
        [
            Paragraph(
                header,
                STYLES["header_cell"],
            )
            for header in headers
        ]
    ]

    for company in data.companies.itertuples(
        index=False
    ):
        cfo_value = format_metric(
            getattr(
                company,
                "cfo_quality_score",
                np.nan,
            ),
            "ratio",
        )

        cfo_label = getattr(
            company,
            "cfo_quality_label",
            None,
        )

        if (
            cfo_label is not None
            and pd.notna(cfo_label)
            and str(cfo_label).strip()
        ):
            cfo_value = (
                f"{escape(cfo_value)}<br/>"
                f"<font size='5.7' color='#5D6D7E'>"
                f"{escape(str(cfo_label).strip())}</font>"
            )

        row_values = [
            Paragraph(
                escape(
                    str(company.company_name)
                ),
                STYLES["company"],
            ),
            Paragraph(
                escape(
                    str(company.company_id)
                ),
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "revenue_cagr_5yr",
                        np.nan,
                    ),
                    "percent",
                ),
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "pat_cagr_5yr",
                        np.nan,
                    ),
                    "percent",
                ),
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "roe",
                        np.nan,
                    ),
                    "percent",
                ),
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "roce",
                        np.nan,
                    ),
                    "percent",
                ),
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "debt_equity",
                        np.nan,
                    ),
                    "ratio",
                ),
                STYLES["cell"],
            ),
            Paragraph(
                cfo_value,
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "capex_intensity_pct",
                        np.nan,
                    ),
                    "percent",
                ),
                STYLES["cell"],
            ),
            Paragraph(
                format_metric(
                    getattr(
                        company,
                        "fcf_conversion_pct",
                        np.nan,
                    ),
                    "percent",
                ),
                STYLES["cell"],
            ),
        ]

        rows.append(
            row_values
        )

    usable_width = (
        PAGE_WIDTH - 24 * mm
    )

    column_widths = [
        41 * mm,
        18 * mm,
        23 * mm,
        23 * mm,
        19 * mm,
        19 * mm,
        22 * mm,
        27 * mm,
        27 * mm,
        27 * mm,
    ]

    width_difference = (
        usable_width
        - sum(column_widths)
    )

    column_widths[0] += (
        width_difference
    )

    table = Table(
        rows,
        colWidths=column_widths,
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )

    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    NAVY_2,
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "ALIGN",
                    (1, 1),
                    (-1, -1),
                    "CENTER",
                ),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [
                        WHITE,
                        PALE_BLUE,
                    ],
                ),
                (
                    "BOX",
                    (0, 0),
                    (-1, -1),
                    0.65,
                    BORDER,
                ),
                (
                    "INNERGRID",
                    (0, 0),
                    (-1, -1),
                    0.35,
                    BORDER,
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    3.5,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    3.5,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    4,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    4,
                ),
            ]
        )
    )

    return table


def build_story(
    data: SectorReportData,
) -> list[object]:
    """Build the flowable story for one sector report."""

    company_names = ", ".join(
        data.companies[
            "company_name"
        ].astype(str)
    )

    story: list[object] = [
        Spacer(
            1,
            5 * mm,
        ),
        Paragraph(
            f"{escape(data.sector_name)} Sector Report",
            STYLES["title"],
        ),
        Paragraph(
            (
                f"<b>{len(data.companies)} companies</b> | "
                "Latest available company KPIs and sector medians"
            ),
            STYLES["subtitle"],
        ),
        Paragraph(
            "Median KPIs",
            STYLES["section"],
        ),
        median_kpi_table(
            data
        ),
        Spacer(
            1,
            4 * mm,
        ),
        Paragraph(
            "Company list",
            STYLES["section"],
        ),
        Paragraph(
            escape(
                company_names
            ),
            STYLES["body"],
        ),
        Spacer(
            1,
            4 * mm,
        ),
        Paragraph(
            "Company metrics",
            STYLES["section"],
        ),
        company_metrics_table(
            data
        ),
    ]

    return story


# =============================================================================
# PDF GENERATION AND CLI
# =============================================================================


def validate_sector_pdf(
    pdf_path: Path,
) -> int | None:
    """Validate that the PDF is readable when pypdf is available."""

    if not pdf_path.exists():
        raise RuntimeError(
            f"Sector PDF was not created: {pdf_path}"
        )

    if pdf_path.stat().st_size <= 0:
        raise RuntimeError(
            f"Sector PDF is empty: {pdf_path}"
        )

    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    reader = PdfReader(
        str(pdf_path)
    )
    page_count = len(
        reader.pages
    )

    if page_count < 1:
        raise RuntimeError(
            f"Sector PDF has no pages: {pdf_path}"
        )

    return page_count


def generate_sector_report(
    data: SectorReportData,
    output_path: Path | None = None,
) -> Path:
    """Generate one sector PDF."""

    SECTOR_REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    pdf_path = (
        output_path
        if output_path is not None
        else SECTOR_REPORT_DIR
        / (
            f"{safe_filename(data.sector_name)}"
            "_report.pdf"
        )
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

    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=PAGE_SIZE,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=23 * mm,
        bottomMargin=13 * mm,
        title=(
            f"{data.sector_name} Sector Report"
        ),
        author=(
            "N100 Financial Intelligence Platform"
        ),
        subject=(
            "Sector company metrics and median KPIs"
        ),
    )

    document.build(
        build_story(data),
        onFirstPage=report_header_footer,
        onLaterPages=report_header_footer,
    )

    page_count = validate_sector_pdf(
        pdf_path
    )

    print()
    print("Sector report generated")
    print("=" * 60)
    print(f"Sector:                  {data.sector_name}")
    print(f"Companies:               {len(data.companies)}")
    print(
        "Pages:                   "
        + (
            str(page_count)
            if page_count is not None
            else "not checked (pypdf unavailable)"
        )
    )
    print(f"PDF size:                {pdf_path.stat().st_size:,} bytes")
    print(f"PDF output:              {pdf_path}")

    return pdf_path


def clear_existing_sector_reports() -> int:
    """Remove stale sector reports before a full regeneration."""

    SECTOR_REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    removed = 0

    for path in SECTOR_REPORT_DIR.glob(
        "*_report.pdf"
    ):
        path.unlink()
        removed += 1

    return removed


def generate_all_sector_reports() -> list[Path]:
    """Generate and validate all broad-sector reports."""

    metrics = load_company_metrics()

    sectors = sorted(
        metrics["sector"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    sector_counts = (
        metrics["sector"]
        .value_counts()
        .sort_index()
    )

    actual_sector_set = set(sectors)

    if actual_sector_set != EXPECTED_SECTORS:
        missing_sectors = sorted(
            EXPECTED_SECTORS - actual_sector_set
        )
        unexpected_sectors = sorted(
            actual_sector_set - EXPECTED_SECTORS
        )

        raise RuntimeError(
            "Sector taxonomy does not match the approved 11-sector set. "
            f"Missing={missing_sectors}; "
            f"Unexpected={unexpected_sectors}.\n"
            + sector_counts.to_string()
        )

    if len(sectors) != EXPECTED_SECTOR_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_SECTOR_COUNT} sectors, "
            f"but found {len(sectors)}:\n"
            + sector_counts.to_string()
        )

    conglomerate_count = int(
        sector_counts.get(
            "Conglomerates",
            0,
        )
    )

    if conglomerate_count < 1:
        raise RuntimeError(
            "The approved Conglomerates sector contains no companies."
        )

    removed = clear_existing_sector_reports()

    print("Sector report batch")
    print("=" * 60)
    print(f"Companies found:          {metrics['company_id'].nunique()}")
    print(f"Sectors found:            {len(sectors)}")
    print(f"Stale PDFs removed:       {removed}")
    print()
    print("Approved sector distribution:")
    print(sector_counts.to_string())

    print()
    print("Conglomerates membership:")
    print(
        metrics.loc[
            metrics["sector"] == "Conglomerates",
            [
                "company_id",
                "company_name",
                "sub_sector",
            ],
        ].to_string(index=False)
    )

    generated: list[Path] = []
    failures: list[str] = []

    for position, sector_name in enumerate(
        sectors,
        start=1,
    ):
        print()
        print(
            f"[{position}/{len(sectors)}] "
            f"{sector_name}"
        )

        try:
            sector_data = build_sector_data(
                metrics,
                sector_name,
            )

            generated.append(
                generate_sector_report(
                    sector_data
                )
            )
        except Exception as exc:
            failures.append(
                f"{sector_name}: {exc}"
            )
            print(
                f"ERROR: {sector_name}: {exc}"
            )

    actual_count = len(
        list(
            SECTOR_REPORT_DIR.glob(
                "*_report.pdf"
            )
        )
    )

    print()
    print("Sector report batch summary")
    print("=" * 60)
    print(f"Expected sector PDFs:     {EXPECTED_SECTOR_COUNT}")
    print(f"Generated sector PDFs:    {len(generated)}")
    print(f"Actual directory count:   {actual_count}")
    print(f"Sector directory:         {SECTOR_REPORT_DIR}")

    if failures:
        raise RuntimeError(
            "One or more sector reports failed:\n"
            + "\n".join(
                failures
            )
        )

    if (
        len(generated) != EXPECTED_SECTOR_COUNT
        or actual_count != EXPECTED_SECTOR_COUNT
    ):
        raise RuntimeError(
            "Sector PDF count validation failed: "
            f"generated={len(generated)}, "
            f"actual={actual_count}, "
            f"expected={EXPECTED_SECTOR_COUNT}."
        )

    print(
        "Sector validation: PASS - all 11 sector PDFs were generated."
    )

    return generated


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate broad-sector PDF reports."
        )
    )

    group = parser.add_mutually_exclusive_group(
        required=True
    )

    group.add_argument(
        "--all",
        action="store_true",
        help="Generate all 11 sector reports.",
    )
    group.add_argument(
        "--sector",
        help=(
            "Generate one sector by its exact or "
            "case-insensitive name."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Optional PDF output path. "
            "Only valid with --sector."
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

        generate_all_sector_reports()
        return

    metrics = load_company_metrics()

    available_sectors = sorted(
        metrics["sector"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    sector_lookup = {
        sector.casefold(): sector
        for sector in available_sectors
    }

    requested = str(
        args.sector
    ).strip()

    sector_name = sector_lookup.get(
        requested.casefold()
    )

    if sector_name is None:
        raise SystemExit(
            f"Sector {requested!r} was not found. "
            f"Available sectors: {available_sectors}"
        )

    data = build_sector_data(
        metrics,
        sector_name,
    )

    generate_sector_report(
        data,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
