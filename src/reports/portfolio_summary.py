"""Sprint 5 Day 35: portfolio summary PDF.

Run from the project root:

    python -m src.reports.portfolio_summary

Output:

    reports/portfolio/portfolio_summary.pdf

The report contains one fixed A4 page per company, sorted alphabetically by
ticker. Each page shows the company name, ticker, approved broad sector, six
important KPIs, and an ASCII-safe trend label:

    UP    latest value increased by more than 2%
    DOWN  latest value decreased by more than 2%
    FLAT  change remained within +/-2%
    N/A   fewer than two usable values

The six KPIs match the company-tearsheet definitions:

    1. Revenue CAGR (5Y)
    2. PAT CAGR (5Y)
    3. ROE
    4. ROCE
    5. Debt-to-Equity
    6. CFO Quality (rolling 5-observation average of CFO / PAT)

Optional smoke test:

    python -m src.reports.portfolio_summary ^
        --limit 5 ^
        --output reports/portfolio/portfolio_summary_test.pdf
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
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from src.config import get_settings
from src.reports.sector_report import load_company_metrics


# =============================================================================
# PATHS AND CONSTANTS
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = get_settings()


def resolve_project_path(value: object) -> Path:
    """Resolve configured paths relative to the project root."""

    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


DATABASE_PATH = resolve_project_path(SETTINGS.database_path)

REPORTS_DIR = PROJECT_ROOT / "reports"
PORTFOLIO_DIR = REPORTS_DIR / "portfolio"
DEFAULT_OUTPUT_PATH = PORTFOLIO_DIR / "portfolio_summary.pdf"

PAGE_WIDTH, PAGE_HEIGHT = A4

NAVY = colors.HexColor("#0B1F3A")
NAVY_2 = colors.HexColor("#173B63")
BLUE = colors.HexColor("#2F6B9A")
LIGHT_BLUE = colors.HexColor("#EAF2F8")
PALE_BLUE = colors.HexColor("#F5F8FC")
TEXT_DARK = colors.HexColor("#17202A")
TEXT_MUTED = colors.HexColor("#5D6D7E")
BORDER = colors.HexColor("#D5DDE5")
WHITE = colors.white

UP_BG = colors.HexColor("#E8F5EC")
UP_TEXT = colors.HexColor("#207A3C")
DOWN_BG = colors.HexColor("#FCECEC")
DOWN_TEXT = colors.HexColor("#A93636")
FLAT_BG = colors.HexColor("#EEF1F4")
FLAT_TEXT = colors.HexColor("#59636D")
NA_BG = colors.HexColor("#EAF2F8")
NA_TEXT = colors.HexColor("#2F6B9A")

TREND_THRESHOLD_PCT = 2.0
EXPECTED_COMPANY_COUNT = 92

METRIC_DEFINITIONS = (
    {
        "key": "revenue_cagr_5yr",
        "label": "Revenue CAGR",
        "note": "5-year",
        "format": "percent",
    },
    {
        "key": "pat_cagr_5yr",
        "label": "PAT CAGR",
        "note": "5-year",
        "format": "percent",
    },
    {
        "key": "roe",
        "label": "ROE",
        "note": "latest available",
        "format": "percent",
    },
    {
        "key": "roce",
        "label": "ROCE",
        "note": "latest available",
        "format": "percent",
    },
    {
        "key": "debt_equity",
        "label": "Debt-to-Equity",
        "note": "latest available",
        "format": "ratio",
    },
    {
        "key": "cfo_quality",
        "label": "CFO Quality",
        "note": "5Y avg CFO / PAT",
        "format": "ratio",
    },
)


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass(frozen=True)
class MetricSnapshot:
    """Latest, previous, and trend information for one KPI."""

    latest_value: float | None
    latest_year: int | None
    previous_value: float | None
    previous_year: int | None
    change_pct: float | None
    trend: str


@dataclass(frozen=True)
class CompanySummary:
    """All values required for one company page."""

    ticker: str
    company_name: str
    sector: str
    metrics: dict[str, MetricSnapshot]


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
    """Strip whitespace and uppercase a ticker."""

    if pd.isna(value):
        return ""

    return str(value).strip().upper()


def clean_display_text(value: object) -> str:
    """Collapse newlines and repeated whitespace for PDF display."""

    if pd.isna(value):
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value).strip(),
    )


def normalise_financial_year(value: object) -> int | None:
    """Convert common year representations to a four-digit year."""

    if pd.isna(value):
        return None

    if isinstance(value, (int, float)):
        numeric = int(value)

        if 1900 <= numeric <= 2100:
            return numeric

    text = str(value).strip()

    four_digit = re.search(
        r"(?:19|20)\d{2}",
        text,
    )

    if four_digit:
        return int(four_digit.group(0))

    two_digit = re.search(
        r"(?<!\d)(\d{2})(?!\d)",
        text,
    )

    if two_digit:
        year = int(two_digit.group(1))

        return (
            2000 + year
            if year <= 79
            else 1900 + year
        )

    return None


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
    """Read a SQLite table and normalise its columns."""

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


def select_metric_column(
    frame: pd.DataFrame,
    candidates: Sequence[str],
) -> pd.Series:
    """Return a numeric metric column or an all-NaN fallback."""

    source = first_existing_column(
        frame.columns,
        candidates,
    )

    if source is None:
        return pd.Series(
            np.nan,
            index=frame.index,
            dtype="float64",
        )

    return pd.to_numeric(
        frame[source],
        errors="coerce",
    )


def format_metric(
    value: float | None,
    metric_format: str,
) -> str:
    """Format a KPI for the page."""

    if value is None or pd.isna(value):
        return "N/A"

    if metric_format == "ratio":
        return f"{float(value):,.2f}x"

    return f"{float(value):,.1f}%"


def format_change(
    change_pct: float | None,
) -> str:
    """Format a relative trend change."""

    if change_pct is None or pd.isna(change_pct):
        return "Change: N/A"

    return f"Change: {float(change_pct):+,.1f}%"


# =============================================================================
# KPI HISTORY
# =============================================================================


def exact_cagr_series(
    frame: pd.DataFrame,
    value_column: str,
    years: int = 5,
) -> pd.Series:
    """Calculate exact-period CAGR for each company-year.

    CAGR is returned only when both start and end values are positive and the
    exact start year is available.
    """

    result = pd.Series(
        np.nan,
        index=frame.index,
        dtype="float64",
    )

    if frame.empty or value_column not in frame.columns:
        return result

    lookup = {
        (str(row.company_id), int(row.financial_year)): row_value
        for row, row_value in zip(
            frame[
                [
                    "company_id",
                    "financial_year",
                ]
            ].itertuples(index=False),
            pd.to_numeric(
                frame[value_column],
                errors="coerce",
            ),
        )
        if pd.notna(row_value)
    }

    for index, row in frame[
        [
            "company_id",
            "financial_year",
            value_column,
        ]
    ].iterrows():
        end_value = pd.to_numeric(
            pd.Series([row[value_column]]),
            errors="coerce",
        ).iloc[0]

        if pd.isna(end_value):
            continue

        end_year = int(
            row["financial_year"]
        )
        start_value = lookup.get(
            (
                str(row["company_id"]),
                end_year - years,
            )
        )

        if (
            start_value is None
            or float(start_value) <= 0.0
            or float(end_value) <= 0.0
        ):
            continue

        result.at[index] = (
            (
                float(end_value)
                / float(start_value)
            )
            ** (1.0 / years)
            - 1.0
        ) * 100.0

    return result


def build_kpi_history() -> pd.DataFrame:
    """Build one company-year table containing all six KPI histories."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"Configured database was not found: {DATABASE_PATH}"
        )

    with sqlite3.connect(
        DATABASE_PATH
    ) as connection:
        ratios = prepare_time_table(
            read_table(
                connection,
                "financial_ratios",
            )
        )
        profit_loss = prepare_time_table(
            read_table(
                connection,
                "profitandloss",
            )
        )
        cashflow = prepare_time_table(
            read_table(
                connection,
                "cashflow",
            )
        )

    key_frames = [
        frame[
            [
                "company_id",
                "financial_year",
            ]
        ]
        for frame in (
            ratios,
            profit_loss,
            cashflow,
        )
        if not frame.empty
    ]

    if not key_frames:
        raise RuntimeError(
            "No company-year financial data was found."
        )

    history = pd.concat(
        key_frames,
        ignore_index=True,
    ).drop_duplicates(
        [
            "company_id",
            "financial_year",
        ]
    )

    history = history.sort_values(
        [
            "company_id",
            "financial_year",
        ],
        kind="stable",
    ).reset_index(drop=True)

    # Ratio Engine metrics.
    if not ratios.empty:
        ratio_metrics = ratios[
            [
                "company_id",
                "financial_year",
            ]
        ].copy()

        ratio_metrics[
            "revenue_cagr_5yr"
        ] = select_metric_column(
            ratios,
            (
                "revenue_cagr_5yr",
                "sales_cagr_5yr",
            ),
        )
        ratio_metrics[
            "pat_cagr_5yr"
        ] = select_metric_column(
            ratios,
            (
                "pat_cagr_5yr",
                "profit_cagr_5yr",
            ),
        )
        ratio_metrics["roe"] = select_metric_column(
            ratios,
            (
                "return_on_equity_pct",
                "roe_pct",
                "roe",
            ),
        )
        ratio_metrics["roce"] = select_metric_column(
            ratios,
            (
                "return_on_capital_employed_pct",
                "roce_pct",
                "roce",
            ),
        )
        ratio_metrics[
            "debt_equity"
        ] = select_metric_column(
            ratios,
            (
                "debt_to_equity",
                "debt_equity",
                "de_ratio",
            ),
        )

        history = history.merge(
            ratio_metrics,
            on=[
                "company_id",
                "financial_year",
            ],
            how="left",
            validate="one_to_one",
        )
    else:
        for column in (
            "revenue_cagr_5yr",
            "pat_cagr_5yr",
            "roe",
            "roce",
            "debt_equity",
        ):
            history[column] = np.nan

    # Revenue and PAT are used for CAGR fallback and CFO Quality.
    if not profit_loss.empty:
        pl_metrics = profit_loss[
            [
                "company_id",
                "financial_year",
            ]
        ].copy()

        pl_metrics["revenue"] = select_metric_column(
            profit_loss,
            (
                "sales",
                "revenue",
                "total_revenue",
            ),
        )
        pl_metrics["pat"] = select_metric_column(
            profit_loss,
            (
                "net_profit",
                "profit_after_tax",
                "pat",
            ),
        )

        history = history.merge(
            pl_metrics,
            on=[
                "company_id",
                "financial_year",
            ],
            how="left",
            validate="one_to_one",
        )
    else:
        history["revenue"] = np.nan
        history["pat"] = np.nan

    # CFO is used for the approved CFO/PAT quality definition.
    if not cashflow.empty:
        cf_metrics = cashflow[
            [
                "company_id",
                "financial_year",
            ]
        ].copy()

        cf_metrics["cfo"] = select_metric_column(
            cashflow,
            (
                "operating_activity",
                "cash_from_operating_activity",
                "cash_flow_from_operating_activities",
                "cfo",
            ),
        )

        history = history.merge(
            cf_metrics,
            on=[
                "company_id",
                "financial_year",
            ],
            how="left",
            validate="one_to_one",
        )
    else:
        history["cfo"] = np.nan

    # Exact 5-year CAGR fallback when the Ratio Engine value is unavailable.
    derived_revenue_cagr = exact_cagr_series(
        history,
        "revenue",
        years=5,
    )
    derived_pat_cagr = exact_cagr_series(
        history,
        "pat",
        years=5,
    )

    history[
        "revenue_cagr_5yr"
    ] = pd.to_numeric(
        history["revenue_cagr_5yr"],
        errors="coerce",
    ).combine_first(
        derived_revenue_cagr
    )

    history[
        "pat_cagr_5yr"
    ] = pd.to_numeric(
        history["pat_cagr_5yr"],
        errors="coerce",
    ).combine_first(
        derived_pat_cagr
    )

    annual_cfo_quality = (
        pd.to_numeric(
            history["cfo"],
            errors="coerce",
        )
        / pd.to_numeric(
            history["pat"],
            errors="coerce",
        ).replace(
            0.0,
            np.nan,
        )
    )

    history[
        "annual_cfo_quality"
    ] = annual_cfo_quality.replace(
        [
            np.inf,
            -np.inf,
        ],
        np.nan,
    )

    # Day 31/33 project definition: average valid CFO/PAT observations across
    # the latest five company rows. The rolling form creates a comparable
    # historical series for trend arrows.
    history["cfo_quality"] = (
        history.groupby(
            "company_id",
            sort=False,
        )["annual_cfo_quality"]
        .transform(
            lambda series: series.rolling(
                window=5,
                min_periods=1,
            ).mean()
        )
    )

    for metric in (
        "revenue_cagr_5yr",
        "pat_cagr_5yr",
        "roe",
        "roce",
        "debt_equity",
        "cfo_quality",
    ):
        history[metric] = pd.to_numeric(
            history[metric],
            errors="coerce",
        ).replace(
            [
                np.inf,
                -np.inf,
            ],
            np.nan,
        )

    return history.sort_values(
        [
            "company_id",
            "financial_year",
        ],
        kind="stable",
    ).reset_index(drop=True)


