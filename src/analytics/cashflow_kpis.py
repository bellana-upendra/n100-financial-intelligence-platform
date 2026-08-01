"""Sprint 5 Day 31: Cash Flow Intelligence.

Run from the project root:

    python -m src.analytics.cashflow_kpis

Outputs:

    output/cashflow_intelligence.xlsx
    output/distress_alerts.csv

Project-aligned definitions:
    Free cash flow = CFO + CFI

The Ratio Engine's ``free_cash_flow_cr`` is used first when available.
Otherwise, FCF is calculated from operating_activity + investing_activity.

The Day 31 task defines FCF conversion as:
    FCF conversion % = FCF / Net Profit * 100
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Sequence

import pandas as pd

from src.config import get_settings


# =============================================================================
# PATHS AND OUTPUT COLUMNS
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = get_settings()


def resolve_project_path(value: object) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


DATABASE_PATH = resolve_project_path(SETTINGS.database_path)
OUTPUT_DIR = resolve_project_path(SETTINGS.output_dir)

INTELLIGENCE_OUTPUT = OUTPUT_DIR / "cashflow_intelligence.xlsx"
DISTRESS_OUTPUT = OUTPUT_DIR / "distress_alerts.csv"

EXPECTED_COMPANY_COUNT = 92

INTELLIGENCE_COLUMNS = [
    "company_id",
    "sector",
    "cfo_quality_score",
    "cfo_quality_label",
    "capex_intensity_pct",
    "capex_label",
    "fcf_cagr_5yr",
    "fcf_conversion_pct",
    "distress_flag",
    "deleveraging_flag",
    "capital_allocation_label",
]

DISTRESS_COLUMNS = [
    "company_id",
    "ticker",
    "company_name",
    "cfo_value",
    "cff_value",
    "latest_net_profit",
]


# =============================================================================
# NORMALISATION AND DATABASE HELPERS
# =============================================================================


def normalise_column_name(value: object) -> str:
    """Convert a source column name to lowercase snake_case."""

    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return cleaned.strip("_")


def normalise_company_id(value: object) -> str:
    """Strip whitespace and uppercase a ticker/company identifier."""

    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def normalise_financial_year(value: object) -> int | None:
    """Convert values such as 2024, Mar-24, or 2024-03 to year 2024."""

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


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
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


def read_table(connection: sqlite3.Connection, table_name: str) -> pd.DataFrame:
    """Read a complete SQLite table and normalise its column names."""

    if not table_exists(connection, table_name):
        return pd.DataFrame()

    frame = pd.read_sql_query(f'SELECT * FROM "{table_name}"', connection)
    frame.columns = [normalise_column_name(column) for column in frame.columns]
    return frame


def first_existing_column(
    frame: pd.DataFrame,
    candidates: Sequence[str],
) -> str | None:
    """Return the first candidate column found in a DataFrame."""

    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def clean_nullable_text(series: pd.Series) -> pd.Series:
    """Trim text and convert blank/common null strings to pd.NA."""

    result = series.astype("string").str.strip()
    return result.replace(
        {
            "": pd.NA,
            "nan": pd.NA,
            "NaN": pd.NA,
            "None": pd.NA,
            "<NA>": pd.NA,
        }
    )


def prepare_time_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise company/year and retain one row per company-year."""

    if frame.empty:
        return frame.copy()

    company_column = first_existing_column(
        frame,
        ("company_id", "ticker", "symbol", "company"),
    )
    year_column = first_existing_column(
        frame,
        ("financial_year", "year", "fy", "report_year"),
    )

    if company_column is None or year_column is None:
        return pd.DataFrame()

    result = frame.copy()
    result["company_id"] = result[company_column].map(normalise_company_id)
    result["financial_year"] = result[year_column].map(normalise_financial_year)

    result = result.dropna(subset=["financial_year"])
    result = result[result["company_id"] != ""]
    result["financial_year"] = result["financial_year"].astype(int)

    result["_source_order"] = range(len(result))
    sort_columns = ["company_id", "financial_year"]

    if "id" in result.columns:
        sort_columns.append("id")

    sort_columns.append("_source_order")

    result = result.sort_values(sort_columns, kind="stable")
    result = result.drop_duplicates(
        subset=["company_id", "financial_year"],
        keep="last",
    )

    return result.drop(columns=["_source_order"], errors="ignore")