# =============================================================================
# TREND CALCULATION
# =============================================================================


def calculate_trend(
    latest_value: float,
    previous_value: float,
) -> tuple[str, float]:
    """Apply the required +/-2% trend rule.

    The percentage denominator uses abs(previous_value), which also gives a
    stable interpretation for negative KPIs. A zero-to-zero move is FLAT.
    When the previous value is zero and the latest is non-zero, direction is
    determined by the sign of the latest value.
    """

    if abs(previous_value) < 1e-12:
        if abs(latest_value) < 1e-12:
            return "FLAT", 0.0

        return (
            ("UP", np.inf)
            if latest_value > 0.0
            else ("DOWN", -np.inf)
        )

    change_pct = (
        (
            latest_value
            - previous_value
        )
        / abs(previous_value)
        * 100.0
    )

    if change_pct > TREND_THRESHOLD_PCT:
        trend = "UP"
    elif change_pct < -TREND_THRESHOLD_PCT:
        trend = "DOWN"
    else:
        trend = "FLAT"

    return trend, float(change_pct)


def metric_snapshot(
    company_history: pd.DataFrame,
    metric_key: str,
) -> MetricSnapshot:
    """Return the latest two available values and required trend label."""

    if (
        company_history.empty
        or metric_key not in company_history.columns
    ):
        return MetricSnapshot(
            latest_value=None,
            latest_year=None,
            previous_value=None,
            previous_year=None,
            change_pct=None,
            trend="N/A",
        )

    values = company_history[
        [
            "financial_year",
            metric_key,
        ]
    ].copy()

    values[metric_key] = pd.to_numeric(
        values[metric_key],
        errors="coerce",
    )

    values = values.dropna(
        subset=[metric_key]
    ).sort_values(
        "financial_year",
        kind="stable",
    )

    if values.empty:
        return MetricSnapshot(
            latest_value=None,
            latest_year=None,
            previous_value=None,
            previous_year=None,
            change_pct=None,
            trend="N/A",
        )

    latest = values.iloc[-1]

    if len(values) < 2:
        return MetricSnapshot(
            latest_value=float(
                latest[metric_key]
            ),
            latest_year=int(
                latest["financial_year"]
            ),
            previous_value=None,
            previous_year=None,
            change_pct=None,
            trend="N/A",
        )

    previous = values.iloc[-2]

    trend, change_pct = calculate_trend(
        float(latest[metric_key]),
        float(previous[metric_key]),
    )

    return MetricSnapshot(
        latest_value=float(
            latest[metric_key]
        ),
        latest_year=int(
            latest["financial_year"]
        ),
        previous_value=float(
            previous[metric_key]
        ),
        previous_year=int(
            previous["financial_year"]
        ),
        change_pct=change_pct,
        trend=trend,
    )


def assemble_company_summaries(
    limit: int | None = None,
) -> list[CompanySummary]:
    """Load all companies, sort by ticker, and calculate KPI snapshots."""

    directory = load_company_metrics()[
        [
            "company_id",
            "company_name",
            "sector",
        ]
    ].copy()

    directory["company_id"] = directory[
        "company_id"
    ].map(normalise_company_id)

    directory["company_name"] = directory[
        "company_name"
    ].map(clean_display_text)

    directory["sector"] = directory[
        "sector"
    ].map(clean_display_text)

    directory = directory[
        directory["company_id"] != ""
    ].drop_duplicates(
        "company_id",
        keep="last",
    ).sort_values(
        "company_id",
        kind="stable",
    ).reset_index(drop=True)

    if limit is None and len(directory) != EXPECTED_COMPANY_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_COMPANY_COUNT} companies, "
            f"but found {len(directory)}."
        )

    if limit is not None:
        if limit < 1:
            raise ValueError(
                "--limit must be at least 1."
            )

        directory = directory.head(
            limit
        ).copy()

    history = build_kpi_history()

    summaries: list[CompanySummary] = []

    for company in directory.itertuples(
        index=False
    ):
        ticker = str(
            company.company_id
        )

        company_history = history[
            history["company_id"] == ticker
        ].copy()

        snapshots = {
            definition["key"]: metric_snapshot(
                company_history,
                definition["key"],
            )
            for definition in METRIC_DEFINITIONS
        }

        summaries.append(
            CompanySummary(
                ticker=ticker,
                company_name=(
                    str(company.company_name).strip()
                    or ticker
                ),
                sector=(
                    str(company.sector).strip()
                    or "Unclassified"
                ),
                metrics=snapshots,
            )
        )

    return summaries