def select_metrics(
    frame: pd.DataFrame,
    mapping: dict[str, Sequence[str]],
) -> pd.DataFrame:
    """Select and rename metrics from a normalised time-series table."""

    prepared = prepare_time_table(frame)

    columns = ["company_id", "financial_year", *mapping.keys()]
    if prepared.empty:
        return pd.DataFrame(columns=columns)

    result = prepared[["company_id", "financial_year"]].copy()

    for target, candidates in mapping.items():
        source = first_existing_column(prepared, candidates)

        if source is None:
            result[target] = pd.NA
        else:
            result[target] = prepared[source]

    return result


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric series or an all-NaN series if absent."""

    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")

    return pd.to_numeric(frame[column], errors="coerce")


# =============================================================================
# COMPANY AND SECTOR LOADING
# =============================================================================


def load_sector_fallback() -> pd.DataFrame:
    """Recover company-sector mapping from data/raw/sectors.xlsx.

    The current project database can contain broad_sector/sub_sector columns
    whose values are all NULL, so the original workbook is used as a fallback.
    Both header=0 and header=1 layouts are supported.
    """

    raw_dir = PROJECT_ROOT / "data" / "raw"

    candidates = [
        raw_dir / "sectors.xlsx",
        raw_dir / "sector.xlsx",
        raw_dir / "companies.xlsx",
    ]

    if raw_dir.exists():
        candidates.extend(sorted(raw_dir.glob("*sector*.xlsx")))

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
                frame = pd.read_excel(path, header=header_row)
            except Exception:
                continue

            frame.columns = [
                normalise_column_name(column)
                for column in frame.columns
            ]

            company_column = first_existing_column(
                frame,
                ("company_id", "ticker", "symbol", "company"),
            )
            broad_column = first_existing_column(
                frame,
                ("broad_sector", "sector", "sector_name"),
            )
            sub_column = first_existing_column(
                frame,
                ("sub_sector", "subsector", "industry", "industry_name"),
            )

            if company_column is None or (
                broad_column is None and sub_column is None
            ):
                continue

            mapping = pd.DataFrame(
                {
                    "company_id": frame[company_column].map(
                        normalise_company_id
                    ),
                    "fallback_sector": (
                        clean_nullable_text(frame[broad_column])
                        if broad_column is not None
                        else pd.Series(
                            pd.NA,
                            index=frame.index,
                            dtype="string",
                        )
                    ),
                    "fallback_sub_sector": (
                        clean_nullable_text(frame[sub_column])
                        if sub_column is not None
                        else pd.Series(
                            pd.NA,
                            index=frame.index,
                            dtype="string",
                        )
                    ),
                }
            )

            mapping = mapping[mapping["company_id"] != ""]
            mapping = mapping.drop_duplicates("company_id", keep="last")

            if not mapping.empty:
                print(
                    f"Sector fallback workbook: {path} "
                    f"(header={header_row})"
                )
                return mapping

    return pd.DataFrame(
        columns=[
            "company_id",
            "fallback_sector",
            "fallback_sub_sector",
        ]
    )


def load_companies(connection: sqlite3.Connection) -> pd.DataFrame:
    """Load all companies and their sector labels."""

    companies = read_table(connection, "companies")

    if companies.empty:
        raise RuntimeError("The companies table is missing or empty.")

    company_column = first_existing_column(
        companies,
        ("company_id", "ticker", "symbol", "id"),
    )
    name_column = first_existing_column(
        companies,
        ("company_name", "name", "company"),
    )
    sector_column = first_existing_column(
        companies,
        ("broad_sector", "sector", "sector_name"),
    )
    sub_sector_column = first_existing_column(
        companies,
        ("sub_sector", "subsector", "industry", "industry_name"),
    )

    if company_column is None:
        raise RuntimeError(
            "The companies table has no company identifier column."
        )

    result = pd.DataFrame(
        {
            "company_id": companies[company_column].map(
                normalise_company_id
            ),
            "company_name": (
                clean_nullable_text(companies[name_column])
                if name_column is not None
                else companies[company_column].map(normalise_company_id)
            ),
            "sector": (
                clean_nullable_text(companies[sector_column])
                if sector_column is not None
                else pd.Series(
                    pd.NA,
                    index=companies.index,
                    dtype="string",
                )
            ),
            "sub_sector": (
                clean_nullable_text(companies[sub_sector_column])
                if sub_sector_column is not None
                else pd.Series(
                    pd.NA,
                    index=companies.index,
                    dtype="string",
                )
            ),
        }
    )

    result = result[result["company_id"] != ""]
    result = result.drop_duplicates("company_id", keep="last")

    fallback = load_sector_fallback()

    if not fallback.empty:
        result = result.merge(fallback, on="company_id", how="left")

        result["sector"] = result["sector"].combine_first(
            result["fallback_sector"]
        )
        result["sub_sector"] = result["sub_sector"].combine_first(
            result["fallback_sub_sector"]
        )

        result = result.drop(
            columns=[
                "fallback_sector",
                "fallback_sub_sector",
            ],
            errors="ignore",
        )

    result["company_name"] = result["company_name"].fillna(
        result["company_id"]
    )
    result["sector"] = result["sector"].fillna("Unclassified")
    result["sub_sector"] = result["sub_sector"].fillna("Unclassified")

    return result.sort_values(
        "company_id",
        kind="stable",
    ).reset_index(drop=True)


# =============================================================================
# FINANCIAL-HISTORY LOADING
# =============================================================================


def build_cashflow_history(
    connection: sqlite3.Connection,
) -> pd.DataFrame:
    """Build one sorted company-year DataFrame for cash-flow analytics."""

    cashflow_raw = read_table(connection, "cashflow")
    profit_loss_raw = read_table(connection, "profitandloss")
    balance_sheet_raw = read_table(connection, "balancesheet")
    ratios_raw = read_table(connection, "financial_ratios")

    cashflow = select_metrics(
        cashflow_raw,
        {
            "cfo": (
                "operating_activity",
                "cash_from_operating_activity",
                "cash_flow_from_operating_activities",
                "cash_from_operations_cr",
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

    profit_loss = select_metrics(
        profit_loss_raw,
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
            "ebitda": (
                "ebitda",
                "operating_profit",
                "operating_income",
            ),
        },
    )

    balance_sheet = select_metrics(
        balance_sheet_raw,
        {
            "borrowings": (
                "borrowings",
                "total_borrowings",
                "total_debt",
                "debt",
            ),
        },
    )

    ratios = select_metrics(
        ratios_raw,
        {
            "ratio_fcf": (
                "free_cash_flow_cr",
                "free_cash_flow",
                "fcf",
            ),
            "ratio_fcf_cagr_5yr": (
                "fcf_cagr_5yr",
                "free_cash_flow_cagr_5yr",
            ),
            "ratio_capital_allocation": (
                "capital_allocation_label",
                "capital_allocation_pattern",
                "pattern_label",
            ),
        },
    )

    frames = [
        cashflow,
        profit_loss,
        balance_sheet,
        ratios,
    ]

    key_frames = [
        frame[["company_id", "financial_year"]]
        for frame in frames
        if not frame.empty
    ]

    if not key_frames:
        return pd.DataFrame()

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
            if column not in {
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

    numeric_columns = [
        "cfo",
        "cfi",
        "cff",
        "net_cash_flow",
        "sales",
        "net_profit",
        "ebitda",
        "borrowings",
        "ratio_fcf",
        "ratio_fcf_cagr_5yr",
    ]

    for column in numeric_columns:
        if column in history.columns:
            history[column] = pd.to_numeric(
                history[column],
                errors="coerce",
            )

    calculated_fcf = (
        numeric_series(history, "cfo")
        + numeric_series(history, "cfi")
    )

    history["fcf"] = numeric_series(
        history,
        "ratio_fcf",
    ).combine_first(calculated_fcf)

    required_columns = [
        "company_id",
        "financial_year",
        "sales",
        "net_profit",
        "ebitda",
        "cfo",
        "cfi",
        "cff",
        "net_cash_flow",
        "borrowings",
        "fcf",
        "ratio_fcf_cagr_5yr",
        "ratio_capital_allocation",
    ]

    for column in required_columns:
        if column not in history.columns:
            history[column] = pd.NA

    history = history[required_columns]

    history["financial_year"] = pd.to_numeric(
        history["financial_year"],
        errors="coerce",
    )
    history = history.dropna(subset=["financial_year"])
    history["financial_year"] = history["financial_year"].astype(int)

    return history.sort_values(
        [
            "company_id",
            "financial_year",
        ],
        kind="stable",
    ).reset_index(drop=True)


# =============================================================================
# KPI CALCULATION HELPERS
# =============================================================================


def safe_divide(
    numerator: object,
    denominator: object,
    multiplier: float = 1.0,
) -> float | None:
    """Divide safely, returning None for missing or zero denominator."""

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


def classify_cfo_quality(score: float | None) -> str:
    """Classify the five-year average CFO/PAT ratio."""

    if score is None or pd.isna(score):
        return "Insufficient Data"

    if score > 1.0:
        return "High Quality"

    if score >= 0.5:
        return "Moderate"

    return "Accrual Risk"


def classify_capex_intensity(value: float | None) -> str:
    """Classify latest-year CapEx intensity."""

    if value is None or pd.isna(value):
        return "Insufficient Data"

    if value < 3.0:
        return "Asset Light"

    if value <= 8.0:
        return "Moderate"

    return "Capital Intensive"


def compute_fcf_cagr_5yr(
    company_df: pd.DataFrame,
) -> float | None:
    """Calculate exact five-year FCF CAGR.

    Conventional CAGR is undefined when the start or end value is zero or
    negative, so None is returned for those cases.
    """

    if company_df.empty:
        return None

    existing = company_df[
        [
            "financial_year",
            "ratio_fcf_cagr_5yr",
        ]
    ].copy()

    existing["ratio_fcf_cagr_5yr"] = pd.to_numeric(
        existing["ratio_fcf_cagr_5yr"],
        errors="coerce",
    )
    existing = existing.dropna(
        subset=["ratio_fcf_cagr_5yr"]
    ).sort_values("financial_year", kind="stable")

    if not existing.empty:
        return float(existing.iloc[-1]["ratio_fcf_cagr_5yr"])

    values = company_df[
        [
            "financial_year",
            "fcf",
        ]
    ].copy()

    values["fcf"] = pd.to_numeric(
        values["fcf"],
        errors="coerce",
    )
    values = values.dropna(subset=["fcf"])
    values = values.drop_duplicates(
        "financial_year",
        keep="last",
    )
    values = values.sort_values(
        "financial_year",
        kind="stable",
    )

    if values.empty:
        return None

    latest = values.iloc[-1]
    end_year = int(latest["financial_year"])
    start_year = end_year - 5

    start_rows = values[
        values["financial_year"] == start_year
    ]

    if start_rows.empty:
        return None

    start_value = float(start_rows.iloc[-1]["fcf"])
    end_value = float(latest["fcf"])

    if start_value <= 0.0 or end_value <= 0.0:
        return None

    return (
        (
            end_value / start_value
        ) ** (1.0 / 5.0)
        - 1.0
    ) * 100.0


def sign_code(value: object) -> str | None:
    """Return '+' for non-negative, '-' for negative, or None if missing."""

    numeric = pd.to_numeric(
        pd.Series([value]),
        errors="coerce",
    ).iloc[0]

    if pd.isna(numeric):
        return None

    return "+" if float(numeric) >= 0.0 else "-"


CAPITAL_ALLOCATION_PATTERNS = {
    ("+", "-", "-"): "Reinvestor",
    ("+", "-", "+"): "Growth Financed",
    ("+", "+", "-"): "Shareholder Returns",
    ("+", "+", "+"): "Cash Accumulator",
    ("-", "+", "+"): "Distress Signal",
    ("-", "+", "-"): "Asset Liquidation",
    ("-", "-", "+"): "Expansion Funded Externally",
    ("-", "-", "-"): "Cash Burn",
}


def classify_capital_allocation(
    cfo: object,
    cfi: object,
    cff: object,
) -> str:
    """Classify the latest CFO/CFI/CFF sign pattern."""

    pattern = (
        sign_code(cfo),
        sign_code(cfi),
        sign_code(cff),
    )

    if None in pattern:
        return "Insufficient Data"

    return CAPITAL_ALLOCATION_PATTERNS.get(
        pattern,
        "Unclassified",
    )


def latest_nonblank_text(
    company_df: pd.DataFrame,
    column: str,
) -> str:
    """Return latest nonblank text from a company history column."""

    if column not in company_df.columns:
        return ""

    values = company_df[
        [
            "financial_year",
            column,
        ]
    ].copy()

    values[column] = values[column].astype("string").str.strip()
    values = values.dropna(subset=[column])
    values = values[
        ~values[column].isin(
            [
                "",
                "nan",
                "None",
                "<NA>",
            ]
        )
    ]
    values = values.sort_values(
        "financial_year",
        kind="stable",
    )

    if values.empty:
        return ""

    return str(values.iloc[-1][column])


# =============================================================================
# COMPANY-LEVEL INTELLIGENCE
# =============================================================================


def evaluate_company(
    company: pd.Series,
    company_df: pd.DataFrame,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Calculate all Day 31 fields for one company."""

    company_id = normalise_company_id(
        company["company_id"]
    )

    if company_df.empty:
        intelligence_row = {
            "company_id": company_id,
            "sector": company["sector"],
            "cfo_quality_score": None,
            "cfo_quality_label": "Insufficient Data",
            "capex_intensity_pct": None,
            "capex_label": "Insufficient Data",
            "fcf_cagr_5yr": None,
            "fcf_conversion_pct": None,
            "distress_flag": False,
            "deleveraging_flag": False,
            "capital_allocation_label": "Insufficient Data",
        }
        return intelligence_row, None

    company_df = company_df.sort_values(
        "financial_year",
        kind="stable",
    ).copy()

    latest = company_df.iloc[-1]
    latest_year = int(latest["financial_year"])

    # CFO quality: annual CFO/PAT ratio, averaged over the latest five years.
    recent_five = company_df.tail(5).copy()
    recent_five["cfo_quality_ratio"] = recent_five.apply(
        lambda row: safe_divide(
            row.get("cfo"),
            row.get("net_profit"),
        ),
        axis=1,
    )

    valid_quality = pd.to_numeric(
        recent_five["cfo_quality_ratio"],
        errors="coerce",
    ).dropna()

    cfo_quality_score = (
        float(valid_quality.mean())
        if not valid_quality.empty
        else None
    )

    # Latest-year CapEx intensity.
    capex_intensity_pct = safe_divide(
        abs(float(latest["cfi"]))
        if pd.notna(latest["cfi"])
        else None,
        latest["sales"],
        multiplier=100.0,
    )

    # FCF conversion uses the latest-year FCF/PAT definition requested for Day 31.
    fcf_conversion_pct = safe_divide(
        latest["fcf"],
        latest["net_profit"],
        multiplier=100.0,
    )

    # Latest-year distress flag.
    distress_flag = bool(
        pd.notna(latest["cfo"])
        and pd.notna(latest["cff"])
        and float(latest["cfo"]) < 0.0
        and float(latest["cff"]) > 0.0
    )

    # Previous-year borrowings must be the exact prior financial year.
    prior_rows = company_df[
        company_df["financial_year"] == latest_year - 1
    ]

    previous_borrowings = (
        pd.to_numeric(
            prior_rows["borrowings"],
            errors="coerce",
        ).dropna()
    )

    previous_borrowing_value = (
        float(previous_borrowings.iloc[-1])
        if not previous_borrowings.empty
        else None
    )

    deleveraging_flag = bool(
        pd.notna(latest["cff"])
        and float(latest["cff"]) < 0.0
        and pd.notna(latest["borrowings"])
        and previous_borrowing_value is not None
        and float(latest["borrowings"])
        < previous_borrowing_value
    )

    existing_pattern = latest_nonblank_text(
        company_df,
        "ratio_capital_allocation",
    )

    capital_allocation_label = (
        existing_pattern
        if existing_pattern
        else classify_capital_allocation(
            latest["cfo"],
            latest["cfi"],
            latest["cff"],
        )
    )

    intelligence_row = {
        "company_id": company_id,
        "sector": company["sector"],
        "cfo_quality_score": (
            round(cfo_quality_score, 4)
            if cfo_quality_score is not None
            else None
        ),
        "cfo_quality_label": classify_cfo_quality(
            cfo_quality_score
        ),
        "capex_intensity_pct": (
            round(capex_intensity_pct, 4)
            if capex_intensity_pct is not None
            else None
        ),
        "capex_label": classify_capex_intensity(
            capex_intensity_pct
        ),
        "fcf_cagr_5yr": (
            round(value, 4)
            if (
                value := compute_fcf_cagr_5yr(
                    company_df
                )
            ) is not None
            else None
        ),
        "fcf_conversion_pct": (
            round(fcf_conversion_pct, 4)
            if fcf_conversion_pct is not None
            else None
        ),
        "distress_flag": distress_flag,
        "deleveraging_flag": deleveraging_flag,
        "capital_allocation_label": (
            capital_allocation_label
        ),
    }

    distress_row = None

    if distress_flag:
        distress_row = {
            "company_id": company_id,
            "ticker": company_id,
            "company_name": company["company_name"],
            "cfo_value": (
                round(float(latest["cfo"]), 4)
                if pd.notna(latest["cfo"])
                else None
            ),
            "cff_value": (
                round(float(latest["cff"]), 4)
                if pd.notna(latest["cff"])
                else None
            ),
            "latest_net_profit": (
                round(float(latest["net_profit"]), 4)
                if pd.notna(latest["net_profit"])
                else None
            ),
        }

    return intelligence_row, distress_row