# =============================================================================
# REPORTLAB STYLES AND DRAWING
# =============================================================================


def build_styles() -> dict[str, ParagraphStyle]:
    """Create paragraph styles for the fixed one-page layout."""

    styles = getSampleStyleSheet()

    return {
        "company_name": ParagraphStyle(
            "PortfolioCompanyName",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=23,
            textColor=WHITE,
            alignment=TA_LEFT,
        ),
        "sector": ParagraphStyle(
            "PortfolioSector",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#D6E3F0"),
            alignment=TA_LEFT,
        ),
        "tile_label": ParagraphStyle(
            "PortfolioTileLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=NAVY,
            alignment=TA_CENTER,
        ),
        "tile_value": ParagraphStyle(
            "PortfolioTileValue",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=25,
            textColor=TEXT_DARK,
            alignment=TA_CENTER,
        ),
        "tile_note": ParagraphStyle(
            "PortfolioTileNote",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=9,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
        ),
        "tile_detail": ParagraphStyle(
            "PortfolioTileDetail",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=TEXT_DARK,
            alignment=TA_CENTER,
        ),
        "footer": ParagraphStyle(
            "PortfolioFooter",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8,
            textColor=TEXT_MUTED,
            alignment=TA_CENTER,
        ),
    }


STYLES = build_styles()


def draw_wrapped_paragraph(
    pdf: canvas.Canvas,
    text: str,
    style: ParagraphStyle,
    x: float,
    top_y: float,
    width: float,
    maximum_height: float,
) -> float:
    """Draw a wrapped paragraph from a top coordinate."""

    paragraph = Paragraph(
        escape(text),
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


def trend_palette(
    trend: str,
) -> tuple[colors.Color, colors.Color]:
    """Return background and text colors for a trend badge."""

    return {
        "UP": (
            UP_BG,
            UP_TEXT,
        ),
        "DOWN": (
            DOWN_BG,
            DOWN_TEXT,
        ),
        "FLAT": (
            FLAT_BG,
            FLAT_TEXT,
        ),
        "N/A": (
            NA_BG,
            NA_TEXT,
        ),
    }.get(
        trend,
        (
            NA_BG,
            NA_TEXT,
        ),
    )


def display_change(
    snapshot: MetricSnapshot,
) -> str:
    """Format change text, including a safe zero-base representation."""

    if snapshot.change_pct is None:
        return "Change: N/A"

    if np.isposinf(snapshot.change_pct):
        return "Change: positive from zero"

    if np.isneginf(snapshot.change_pct):
        return "Change: negative from zero"

    return format_change(
        snapshot.change_pct
    )


def draw_metric_tile(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    definition: dict[str, str],
    snapshot: MetricSnapshot,
) -> None:
    """Draw one fixed KPI tile."""

    pdf.setFillColor(PALE_BLUE)
    pdf.setStrokeColor(BORDER)
    pdf.setLineWidth(0.8)

    pdf.roundRect(
        x,
        y,
        width,
        height,
        8,
        stroke=1,
        fill=1,
    )

    label = Paragraph(
        escape(definition["label"]),
        STYLES["tile_label"],
    )
    note = Paragraph(
        escape(definition["note"]),
        STYLES["tile_note"],
    )
    value = Paragraph(
        escape(
            format_metric(
                snapshot.latest_value,
                definition["format"],
            )
        ),
        STYLES["tile_value"],
    )

    content_width = width - 16

    _, label_height = label.wrap(
        content_width,
        18,
    )
    _, note_height = note.wrap(
        content_width,
        14,
    )
    _, value_height = value.wrap(
        content_width,
        32,
    )

    label.drawOn(
        pdf,
        x + 8,
        y + height - 18 - label_height,
    )

    note.drawOn(
        pdf,
        x + 8,
        y + height - 37 - note_height,
    )

    value.drawOn(
        pdf,
        x + 8,
        y + height - 79 - value_height,
    )

    badge_bg, badge_text = trend_palette(
        snapshot.trend
    )

    badge_width = 72
    badge_height = 25
    badge_x = x + (
        width - badge_width
    ) / 2
    badge_y = y + 79

    pdf.setFillColor(badge_bg)
    pdf.setStrokeColor(badge_bg)

    pdf.roundRect(
        badge_x,
        badge_y,
        badge_width,
        badge_height,
        7,
        stroke=0,
        fill=1,
    )

    pdf.setFillColor(badge_text)
    pdf.setFont(
        "Helvetica-Bold",
        10,
    )

    pdf.drawCentredString(
        x + width / 2,
        badge_y + 8,
        snapshot.trend,
    )

    latest_year_text = (
        f"Latest: FY{snapshot.latest_year}"
        if snapshot.latest_year is not None
        else "Latest: N/A"
    )

    previous_value_text = format_metric(
        snapshot.previous_value,
        definition["format"],
    )

    previous_text = (
        (
            f"Previous FY{snapshot.previous_year}: "
            f"{previous_value_text}"
        )
        if snapshot.previous_year is not None
        else "Previous: N/A"
    )

    details = Paragraph(
        (
            f"{escape(latest_year_text)}<br/>"
            f"{escape(previous_text)}<br/>"
            f"{escape(display_change(snapshot))}"
        ),
        STYLES["tile_detail"],
    )

    _, details_height = details.wrap(
        content_width,
        48,
    )

    details.drawOn(
        pdf,
        x + 8,
        y + 18,
    )


def draw_company_page(
    pdf: canvas.Canvas,
    company: CompanySummary,
    page_number: int,
    total_pages: int,
) -> None:
    """Draw one complete company page."""

    pdf.setFillColor(WHITE)
    pdf.rect(
        0,
        0,
        PAGE_WIDTH,
        PAGE_HEIGHT,
        stroke=0,
        fill=1,
    )

    # Navy header.
    pdf.setFillColor(NAVY)
    pdf.rect(
        0,
        PAGE_HEIGHT - 115,
        PAGE_WIDTH,
        115,
        stroke=0,
        fill=1,
    )

    draw_wrapped_paragraph(
        pdf,
        company.company_name,
        STYLES["company_name"],
        30,
        PAGE_HEIGHT - 31,
        PAGE_WIDTH - 175,
        52,
    )

    draw_wrapped_paragraph(
        pdf,
        company.sector,
        STYLES["sector"],
        30,
        PAGE_HEIGHT - 83,
        PAGE_WIDTH - 175,
        25,
    )

    ticker_width = max(
        75,
        pdf.stringWidth(
            company.ticker,
            "Helvetica-Bold",
            11,
        )
        + 28,
    )

    pdf.setFillColor(NAVY_2)
    pdf.roundRect(
        PAGE_WIDTH - 30 - ticker_width,
        PAGE_HEIGHT - 73,
        ticker_width,
        34,
        8,
        stroke=0,
        fill=1,
    )

    pdf.setFillColor(WHITE)
    pdf.setFont(
        "Helvetica-Bold",
        11,
    )

    pdf.drawCentredString(
        PAGE_WIDTH - 30 - ticker_width / 2,
        PAGE_HEIGHT - 61,
        company.ticker,
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
        PAGE_HEIGHT - 98,
        "Portfolio Summary",
    )

    # Six tiles in two rows of three.
    side_margin = 30
    horizontal_gap = 14
    vertical_gap = 18

    tile_width = (
        PAGE_WIDTH
        - 2 * side_margin
        - 2 * horizontal_gap
    ) / 3

    tile_height = 265

    top_row_y = 413
    bottom_row_y = (
        top_row_y
        - tile_height
        - vertical_gap
    )

    x_positions = [
        side_margin,
        side_margin
        + tile_width
        + horizontal_gap,
        side_margin
        + 2 * (
            tile_width
            + horizontal_gap
        ),
    ]

    y_positions = [
        top_row_y,
        bottom_row_y,
    ]

    for index, definition in enumerate(
        METRIC_DEFINITIONS
    ):
        row = index // 3
        column = index % 3

        draw_metric_tile(
            pdf,
            x_positions[column],
            y_positions[row],
            tile_width,
            tile_height,
            definition,
            company.metrics[
                definition["key"]
            ],
        )

    # Footer.
    pdf.setStrokeColor(BORDER)
    pdf.setLineWidth(0.5)
    pdf.line(
        30,
        34,
        PAGE_WIDTH - 30,
        34,
    )

    footer_text = (
        "Trend compares the latest and previous available KPI values: "
        "more than +2% = UP, less than -2% = DOWN, otherwise FLAT. "
        "Direction is mechanical and is not an investment recommendation."
    )

    footer = Paragraph(
        escape(footer_text),
        STYLES["footer"],
    )

    _, footer_height = footer.wrap(
        PAGE_WIDTH - 135,
        24,
    )

    footer.drawOn(
        pdf,
        30,
        10,
    )

    pdf.setFillColor(TEXT_MUTED)
    pdf.setFont(
        "Helvetica",
        7,
    )

    pdf.drawRightString(
        PAGE_WIDTH - 30,
        18,
        f"Page {page_number} of {total_pages}",
    )


# =============================================================================
# PDF GENERATION AND VALIDATION
# =============================================================================


def validate_portfolio_pdf(
    pdf_path: Path,
    expected_pages: int,
) -> int | None:
    """Validate the generated page count when pypdf is available."""

    if not pdf_path.exists():
        raise RuntimeError(
            f"Portfolio PDF was not created: {pdf_path}"
        )

    if pdf_path.stat().st_size <= 0:
        raise RuntimeError(
            f"Portfolio PDF is empty: {pdf_path}"
        )

    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    reader = PdfReader(
        str(pdf_path)
    )

    actual_pages = len(
        reader.pages
    )

    if actual_pages != expected_pages:
        raise RuntimeError(
            f"Expected {expected_pages} pages, "
            f"but generated {actual_pages}."
        )

    return actual_pages


def generate_portfolio_summary(
    output_path: Path = DEFAULT_OUTPUT_PATH,
    limit: int | None = None,
) -> Path:
    """Generate the complete alphabetically sorted portfolio PDF."""

    summaries = assemble_company_summaries(
        limit=limit
    )

    if not summaries:
        raise RuntimeError(
            "No company summaries were available."
        )

    tickers = [
        company.ticker
        for company in summaries
    ]

    if tickers != sorted(tickers):
        raise RuntimeError(
            "Companies are not sorted alphabetically by ticker."
        )

    pdf_path = (
        output_path
        if output_path.is_absolute()
        else PROJECT_ROOT / output_path
    )

    pdf_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    pdf = canvas.Canvas(
        str(pdf_path),
        pagesize=A4,
        pageCompression=1,
    )

    pdf.setTitle(
        "N100 Portfolio Summary"
    )
    pdf.setAuthor(
        "N100 Financial Intelligence Platform"
    )
    pdf.setSubject(
        "One-page company KPI summaries with trend labels"
    )

    total_pages = len(
        summaries
    )

    for index, company in enumerate(
        summaries,
        start=1,
    ):
        page_key = (
            "company_"
            + re.sub(
                r"[^A-Za-z0-9_-]+",
                "_",
                company.ticker,
            )
        )

        pdf.bookmarkPage(
            page_key
        )

        pdf.addOutlineEntry(
            (
                f"{company.ticker} - "
                f"{company.company_name}"
            ),
            page_key,
            level=0,
            closed=False,
        )

        draw_company_page(
            pdf,
            company,
            index,
            total_pages,
        )

        if index < total_pages:
            pdf.showPage()

    pdf.save()

    actual_pages = validate_portfolio_pdf(
        pdf_path,
        expected_pages=total_pages,
    )

    trend_counts: dict[str, int] = {
        "UP": 0,
        "DOWN": 0,
        "FLAT": 0,
        "N/A": 0,
    }

    for company in summaries:
        for snapshot in company.metrics.values():
            trend_counts[
                snapshot.trend
            ] = (
                trend_counts.get(
                    snapshot.trend,
                    0,
                )
                + 1
            )

    print("Portfolio summary generated")
    print("=" * 60)
    print(f"Companies:               {len(summaries)}")
    print(f"First ticker:            {tickers[0]}")
    print(f"Last ticker:             {tickers[-1]}")
    print(
        "PDF pages:              "
        + (
            str(actual_pages)
            if actual_pages is not None
            else (
                f"{total_pages} expected "
                "(pypdf unavailable)"
            )
        )
    )
    print(f"PDF size:               {pdf_path.stat().st_size:,} bytes")
    print(f"UP labels:              {trend_counts['UP']}")
    print(f"DOWN labels:            {trend_counts['DOWN']}")
    print(f"FLAT labels:            {trend_counts['FLAT']}")
    print(f"N/A labels:             {trend_counts['N/A']}")
    print(f"PDF output:             {pdf_path}")
    print(
        "Validation: PASS - companies are alphabetically sorted "
        "and page count matches company count."
    )

    return pdf_path


def parse_arguments() -> argparse.Namespace:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate the one-page-per-company portfolio summary PDF."
        )
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=(
            "Output PDF path. Default: "
            "reports/portfolio/portfolio_summary.pdf"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Optional smoke-test limit. "
            "The default generates all companies."
        ),
    )

    return parser.parse_args()


def main() -> None:
    """Command-line entry point."""

    args = parse_arguments()

    generate_portfolio_summary(
        output_path=args.output,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