def generate_cashflow_intelligence(
    companies: pd.DataFrame,
    history: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate one intelligence row per company and distress alerts."""

    intelligence_rows: list[dict[str, object]] = []
    distress_rows: list[dict[str, object]] = []

    grouped = {
        company_id: frame.copy()
        for company_id, frame in history.groupby(
            "company_id",
            sort=False,
        )
    } if not history.empty else {}

    for _, company in companies.iterrows():
        company_id = normalise_company_id(
            company["company_id"]
        )

        company_df = grouped.get(
            company_id,
            pd.DataFrame(),
        )

        intelligence_row, distress_row = evaluate_company(
            company,
            company_df,
        )

        intelligence_rows.append(intelligence_row)

        if distress_row is not None:
            distress_rows.append(distress_row)

    intelligence = pd.DataFrame(
        intelligence_rows,
        columns=INTELLIGENCE_COLUMNS,
    ).sort_values(
        "company_id",
        kind="stable",
    ).reset_index(drop=True)

    distress = pd.DataFrame(
        distress_rows,
        columns=DISTRESS_COLUMNS,
    ).sort_values(
        "company_id",
        kind="stable",
    ).reset_index(drop=True)

    return intelligence, distress


# =============================================================================
# OUTPUT WRITING AND VALIDATION
# =============================================================================


def write_outputs(
    intelligence: pd.DataFrame,
    distress: pd.DataFrame,
) -> None:
    """Write the required Excel and CSV files."""

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with pd.ExcelWriter(
        INTELLIGENCE_OUTPUT,
        engine="openpyxl",
    ) as writer:
        intelligence.to_excel(
            writer,
            index=False,
            sheet_name="Cash Flow Intelligence",
        )

        worksheet = writer.sheets[
            "Cash Flow Intelligence"
        ]

        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions

        column_widths = {
            "A": 16,
            "B": 24,
            "C": 19,
            "D": 20,
            "E": 21,
            "F": 20,
            "G": 17,
            "H": 20,
            "I": 15,
            "J": 18,
            "K": 31,
        }

        for column_letter, width in column_widths.items():
            worksheet.column_dimensions[
                column_letter
            ].width = width

        for cell in worksheet[1]:
            cell.font = cell.font.copy(bold=True)

        # Apply readable numeric formats.
        for row in range(
            2,
            worksheet.max_row + 1,
        ):
            worksheet[f"C{row}"].number_format = "0.0000"
            worksheet[f"E{row}"].number_format = "0.0000"
            worksheet[f"G{row}"].number_format = "0.0000"
            worksheet[f"H{row}"].number_format = "0.0000"

    distress.reindex(
        columns=DISTRESS_COLUMNS
    ).to_csv(
        DISTRESS_OUTPUT,
        index=False,
    )


def validate_outputs(
    intelligence: pd.DataFrame,
    companies: pd.DataFrame,
) -> None:
    """Run the required company-count and duplicate checks."""

    duplicates = int(
        intelligence["company_id"].duplicated().sum()
    )

    if duplicates:
        raise RuntimeError(
            f"Validation failed: {duplicates} duplicate company rows."
        )

    expected_ids = set(
        companies["company_id"].map(
            normalise_company_id
        )
    )
    actual_ids = set(
        intelligence["company_id"].map(
            normalise_company_id
        )
    )

    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)

    if missing or unexpected:
        raise RuntimeError(
            "Company coverage validation failed. "
            f"Missing={missing}; Unexpected={unexpected}"
        )

    if len(intelligence) != len(companies):
        raise RuntimeError(
            "Row-count validation failed: "
            f"companies={len(companies)}, "
            f"output={len(intelligence)}"
        )


# =============================================================================
# MAIN PIPELINE
# =============================================================================


def run_cashflow_intelligence() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the complete Sprint 5 Day 31 pipeline."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(
            f"Configured database was not found: {DATABASE_PATH}"
        )

    print(f"Database: {DATABASE_PATH}")

    with sqlite3.connect(DATABASE_PATH) as connection:
        companies = load_companies(connection)
        history = build_cashflow_history(connection)

    intelligence, distress = generate_cashflow_intelligence(
        companies,
        history,
    )

    validate_outputs(
        intelligence,
        companies,
    )

    write_outputs(
        intelligence,
        distress,
    )

    print()
    print("Cash Flow Intelligence completed")
    print("=" * 60)
    print(f"Companies table:          {len(companies)}")
    print(
        "Companies with history:   "
        f"{history['company_id'].nunique() if not history.empty else 0}"
    )
    print(f"Financial-history rows:   {len(history)}")
    print(f"Output rows:              {len(intelligence)}")
    print(
        "Duplicate companies:      "
        f"{intelligence['company_id'].duplicated().sum()}"
    )
    print(
        "Distress alerts:          "
        f"{intelligence['distress_flag'].sum()}"
    )
    print(
        "Deleveraging companies:   "
        f"{intelligence['deleveraging_flag'].sum()}"
    )
    print(f"Excel output:             {INTELLIGENCE_OUTPUT}")
    print(f"Distress output:          {DISTRESS_OUTPUT}")

    if len(intelligence) == EXPECTED_COMPANY_COUNT:
        print(
            "Coverage check: PASS — 92 company rows generated."
        )
    else:
        print(
            "Coverage check: REVIEW — expected "
            f"{EXPECTED_COMPANY_COUNT}, generated {len(intelligence)}."
        )

    return intelligence, distress


def main() -> None:
    """Command-line entry point."""

    run_cashflow_intelligence()


if __name__ == "__main__":
    main()
