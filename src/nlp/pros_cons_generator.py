"""Sprint 5 Day 30: automatic pros and cons generator.

Run from the project root:

    python -m src.nlp.pros_cons_generator

Output:

    output/pros_cons_generated.csv

A diagnostic file is also written when any company has no qualifying pro or
con:

    output/pros_cons_missing_coverage.csv

The generator reads the configured SQLite database, builds one company-year
financial-history DataFrame, evaluates 12 pro rules and 12 con rules, and saves
only matched rules with confidence greater than 60. When a company has no
absolute-threshold pro or con, a conservative evidence-based coverage check uses
real reported metrics (low leverage, cash conversion, growth, payout, returns,
and leverage) rather than inventing a statement.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pandas as pd

from src.config import get_settings


# ============================================================================
# PATHS AND OUTPUT SCHEMA
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = get_settings()


def resolve_project_path(value: object) -> Path:
    """Resolve a configured path relative to the project root."""

    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


DATABASE_PATH = resolve_project_path(SETTINGS.database_path)
OUTPUT_DIR = resolve_project_path(SETTINGS.output_dir)
OUTPUT_PATH = OUTPUT_DIR / "pros_cons_generated.csv"
MISSING_COVERAGE_PATH = OUTPUT_DIR / "pros_cons_missing_coverage.csv"

OUTPUT_COLUMNS = [
    "company_id",
    "type",
    "rule_id",
    "text",
    "confidence_pct",
]

MISSING_COVERAGE_COLUMNS = [
    "company_id",
    "company_name",
    "broad_sector",
    "sub_sector",
    "missing_type",
    "reason",
]

MIN_CONFIDENCE_EXCLUSIVE = 60.0
EXPECTED_COMPANY_COUNT = 92


# ============================================================================
# RULE RESULT MODEL
# ============================================================================

@dataclass(frozen=True)
class RuleResult:
    """Result produced by one rule evaluator."""

    matched: bool
    rule_id: str
    rule_type: str
    text: str
    confidence_pct: float


RuleFunction = Callable[[pd.DataFrame], RuleResult]


def no_match(rule_id: str, rule_type: str) -> RuleResult:
    """Return a standard non-match result."""

    return RuleResult(
        matched=False,
        rule_id=rule_id,
        rule_type=rule_type,
        text="",
        confidence_pct=0.0,
    )


def matched_result(
    rule_id: str,
    rule_type: str,
    text: str,
    confidence_pct: float,
) -> RuleResult:
    """Create a matched result with confidence constrained to 0-100."""

    confidence = max(0.0, min(100.0, float(confidence_pct)))
    return RuleResult(
        matched=True,
        rule_id=rule_id,
        rule_type=rule_type,
        text=text,
        confidence_pct=round(confidence, 1),
    )


# ============================================================================
# DATABASE AND COLUMN HELPERS
# ============================================================================


def normalise_column_name(value: object) -> str:
    """Convert a source column name to lowercase snake_case."""

    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return cleaned.strip("_")


def normalise_company_id(value: object) -> str:
    """Strip and uppercase a company identifier."""

    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def normalise_financial_year(value: object) -> int | None:
    """Convert common year labels such as 2024, Mar-24, or 2024-03 to 2024."""

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
        WHERE type = 'table' AND name = ?
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


def first_existing_column(frame: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    """Return the first candidate column present in a DataFrame."""

    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def prepare_time_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise company ID/year and keep one row per company-year."""

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
    result = result.dropna(subset=["company_id", "financial_year"])
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
    """Select and rename metrics from one normalised time-series table."""

    prepared = prepare_time_table(frame)
    if prepared.empty:
        return pd.DataFrame(columns=["company_id", "financial_year", *mapping])

    result = prepared[["company_id", "financial_year"]].copy()
    for target, candidates in mapping.items():
        source = first_existing_column(prepared, candidates)
        if source is None:
            result[target] = pd.NA
        else:
            result[target] = prepared[source]
    return result


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric series, or an all-NaN series when the column is absent."""

    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def coalesce_series(*series_list: pd.Series) -> pd.Series:
    """Return the first non-null value across aligned series."""

    if not series_list:
        raise ValueError("At least one series is required")

    result = series_list[0].copy()
    for series in series_list[1:]:
        result = result.combine_first(series)
    return result


# ============================================================================
# FINANCIAL HISTORY LOADER
# ============================================================================


def clean_nullable_text(series: pd.Series) -> pd.Series:
    """Normalise text values and convert blanks/common null markers to pd.NA."""

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


def load_raw_company_sector_mapping() -> pd.DataFrame:
    """Load company-sector labels from a raw workbook when SQLite is blank.

    Sprint source files normally keep the company mapping in
    ``data/raw/sectors.xlsx`` with ``header=1``. Some project copies keep the
    same fields in ``companies.xlsx`` or use a normal header row, so both
    layouts are supported.
    """

    raw_dir = PROJECT_ROOT / "data" / "raw"
    preferred = [
        raw_dir / "sectors.xlsx",
        raw_dir / "sector.xlsx",
        raw_dir / "companies.xlsx",
    ]

    discovered: list[Path] = []
    if raw_dir.exists():
        discovered.extend(sorted(raw_dir.glob("*.xlsx")))

    candidates: list[Path] = []
    seen: set[Path] = set()
    for path in [*preferred, *discovered]:
        resolved = path.resolve()
        if path.exists() and resolved not in seen:
            candidates.append(path)
            seen.add(resolved)

    for path in candidates:
        # Avoid reading unrelated workbooks unless their names are plausible.
        name = path.stem.lower()
        if path not in preferred and "sector" not in name and "compan" not in name:
            continue

        for header_row in (1, 0):
            try:
                frame = pd.read_excel(path, header=header_row)
            except Exception:
                continue

            frame.columns = [normalise_column_name(column) for column in frame.columns]
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

            if company_column is None or (broad_column is None and sub_column is None):
                continue

            mapping = pd.DataFrame(
                {
                    "company_id": frame[company_column].map(normalise_company_id),
                    "raw_broad_sector": (
                        clean_nullable_text(frame[broad_column])
                        if broad_column is not None
                        else pd.Series(pd.NA, index=frame.index, dtype="string")
                    ),
                    "raw_sub_sector": (
                        clean_nullable_text(frame[sub_column])
                        if sub_column is not None
                        else pd.Series(pd.NA, index=frame.index, dtype="string")
                    ),
                }
            )
            mapping = mapping[mapping["company_id"] != ""]
            mapping = mapping.drop_duplicates("company_id", keep="last")

            if not mapping.empty and not mapping[
                ["raw_broad_sector", "raw_sub_sector"]
            ].isna().all().all():
                print(f"Sector fallback workbook: {path} (header={header_row})")
                return mapping

    return pd.DataFrame(
        columns=["company_id", "raw_broad_sector", "raw_sub_sector"]
    )


def load_companies_and_sectors(
    connection: sqlite3.Connection,
) -> pd.DataFrame:
    """Load all companies and recover sector labels from the best source.

    The ``companies`` table is preferred. If its sector columns exist but are
    empty, the loader falls back to the raw company-sector workbook. The
    database ``sectors`` table in this project is only a dimension containing
    ``sector_id`` and ``sector_name``; it cannot be joined to companies because
    it has no company identifier.
    """

    companies = read_table(connection, "companies")
    if companies.empty:
        raise RuntimeError("The companies table is missing or empty.")

    company_id_column = first_existing_column(
        companies,
        ("company_id", "ticker", "symbol", "id"),
    )
    if company_id_column is None:
        raise RuntimeError("The companies table has no company identifier column.")

    company_name_column = first_existing_column(
        companies,
        ("company_name", "name", "company"),
    )
    broad_sector_column = first_existing_column(
        companies,
        ("broad_sector", "sector", "sector_name"),
    )
    sub_sector_column = first_existing_column(
        companies,
        ("sub_sector", "subsector", "industry", "industry_name"),
    )

    company_frame = pd.DataFrame(
        {
            "company_id": companies[company_id_column].map(normalise_company_id),
            "company_name": (
                clean_nullable_text(companies[company_name_column])
                if company_name_column is not None
                else clean_nullable_text(companies[company_id_column])
            ),
            "broad_sector": (
                clean_nullable_text(companies[broad_sector_column])
                if broad_sector_column is not None
                else pd.Series(pd.NA, index=companies.index, dtype="string")
            ),
            "sub_sector": (
                clean_nullable_text(companies[sub_sector_column])
                if sub_sector_column is not None
                else pd.Series(pd.NA, index=companies.index, dtype="string")
            ),
        }
    )

    company_frame = company_frame[company_frame["company_id"] != ""]
    company_frame = company_frame.drop_duplicates("company_id", keep="last")

    # The current SQLite database has the columns but may contain NULL for all
    # companies. Recover those labels from the raw source workbook.
    raw_mapping = load_raw_company_sector_mapping()
    if not raw_mapping.empty:
        company_frame = company_frame.merge(raw_mapping, on="company_id", how="left")
        company_frame["broad_sector"] = company_frame["broad_sector"].combine_first(
            company_frame["raw_broad_sector"]
        )
        company_frame["sub_sector"] = company_frame["sub_sector"].combine_first(
            company_frame["raw_sub_sector"]
        )
        company_frame = company_frame.drop(
            columns=["raw_broad_sector", "raw_sub_sector"],
            errors="ignore",
        )

    missing_broad = int(company_frame["broad_sector"].isna().sum())
    if missing_broad:
        print(
            "Warning: broad_sector is still missing for "
            f"{missing_broad} of {len(company_frame)} companies."
        )

    return company_frame.sort_values("company_id", kind="stable").reset_index(
        drop=True
    )

def build_financial_history(
    connection: sqlite3.Connection,
    companies: pd.DataFrame,
) -> pd.DataFrame:
    """Build one sorted company-year DataFrame used by all 24 rules."""

    ratios_raw = read_table(connection, "financial_ratios")
    pl_raw = read_table(connection, "profitandloss")
    bs_raw = read_table(connection, "balancesheet")
    cf_raw = read_table(connection, "cashflow")
    market_raw = read_table(connection, "market_cap")

    ratios = select_metrics(
        ratios_raw,
        {
            "roe_ratio": ("return_on_equity_pct", "roe_pct", "roe"),
            "roce_ratio": (
                "return_on_capital_employed_pct",
                "roce_pct",
                "roce",
            ),
            "opm_ratio": (
                "operating_profit_margin_pct",
                "opm_percentage",
                "opm_pct",
                "opm",
            ),
            "debt_equity_ratio": ("debt_to_equity", "debt_equity", "de_ratio"),
            "fcf_ratio": ("free_cash_flow_cr", "free_cash_flow", "fcf"),
            "interest_coverage_ratio": (
                "interest_coverage",
                "interest_coverage_ratio",
                "icr",
            ),
            "icr_label": ("icr_label", "interest_coverage_label"),
            "revenue_cagr_5yr": ("revenue_cagr_5yr", "sales_cagr_5yr"),
            "pat_cagr_5yr": ("pat_cagr_5yr", "profit_cagr_5yr"),
            "eps_cagr_5yr": ("eps_cagr_5yr",),
            "dividend_payout_ratio": (
                "dividend_payout_ratio_pct",
                "dividend_payout_pct",
                "dividend_payout_ratio",
            ),
        },
    )

    profit_loss = select_metrics(
        pl_raw,
        {
            "sales_pl": ("sales", "revenue", "total_revenue"),
            "net_profit_pl": ("net_profit", "profit_after_tax", "pat"),
            "eps_pl": ("eps", "earnings_per_share"),
            "ebitda_pl": ("ebitda", "operating_profit", "operating_income"),
            "interest_expense_pl": ("interest", "finance_cost", "interest_expense"),
            "depreciation_pl": ("depreciation", "depreciation_amortisation"),
            "dividend_payout_pl": (
                "dividend_payout",
                "dividend_payout_pct",
                "dividend_payout_ratio_pct",
            ),
        },
    )

    balance_sheet = select_metrics(
        bs_raw,
        {
            "borrowings_bs": ("borrowings", "total_borrowings", "debt"),
            "total_assets_bs": ("total_assets",),
            "cash_bs": (
                "cash_and_cash_equivalents",
                "cash_equivalents",
                "cash_and_bank",
                "cash_bank_balance",
                "cash",
            ),
            "equity_bs": (
                "equity_share_capital",
                "share_capital",
                "equity",
            ),
            "reserves_bs": ("reserves", "reserves_and_surplus"),
        },
    )

    cash_flow = select_metrics(
        cf_raw,
        {
            "cfo_cf": (
                "operating_activity",
                "cash_from_operating_activity",
                "cash_flow_from_operating_activities",
                "cfo",
            ),
            "cfi_cf": (
                "investing_activity",
                "cash_from_investing_activity",
                "cash_flow_from_investing_activities",
                "cfi",
            ),
        },
    )

    market = select_metrics(
        market_raw,
        {
            "dividend_yield_market": (
                "dividend_yield_pct",
                "dividend_yield",
            ),
        },
    )

    time_frames = [ratios, profit_loss, balance_sheet, cash_flow, market]
    pair_frames = [
        frame[["company_id", "financial_year"]]
        for frame in time_frames
        if not frame.empty
    ]

    if not pair_frames:
        return pd.DataFrame()

    history = pd.concat(pair_frames, ignore_index=True).drop_duplicates()

    for frame in time_frames:
        if frame.empty:
            continue
        value_columns = [
            column
            for column in frame.columns
            if column not in {"company_id", "financial_year"}
        ]
        history = history.merge(
            frame[["company_id", "financial_year", *value_columns]],
            on=["company_id", "financial_year"],
            how="left",
        )

    history = history.merge(
        companies,
        on="company_id",
        how="left",
    )

    # Convert all metric candidates to numbers before deriving final fields.
    numeric_candidates = [
        "roe_ratio",
        "roce_ratio",
        "opm_ratio",
        "debt_equity_ratio",
        "fcf_ratio",
        "interest_coverage_ratio",
        "revenue_cagr_5yr",
        "pat_cagr_5yr",
        "eps_cagr_5yr",
        "dividend_payout_ratio",
        "sales_pl",
        "net_profit_pl",
        "eps_pl",
        "ebitda_pl",
        "interest_expense_pl",
        "depreciation_pl",
        "dividend_payout_pl",
        "borrowings_bs",
        "total_assets_bs",
        "cash_bs",
        "equity_bs",
        "reserves_bs",
        "cfo_cf",
        "cfi_cf",
        "dividend_yield_market",
    ]
    for column in numeric_candidates:
        if column in history.columns:
            history[column] = pd.to_numeric(history[column], errors="coerce")

    history["sales"] = numeric_series(history, "sales_pl")
    history["net_profit"] = numeric_series(history, "net_profit_pl")
    history["cfo"] = numeric_series(history, "cfo_cf")
    history["roe"] = numeric_series(history, "roe_ratio")
    history["roce"] = numeric_series(history, "roce_ratio")
    history["eps"] = numeric_series(history, "eps_pl")
    history["borrowings"] = numeric_series(history, "borrowings_bs")
    history["total_assets"] = numeric_series(history, "total_assets_bs")
    history["ebitda"] = numeric_series(history, "ebitda_pl")
    history["interest_coverage"] = numeric_series(history, "interest_coverage_ratio")
    history["dividend_yield"] = numeric_series(history, "dividend_yield_market")
    history["dividend_payout"] = coalesce_series(
        numeric_series(history, "dividend_payout_ratio"),
        numeric_series(history, "dividend_payout_pl"),
    )

    calculated_fcf = numeric_series(history, "cfo_cf") + numeric_series(history, "cfi_cf")
    history["fcf"] = coalesce_series(
        numeric_series(history, "fcf_ratio"),
        calculated_fcf,
    )

    calculated_opm = (
        numeric_series(history, "ebitda_pl")
        / numeric_series(history, "sales_pl").replace(0, pd.NA)
        * 100.0
    )
    history["opm"] = coalesce_series(
        numeric_series(history, "opm_ratio"),
        calculated_opm,
    )

    equity_plus_reserves = (
        numeric_series(history, "equity_bs")
        + numeric_series(history, "reserves_bs")
    )
    calculated_de = (
        numeric_series(history, "borrowings_bs")
        / equity_plus_reserves.replace(0, pd.NA)
    )
    history["debt_equity"] = coalesce_series(
        numeric_series(history, "debt_equity_ratio"),
        calculated_de,
    )

    cash = numeric_series(history, "cash_bs")
    history["net_debt"] = history["borrowings"] - cash.fillna(0.0)
    history["net_debt_is_proxy"] = cash.isna()

    required_columns = [
        "company_id",
        "company_name",
        "broad_sector",
        "sub_sector",
        "financial_year",
        "sales",
        "net_profit",
        "cfo",
        "fcf",
        "roe",
        "roce",
        "opm",
        "eps",
        "debt_equity",
        "borrowings",
        "total_assets",
        "ebitda",
        "interest_coverage",
        "dividend_yield",
        "dividend_payout",
        "revenue_cagr_5yr",
        "pat_cagr_5yr",
        "eps_cagr_5yr",
        "net_debt",
        "net_debt_is_proxy",
        "icr_label",
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
        ["company_id", "financial_year"],
        kind="stable",
    ).reset_index(drop=True)


# ============================================================================
# ANALYTICAL HELPERS
# ============================================================================

FINANCIAL_SECTOR_KEYWORDS = (
    "bank",
    "banking",
    "financial",
    "finance",
    "insurance",
    "nbfc",
    "lending",
    "consumer finance",
    "housing finance",
    "asset management",
    "capital market",
    "brokerage",
    "fintech",
)


def is_financial_sector(sector: object, sub_sector: object = "") -> bool:
    """Classify actual database sector labels as financial or non-financial."""

    values = []
    for value in (sector, sub_sector):
        if not pd.isna(value):
            values.append(str(value).strip().lower())
    combined = " ".join(values)
    return any(keyword in combined for keyword in FINANCIAL_SECTOR_KEYWORDS)


def latest_row(company_df: pd.DataFrame) -> pd.Series | None:
    """Return the latest company-year row."""

    if company_df.empty:
        return None
    return company_df.sort_values("financial_year", kind="stable").iloc[-1]


def latest_numeric(company_df: pd.DataFrame, column: str) -> float | None:
    """Return the latest non-null numeric value for a metric."""

    if company_df.empty or column not in company_df.columns:
        return None

    values = company_df[["financial_year", column]].copy()
    values[column] = pd.to_numeric(values[column], errors="coerce")
    values = values.dropna(subset=[column]).sort_values("financial_year", kind="stable")
    if values.empty:
        return None
    return float(values.iloc[-1][column])


def latest_text(company_df: pd.DataFrame, column: str) -> str:
    """Return the latest non-blank text value for a column."""

    if company_df.empty or column not in company_df.columns:
        return ""

    values = company_df[["financial_year", column]].dropna(subset=[column])
    values = values.sort_values("financial_year", kind="stable")
    if values.empty:
        return ""
    return str(values.iloc[-1][column]).strip()


def trailing_consecutive_rows(
    company_df: pd.DataFrame,
    columns: Sequence[str],
) -> pd.DataFrame:
    """Return the latest uninterrupted annual window with complete metrics."""

    if company_df.empty or any(column not in company_df.columns for column in columns):
        return pd.DataFrame(columns=["financial_year", *columns])

    data = company_df[["financial_year", *columns]].copy()
    for column in columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    data = data.dropna(subset=["financial_year", *columns])
    data = data.drop_duplicates("financial_year", keep="last")
    data = data.sort_values("financial_year", kind="stable")
    if data.empty:
        return data

    selected_indices = [data.index[-1]]
    expected_year = int(data.iloc[-1]["financial_year"]) - 1

    for index in reversed(data.index[:-1].tolist()):
        year = int(data.loc[index, "financial_year"])
        if year == expected_year:
            selected_indices.append(index)
            expected_year -= 1
        elif year < expected_year:
            break

    return data.loc[list(reversed(selected_indices))].reset_index(drop=True)


def trailing_rule_run(
    company_df: pd.DataFrame,
    column: str,
    predicate: Callable[[float], bool],
) -> pd.DataFrame:
    """Return the latest consecutive run for which a predicate is true."""

    consecutive = trailing_consecutive_rows(company_df, [column])
    if consecutive.empty:
        return consecutive

    selected: list[int] = []
    for index in reversed(consecutive.index.tolist()):
        value = float(consecutive.loc[index, column])
        if predicate(value):
            selected.append(index)
        else:
            break

    if not selected:
        return consecutive.iloc[0:0]
    return consecutive.loc[list(reversed(selected))].reset_index(drop=True)


def metric_completeness(
    company_df: pd.DataFrame,
    columns: Sequence[str],
    years: int = 5,
) -> float:
    """Calculate recent data completeness as a value between 0 and 1."""

    if company_df.empty:
        return 0.0

    recent = company_df.sort_values("financial_year", kind="stable").tail(years)
    available = 0
    possible = max(1, len(recent) * len(columns))

    for column in columns:
        if column not in recent.columns:
            continue
        available += int(pd.to_numeric(recent[column], errors="coerce").notna().sum())

    return max(0.0, min(1.0, available / possible))


def compute_cagr_from_history(
    company_df: pd.DataFrame,
    column: str,
    years: int = 5,
) -> float | None:
    """Compute CAGR using the latest value and the exact year N years earlier."""

    if company_df.empty or column not in company_df.columns:
        return None

    data = company_df[["financial_year", column]].copy()
    data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna().drop_duplicates("financial_year", keep="last")
    data = data.sort_values("financial_year", kind="stable")
    if data.empty:
        return None

    latest = data.iloc[-1]
    end_year = int(latest["financial_year"])
    start_rows = data[data["financial_year"] == end_year - years]
    if start_rows.empty:
        return None

    start_value = float(start_rows.iloc[-1][column])
    end_value = float(latest[column])
    if start_value <= 0 or end_value <= 0:
        return None

    return ((end_value / start_value) ** (1.0 / years) - 1.0) * 100.0


def latest_cagr(
    company_df: pd.DataFrame,
    ratio_column: str,
    source_column: str,
) -> float | None:
    """Use Ratio Engine CAGR first, then calculate it from financial history."""

    ratio_value = latest_numeric(company_df, ratio_column)
    if ratio_value is not None:
        return ratio_value
    return compute_cagr_from_history(company_df, source_column, years=5)


def strictly_increasing(values: Iterable[float]) -> bool:
    """Return True when every value is greater than the previous value."""

    sequence = list(values)
    return len(sequence) >= 2 and all(
        current > previous for previous, current in zip(sequence, sequence[1:])
    )


def strictly_decreasing(values: Iterable[float]) -> bool:
    """Return True when every value is lower than the previous value."""

    sequence = list(values)
    return len(sequence) >= 2 and all(
        current < previous for previous, current in zip(sequence, sequence[1:])
    )


def pct_change(start: float, end: float) -> float:
    """Return absolute percentage change, safely handling a zero base."""

    denominator = abs(start)
    if denominator < 1e-9:
        return 0.0
    return (end - start) / denominator * 100.0


def score_signal(
    *,
    strength: float,
    years_available: int,
    required_years: int,
    completeness: float,
    repeated_years: int = 0,
    base: float = 61.0,
    cap: float = 100.0,
) -> float:
    """Deterministically score threshold distance, history, and completeness."""

    strength_component = 20.0 * max(0.0, min(1.0, strength))
    history_component = 7.0 * max(
        0.0,
        min(1.0, years_available / max(1, required_years)),
    )
    repeated_component = min(7.0, max(0, repeated_years) * 2.0)
    completeness_component = 5.0 * max(0.0, min(1.0, completeness))

    return min(
        cap,
        base
        + strength_component
        + history_component
        + repeated_component
        + completeness_component,
    )


# ============================================================================
# PRO RULES: PRO_01 TO PRO_12
# ============================================================================


def evaluate_pro_rule_1(company_df: pd.DataFrame) -> RuleResult:
    """PRO_01: ROE above 20% for at least three consecutive years."""

    rule_id = "PRO_01"
    run = trailing_rule_run(company_df, "roe", lambda value: value > 20.0)
    if len(run) < 3:
        return no_match(rule_id, "pro")

    minimum_roe = float(run["roe"].min())
    confidence = score_signal(
        strength=(minimum_roe - 20.0) / 10.0,
        years_available=len(run),
        required_years=3,
        repeated_years=len(run) - 3,
        completeness=metric_completeness(company_df, ["roe"]),
    )
    return matched_result(
        rule_id,
        "pro",
        "Consistently high return on equity above 20% demonstrates exceptional capital efficiency",
        confidence,
    )


def evaluate_pro_rule_2(company_df: pd.DataFrame) -> RuleResult:
    """PRO_02: Positive free cash flow for five consecutive years."""

    rule_id = "PRO_02"
    run = trailing_rule_run(company_df, "fcf", lambda value: value > 0.0)
    if len(run) < 5:
        return no_match(rule_id, "pro")

    recent = company_df.sort_values("financial_year", kind="stable").tail(5)
    fcf = pd.to_numeric(recent["fcf"], errors="coerce")
    sales = pd.to_numeric(recent["sales"], errors="coerce").replace(0, pd.NA)
    fcf_margin = (fcf / sales * 100.0).median(skipna=True)
    strength = 0.25 if pd.isna(fcf_margin) else max(0.0, float(fcf_margin)) / 10.0

    confidence = score_signal(
        strength=strength,
        years_available=len(run),
        required_years=5,
        repeated_years=len(run) - 5,
        completeness=metric_completeness(company_df, ["fcf", "sales"]),
    )
    return matched_result(
        rule_id,
        "pro",
        "Strong free cash flow generation over 5 years signals healthy business fundamentals",
        confidence,
    )


def evaluate_pro_rule_3(company_df: pd.DataFrame) -> RuleResult:
    """PRO_03: Debt-to-equity is effectively zero in the latest year."""

    rule_id = "PRO_03"
    debt_equity = latest_numeric(company_df, "debt_equity")
    if debt_equity is None or abs(debt_equity) > 0.01:
        return no_match(rule_id, "pro")

    debt_free_run = trailing_rule_run(
        company_df,
        "debt_equity",
        lambda value: abs(value) <= 0.01,
    )
    confidence = score_signal(
        strength=1.0,
        years_available=max(1, len(debt_free_run)),
        required_years=1,
        repeated_years=max(0, len(debt_free_run) - 1),
        completeness=metric_completeness(company_df, ["debt_equity"]),
        base=66.0,
    )
    return matched_result(
        rule_id,
        "pro",
        "Debt-free balance sheet provides financial flexibility and eliminates interest burden",
        confidence,
    )


def evaluate_pro_rule_4(company_df: pd.DataFrame) -> RuleResult:
    """PRO_04: Revenue CAGR is above 15% over five years."""

    rule_id = "PRO_04"
    value = latest_cagr(company_df, "revenue_cagr_5yr", "sales")
    if value is None or value <= 15.0:
        return no_match(rule_id, "pro")

    confidence = score_signal(
        strength=(value - 15.0) / 15.0,
        years_available=min(6, int(pd.to_numeric(company_df["sales"], errors="coerce").notna().sum())),
        required_years=6,
        completeness=metric_completeness(company_df, ["sales"], years=6),
    )
    return matched_result(
        rule_id,
        "pro",
        "Revenue growing at above 15% CAGR over 5 years reflects strong business momentum",
        confidence,
    )


def evaluate_pro_rule_5(company_df: pd.DataFrame) -> RuleResult:
    """PRO_05: Latest operating margin is above 25%."""

    rule_id = "PRO_05"
    value = latest_numeric(company_df, "opm")
    if value is None or value <= 25.0:
        return no_match(rule_id, "pro")

    confidence = score_signal(
        strength=(value - 25.0) / 15.0,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["opm"]),
    )
    return matched_result(
        rule_id,
        "pro",
        "Operating profit margin above 25% indicates strong pricing power and cost discipline",
        confidence,
    )


def evaluate_pro_rule_6(company_df: pd.DataFrame) -> RuleResult:
    """PRO_06: PAT CAGR is above 20% over five years."""

    rule_id = "PRO_06"
    value = latest_cagr(company_df, "pat_cagr_5yr", "net_profit")
    if value is None or value <= 20.0:
        return no_match(rule_id, "pro")

    confidence = score_signal(
        strength=(value - 20.0) / 20.0,
        years_available=min(6, int(pd.to_numeric(company_df["net_profit"], errors="coerce").notna().sum())),
        required_years=6,
        completeness=metric_completeness(company_df, ["net_profit"], years=6),
    )
    return matched_result(
        rule_id,
        "pro",
        "Net profit compounding at above 20% over 5 years creates significant shareholder value",
        confidence,
    )


def evaluate_pro_rule_7(company_df: pd.DataFrame) -> RuleResult:
    """PRO_07: Interest coverage above 10x or a debt-free balance sheet."""

    rule_id = "PRO_07"
    debt_equity = latest_numeric(company_df, "debt_equity")
    interest_coverage = latest_numeric(company_df, "interest_coverage")
    icr_label = latest_text(company_df, "icr_label").lower()

    debt_free = (
        (debt_equity is not None and abs(debt_equity) <= 0.01)
        or "debt free" in icr_label
        or "debt-free" in icr_label
    )
    high_coverage = interest_coverage is not None and interest_coverage > 10.0
    if not debt_free and not high_coverage:
        return no_match(rule_id, "pro")

    if debt_free:
        strength = 1.0
        base = 68.0
    else:
        strength = (float(interest_coverage) - 10.0) / 20.0
        base = 61.0

    confidence = score_signal(
        strength=strength,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(
            company_df,
            ["debt_equity", "interest_coverage"],
        ),
        base=base,
    )
    return matched_result(
        rule_id,
        "pro",
        "Very high interest coverage ratio reflects negligible financial stress from debt servicing",
        confidence,
    )


def evaluate_pro_rule_8(company_df: pd.DataFrame) -> RuleResult:
    """PRO_08: Dividend yield above 2% backed by positive FCF."""

    rule_id = "PRO_08"
    dividend_yield = latest_numeric(company_df, "dividend_yield")
    fcf = latest_numeric(company_df, "fcf")
    if dividend_yield is None or fcf is None or dividend_yield <= 2.0 or fcf <= 0.0:
        return no_match(rule_id, "pro")

    yield_run = trailing_rule_run(
        company_df,
        "dividend_yield",
        lambda value: value > 2.0,
    )
    confidence = score_signal(
        strength=(dividend_yield - 2.0) / 3.0,
        years_available=max(1, len(yield_run)),
        required_years=1,
        repeated_years=max(0, len(yield_run) - 1),
        completeness=metric_completeness(company_df, ["dividend_yield", "fcf"]),
    )
    return matched_result(
        rule_id,
        "pro",
        "Consistent dividend yield above 2% backed by positive free cash flow",
        confidence,
    )


def evaluate_pro_rule_9(company_df: pd.DataFrame) -> RuleResult:
    """PRO_09: EPS CAGR is above 15% over five years."""

    rule_id = "PRO_09"
    value = latest_cagr(company_df, "eps_cagr_5yr", "eps")
    if value is None or value <= 15.0:
        return no_match(rule_id, "pro")

    confidence = score_signal(
        strength=(value - 15.0) / 15.0,
        years_available=min(6, int(pd.to_numeric(company_df["eps"], errors="coerce").notna().sum())),
        required_years=6,
        completeness=metric_completeness(company_df, ["eps"], years=6),
    )
    return matched_result(
        rule_id,
        "pro",
        "Earnings per share growing above 15% CAGR indicates strong earnings quality and compounding",
        confidence,
    )


def evaluate_pro_rule_10(company_df: pd.DataFrame) -> RuleResult:
    """PRO_10: ROE improves across the latest three consecutive years."""

    rule_id = "PRO_10"
    window = trailing_consecutive_rows(company_df, ["roe"])
    if len(window) < 3:
        return no_match(rule_id, "pro")

    recent = window.tail(3)
    values = recent["roe"].astype(float).tolist()
    if not strictly_increasing(values):
        return no_match(rule_id, "pro")

    improvement = values[-1] - values[0]
    confidence = score_signal(
        strength=improvement / 10.0,
        years_available=3,
        required_years=3,
        completeness=metric_completeness(company_df, ["roe"]),
    )
    return matched_result(
        rule_id,
        "pro",
        "Return on equity improving for 3 consecutive years shows strengthening business quality",
        confidence,
    )


def evaluate_pro_rule_11(company_df: pd.DataFrame) -> RuleResult:
    """PRO_11: Profit CAGR exceeds revenue CAGR, indicating operating leverage.

    The task's descriptive text says revenue is growing slower than profits, so
    the economically consistent comparison used here is PAT CAGR > revenue CAGR.
    """

    rule_id = "PRO_11"
    revenue_cagr = latest_cagr(company_df, "revenue_cagr_5yr", "sales")
    pat_cagr = latest_cagr(company_df, "pat_cagr_5yr", "net_profit")
    if revenue_cagr is None or pat_cagr is None or pat_cagr <= revenue_cagr:
        return no_match(rule_id, "pro")

    gap = pat_cagr - revenue_cagr
    confidence = score_signal(
        strength=gap / 10.0,
        years_available=6,
        required_years=6,
        completeness=metric_completeness(
            company_df,
            ["sales", "net_profit"],
            years=6,
        ),
    )
    return matched_result(
        rule_id,
        "pro",
        "Revenue growing slower than profits shows improving operating leverage and scale benefits",
        confidence,
    )


def evaluate_pro_rule_12(company_df: pd.DataFrame) -> RuleResult:
    """PRO_12: Assets rise while borrowings decline over three years."""

    rule_id = "PRO_12"
    window = trailing_consecutive_rows(company_df, ["total_assets", "borrowings"])
    if len(window) < 3:
        return no_match(rule_id, "pro")

    recent = window.tail(3)
    assets = recent["total_assets"].astype(float).tolist()
    borrowings = recent["borrowings"].astype(float).tolist()
    if not strictly_increasing(assets) or not strictly_decreasing(borrowings):
        return no_match(rule_id, "pro")

    asset_growth = max(0.0, pct_change(assets[0], assets[-1]))
    debt_decline = max(0.0, -pct_change(borrowings[0], borrowings[-1]))
    confidence = score_signal(
        strength=(asset_growth + debt_decline) / 40.0,
        years_available=3,
        required_years=3,
        completeness=metric_completeness(
            company_df,
            ["total_assets", "borrowings"],
        ),
    )
    return matched_result(
        rule_id,
        "pro",
        "Growing asset base funded by internal accruals reflects self-sustaining growth",
        confidence,
    )


# ============================================================================
# CON RULES: CON_01 TO CON_12
# ============================================================================


def evaluate_con_rule_1(company_df: pd.DataFrame) -> RuleResult:
    """CON_01: D/E above 2.0, only for non-financial companies."""

    rule_id = "CON_01"
    row = latest_row(company_df)
    if row is None:
        return no_match(rule_id, "con")

    if is_financial_sector(row.get("broad_sector"), row.get("sub_sector")):
        return no_match(rule_id, "con")

    debt_equity = latest_numeric(company_df, "debt_equity")
    if debt_equity is None or debt_equity <= 2.0:
        return no_match(rule_id, "con")

    confidence = score_signal(
        strength=(debt_equity - 2.0) / 3.0,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["debt_equity"]),
    )
    return matched_result(
        rule_id,
        "con",
        f"Debt-to-equity ratio of {debt_equity:.2f} is elevated for a non-financial company and warrants monitoring",
        confidence,
    )


def evaluate_con_rule_2(company_df: pd.DataFrame) -> RuleResult:
    """CON_02: Negative FCF for three consecutive years."""

    rule_id = "CON_02"
    run = trailing_rule_run(company_df, "fcf", lambda value: value < 0.0)
    if len(run) < 3:
        return no_match(rule_id, "con")

    recent = company_df.sort_values("financial_year", kind="stable").tail(3)
    fcf = pd.to_numeric(recent["fcf"], errors="coerce")
    sales = pd.to_numeric(recent["sales"], errors="coerce").replace(0, pd.NA)
    negative_margin = (fcf.abs() / sales.abs() * 100.0).median(skipna=True)
    strength = 0.25 if pd.isna(negative_margin) else float(negative_margin) / 10.0

    confidence = score_signal(
        strength=strength,
        years_available=len(run),
        required_years=3,
        repeated_years=len(run) - 3,
        completeness=metric_completeness(company_df, ["fcf", "sales"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Free cash flow negative for 3 consecutive years raises concern about cash generation quality",
        confidence,
    )


def evaluate_con_rule_3(company_df: pd.DataFrame) -> RuleResult:
    """CON_03: OPM declines across the latest three consecutive years."""

    rule_id = "CON_03"
    window = trailing_consecutive_rows(company_df, ["opm"])
    if len(window) < 3:
        return no_match(rule_id, "con")

    values = window.tail(3)["opm"].astype(float).tolist()
    if not strictly_decreasing(values):
        return no_match(rule_id, "con")

    decline = values[0] - values[-1]
    confidence = score_signal(
        strength=decline / 8.0,
        years_available=3,
        required_years=3,
        completeness=metric_completeness(company_df, ["opm"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Operating margins declining for 3 consecutive years suggest pricing or cost pressure",
        confidence,
    )


def evaluate_con_rule_4(company_df: pd.DataFrame) -> RuleResult:
    """CON_04: Latest net profit is negative."""

    rule_id = "CON_04"
    net_profit = latest_numeric(company_df, "net_profit")
    if net_profit is None or net_profit >= 0.0:
        return no_match(rule_id, "con")

    sales = latest_numeric(company_df, "sales")
    loss_margin = 0.0
    if sales is not None and abs(sales) > 1e-9:
        loss_margin = abs(net_profit / sales * 100.0)

    confidence = score_signal(
        strength=loss_margin / 15.0,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["net_profit", "sales"]),
        base=65.0,
    )
    return matched_result(
        rule_id,
        "con",
        "Company reported a net loss in the most recent financial year",
        confidence,
    )


def evaluate_con_rule_5(company_df: pd.DataFrame) -> RuleResult:
    """CON_05: Revenue declines in each of the latest two year-on-year periods."""

    rule_id = "CON_05"
    window = trailing_consecutive_rows(company_df, ["sales"])
    if len(window) < 3:
        return no_match(rule_id, "con")

    values = window.tail(3)["sales"].astype(float).tolist()
    if not strictly_decreasing(values):
        return no_match(rule_id, "con")

    decline = max(0.0, -pct_change(values[0], values[-1]))
    confidence = score_signal(
        strength=decline / 20.0,
        years_available=3,
        required_years=3,
        completeness=metric_completeness(company_df, ["sales"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Revenue contraction over 2 consecutive years indicates demand weakness or market share loss",
        confidence,
    )


def evaluate_con_rule_6(company_df: pd.DataFrame) -> RuleResult:
    """CON_06: Interest coverage below 1.5x."""

    rule_id = "CON_06"
    interest_coverage = latest_numeric(company_df, "interest_coverage")
    if interest_coverage is None or interest_coverage >= 1.5:
        return no_match(rule_id, "con")

    confidence = score_signal(
        strength=(1.5 - interest_coverage) / 1.5,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["interest_coverage"]),
        base=64.0,
    )
    return matched_result(
        rule_id,
        "con",
        "Interest coverage ratio below 1.5x indicates the company is at risk of not meeting its debt obligations",
        confidence,
    )


def evaluate_con_rule_7(company_df: pd.DataFrame) -> RuleResult:
    """CON_07: Dividend payout is above 100%."""

    rule_id = "CON_07"
    payout = latest_numeric(company_df, "dividend_payout")
    if payout is None or payout <= 100.0:
        return no_match(rule_id, "con")

    confidence = score_signal(
        strength=(payout - 100.0) / 100.0,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["dividend_payout"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Dividend payout ratio above 100% means the company is paying dividends from reserves, which is unsustainable",
        confidence,
    )


def evaluate_con_rule_8(company_df: pd.DataFrame) -> RuleResult:
    """CON_08: D/E rises across the latest three consecutive years."""

    rule_id = "CON_08"
    window = trailing_consecutive_rows(company_df, ["debt_equity"])
    if len(window) < 3:
        return no_match(rule_id, "con")

    values = window.tail(3)["debt_equity"].astype(float).tolist()
    if not strictly_increasing(values):
        return no_match(rule_id, "con")

    increase = values[-1] - values[0]
    confidence = score_signal(
        strength=increase / 1.5,
        years_available=3,
        required_years=3,
        completeness=metric_completeness(company_df, ["debt_equity"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Rising debt-to-equity ratio over 3 years suggests increasing financial leverage risk",
        confidence,
    )


def evaluate_con_rule_9(company_df: pd.DataFrame) -> RuleResult:
    """CON_09: EPS declines across the latest three consecutive years."""

    rule_id = "CON_09"
    window = trailing_consecutive_rows(company_df, ["eps"])
    if len(window) < 3:
        return no_match(rule_id, "con")

    values = window.tail(3)["eps"].astype(float).tolist()
    if not strictly_decreasing(values):
        return no_match(rule_id, "con")

    decline = max(0.0, -pct_change(values[0], values[-1]))
    confidence = score_signal(
        strength=decline / 30.0,
        years_available=3,
        required_years=3,
        completeness=metric_completeness(company_df, ["eps"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Earnings per share declining for 3 consecutive years reflects deteriorating profitability",
        confidence,
    )


def evaluate_con_rule_10(company_df: pd.DataFrame) -> RuleResult:
    """CON_10: Latest ROCE is below 10%."""

    rule_id = "CON_10"
    roce = latest_numeric(company_df, "roce")
    if roce is None or roce >= 10.0:
        return no_match(rule_id, "con")

    confidence = score_signal(
        strength=(10.0 - roce) / 10.0,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["roce"]),
    )
    return matched_result(
        rule_id,
        "con",
        "Return on capital employed below 10% suggests the business is not generating sufficient returns on invested capital",
        confidence,
    )


def evaluate_con_rule_11(company_df: pd.DataFrame) -> RuleResult:
    """CON_11: Net debt exceeds three times EBITDA."""

    rule_id = "CON_11"
    net_debt = latest_numeric(company_df, "net_debt")
    ebitda = latest_numeric(company_df, "ebitda")
    if net_debt is None or ebitda is None or ebitda <= 0.0:
        return no_match(rule_id, "con")

    multiple = net_debt / ebitda
    if multiple <= 3.0:
        return no_match(rule_id, "con")

    latest = latest_row(company_df)
    proxy = bool(latest.get("net_debt_is_proxy")) if latest is not None else True
    confidence = score_signal(
        strength=(multiple - 3.0) / 4.0,
        years_available=1,
        required_years=1,
        completeness=metric_completeness(company_df, ["net_debt", "ebitda"]),
        cap=78.0 if proxy else 100.0,
    )
    return matched_result(
        rule_id,
        "con",
        "Net debt exceeding 3 times EBITDA is a high leverage ratio and limits financial flexibility",
        confidence,
    )


def evaluate_con_rule_12(company_df: pd.DataFrame) -> RuleResult:
    """CON_12: Five-year revenue CAGR is below 5%."""

    rule_id = "CON_12"
    value = latest_cagr(company_df, "revenue_cagr_5yr", "sales")
    if value is None or value >= 5.0:
        return no_match(rule_id, "con")

    confidence = score_signal(
        strength=(5.0 - value) / 10.0,
        years_available=min(6, int(pd.to_numeric(company_df["sales"], errors="coerce").notna().sum())),
        required_years=6,
        completeness=metric_completeness(company_df, ["sales"], years=6),
    )
    return matched_result(
        rule_id,
        "con",
        "Revenue growing at below 5% over 5 years lags inflation and suggests limited business momentum",
        confidence,
    )


PRO_RULES: list[RuleFunction] = [
    evaluate_pro_rule_1,
    evaluate_pro_rule_2,
    evaluate_pro_rule_3,
    evaluate_pro_rule_4,
    evaluate_pro_rule_5,
    evaluate_pro_rule_6,
    evaluate_pro_rule_7,
    evaluate_pro_rule_8,
    evaluate_pro_rule_9,
    evaluate_pro_rule_10,
    evaluate_pro_rule_11,
    evaluate_pro_rule_12,
]

CON_RULES: list[RuleFunction] = [
    evaluate_con_rule_1,
    evaluate_con_rule_2,
    evaluate_con_rule_3,
    evaluate_con_rule_4,
    evaluate_con_rule_5,
    evaluate_con_rule_6,
    evaluate_con_rule_7,
    evaluate_con_rule_8,
    evaluate_con_rule_9,
    evaluate_con_rule_10,
    evaluate_con_rule_11,
    evaluate_con_rule_12,
]


# ============================================================================
# GENERATION AND VALIDATION
# ============================================================================


def generate_pros_cons(
    companies: pd.DataFrame,
    history: pd.DataFrame,
) -> pd.DataFrame:
    """Evaluate all 24 rules for every company."""

    generated_rows: list[dict[str, object]] = []

    for company in companies.itertuples(index=False):
        company_id = normalise_company_id(company.company_id)
        company_df = history[history["company_id"] == company_id].copy()
        company_df = company_df.sort_values("financial_year", kind="stable")

        for evaluator in [*PRO_RULES, *CON_RULES]:
            result = evaluator(company_df)
            if not result.matched:
                continue
            if result.confidence_pct <= MIN_CONFIDENCE_EXCLUSIVE:
                continue

            generated_rows.append(
                {
                    "company_id": company_id,
                    "type": result.rule_type,
                    "rule_id": result.rule_id,
                    "text": result.text,
                    "confidence_pct": result.confidence_pct,
                }
            )

    generated = pd.DataFrame(generated_rows, columns=OUTPUT_COLUMNS)
    if generated.empty:
        return generated

    generated = generated.drop_duplicates(
        subset=["company_id", "type", "rule_id"],
        keep="first",
    )
    type_order = pd.Categorical(
        generated["type"],
        categories=["pro", "con"],
        ordered=True,
    )
    generated = generated.assign(_type_order=type_order).sort_values(
        ["company_id", "_type_order", "rule_id"],
        kind="stable",
    )
    return generated.drop(columns="_type_order").reset_index(drop=True)



def coverage_pro_result(company_df: pd.DataFrame) -> RuleResult:
    """Return one moderate-confidence pro supported by reported metrics.

    This function runs only when none of PRO_01..PRO_12 matched. It does not
    create a generic positive statement. The selected observation must still
    be supported by an explicit numeric threshold and receives a deterministic
    confidence score.
    """

    if company_df.empty:
        return no_match("PRO_03", "pro")

    debt_equity = latest_numeric(company_df, "debt_equity")
    if debt_equity is not None and 0.0 <= debt_equity < 0.50:
        run = trailing_rule_run(
            company_df,
            "debt_equity",
            lambda value: 0.0 <= value < 0.50,
        )
        confidence = score_signal(
            strength=(0.50 - debt_equity) / 0.50,
            years_available=max(1, len(run)),
            required_years=1,
            repeated_years=max(0, len(run) - 1),
            completeness=metric_completeness(company_df, ["debt_equity"]),
            base=62.0,
            cap=85.0,
        )
        return matched_result(
            "PRO_03",
            "pro",
            f"Conservative debt-to-equity ratio of {debt_equity:.2f}x provides balance-sheet flexibility",
            confidence,
        )

    fcf = latest_numeric(company_df, "fcf")
    if fcf is not None and fcf > 0.0:
        confidence = score_signal(
            strength=min(1.0, abs(fcf) / max(1.0, abs(latest_numeric(company_df, "sales") or fcf)) * 10.0),
            years_available=1,
            required_years=1,
            completeness=metric_completeness(company_df, ["fcf", "sales"]),
            base=61.0,
            cap=78.0,
        )
        return matched_result(
            "PRO_02",
            "pro",
            "Positive free cash flow in the latest financial year supports near-term financial flexibility",
            confidence,
        )

    opm = latest_numeric(company_df, "opm")
    if opm is not None and opm > 15.0:
        confidence = score_signal(
            strength=(opm - 15.0) / 15.0,
            years_available=1,
            required_years=1,
            completeness=metric_completeness(company_df, ["opm"]),
            base=61.0,
            cap=78.0,
        )
        return matched_result(
            "PRO_05",
            "pro",
            f"Operating margin of {opm:.1f}% indicates a meaningful operating profit buffer",
            confidence,
        )

    dividend_yield = latest_numeric(company_df, "dividend_yield")
    payout = latest_numeric(company_df, "dividend_payout")
    if (
        dividend_yield is not None
        and dividend_yield > 2.0
        and (payout is None or payout <= 100.0)
    ):
        confidence = score_signal(
            strength=(dividend_yield - 2.0) / 3.0,
            years_available=1,
            required_years=1,
            completeness=metric_completeness(
                company_df,
                ["dividend_yield", "dividend_payout"],
            ),
            base=61.0,
            cap=78.0,
        )
        return matched_result(
            "PRO_08",
            "pro",
            f"Dividend yield of {dividend_yield:.2f}% provides a measurable shareholder return",
            confidence,
        )

    return no_match("PRO_03", "pro")


def coverage_con_result(company_df: pd.DataFrame) -> RuleResult:
    """Return one moderate-confidence watchpoint supported by reported data.

    This function runs only when none of CON_01..CON_12 matched. It selects the
    first applicable evidence-based watchpoint. Financial companies remain
    excluded from non-financial leverage rules.
    """

    if company_df.empty:
        return no_match("CON_12", "con")

    row = latest_row(company_df)
    if row is None:
        return no_match("CON_12", "con")

    financial = is_financial_sector(
        row.get("broad_sector"),
        row.get("sub_sector"),
    )

    fcf = latest_numeric(company_df, "fcf")
    net_profit = latest_numeric(company_df, "net_profit")
    if not financial and fcf is not None and net_profit is not None and net_profit > 0.0:
        conversion = fcf / net_profit * 100.0
        if conversion < 60.0:
            confidence = score_signal(
                strength=(60.0 - conversion) / 60.0,
                years_available=1,
                required_years=1,
                completeness=metric_completeness(company_df, ["fcf", "net_profit"]),
                base=61.0,
                cap=82.0,
            )
            if fcf < 0.0:
                text = "Latest-year free cash flow is negative despite reported accounting profit"
            else:
                text = (
                    f"Free cash flow converted only {conversion:.1f}% of latest net profit, "
                    "which warrants monitoring"
                )
            return matched_result("CON_02", "con", text, confidence)

    payout = latest_numeric(company_df, "dividend_payout")
    if payout is not None and 75.0 <= payout <= 100.0:
        confidence = score_signal(
            strength=(payout - 75.0) / 25.0,
            years_available=1,
            required_years=1,
            completeness=metric_completeness(company_df, ["dividend_payout"]),
            base=61.0,
            cap=82.0,
        )
        return matched_result(
            "CON_07",
            "con",
            f"Dividend payout of {payout:.1f}% leaves a comparatively smaller earnings buffer for reinvestment",
            confidence,
        )

    roe = latest_numeric(company_df, "roe")
    roce = latest_numeric(company_df, "roce")
    weak_return = False
    return_value: float | None = None
    return_name = "return on equity"

    if financial:
        if roe is not None and roe < 15.0:
            weak_return = True
            return_value = roe
    else:
        candidates = [
            ("return on equity", roe),
            ("return on capital employed", roce),
        ]
        valid_candidates = [(name, value) for name, value in candidates if value is not None]
        if valid_candidates:
            return_name, return_value = min(valid_candidates, key=lambda item: float(item[1]))
            weak_return = float(return_value) < 15.0

    if weak_return and return_value is not None:
        confidence = score_signal(
            strength=(15.0 - float(return_value)) / 15.0,
            years_available=1,
            required_years=1,
            completeness=metric_completeness(company_df, ["roe", "roce"]),
            base=61.0,
            cap=82.0,
        )
        return matched_result(
            "CON_10",
            "con",
            f"Latest {return_name} of {float(return_value):.1f}% is below a 15% quality benchmark",
            confidence,
        )

    revenue_cagr = latest_cagr(company_df, "revenue_cagr_5yr", "sales")
    pat_cagr = latest_cagr(company_df, "pat_cagr_5yr", "net_profit")
    if (
        revenue_cagr is not None
        and pat_cagr is not None
        and pat_cagr + 2.0 < revenue_cagr
    ):
        gap = revenue_cagr - pat_cagr
        confidence = score_signal(
            strength=gap / 12.0,
            years_available=6,
            required_years=6,
            completeness=metric_completeness(
                company_df,
                ["sales", "net_profit"],
                years=6,
            ),
            base=61.0,
            cap=84.0,
        )
        return matched_result(
            "CON_09",
            "con",
            f"Five-year profit growth trails revenue growth by {gap:.1f} percentage points",
            confidence,
        )

    net_debt = latest_numeric(company_df, "net_debt")
    ebitda = latest_numeric(company_df, "ebitda")
    if (
        not financial
        and net_debt is not None
        and ebitda is not None
        and ebitda > 0.0
    ):
        multiple = net_debt / ebitda
        if multiple > 1.50:
            confidence = score_signal(
                strength=(multiple - 1.50) / 2.0,
                years_available=1,
                required_years=1,
                completeness=metric_completeness(company_df, ["net_debt", "ebitda"]),
                base=61.0,
                cap=82.0,
            )
            return matched_result(
                "CON_11",
                "con",
                f"Net debt of {multiple:.2f} times EBITDA is an elevated leverage watchpoint",
                confidence,
            )

    if revenue_cagr is not None and revenue_cagr < 12.0:
        confidence = score_signal(
            strength=(12.0 - revenue_cagr) / 12.0,
            years_available=min(
                6,
                int(pd.to_numeric(company_df["sales"], errors="coerce").notna().sum()),
            ),
            required_years=6,
            completeness=metric_completeness(company_df, ["sales"], years=6),
            base=61.0,
            cap=82.0,
        )
        return matched_result(
            "CON_12",
            "con",
            f"Five-year revenue CAGR of {revenue_cagr:.1f}% is below a 12% growth benchmark",
            confidence,
        )

    dividend_yield = latest_numeric(company_df, "dividend_yield")
    if (
        dividend_yield is not None
        and payout is not None
        and dividend_yield < 1.0
        and payout < 10.0
    ):
        confidence = score_signal(
            strength=((1.0 - dividend_yield) + (10.0 - payout) / 10.0) / 2.0,
            years_available=1,
            required_years=1,
            completeness=metric_completeness(
                company_df,
                ["dividend_yield", "dividend_payout"],
            ),
            base=61.0,
            cap=80.0,
        )
        return matched_result(
            "CON_07",
            "con",
            f"Dividend yield of {dividend_yield:.2f}% and payout of {payout:.1f}% provide limited income support",
            confidence,
        )

    opm = latest_numeric(company_df, "opm")
    if opm is not None and opm < 15.0:
        confidence = score_signal(
            strength=(15.0 - opm) / 15.0,
            years_available=1,
            required_years=1,
            completeness=metric_completeness(company_df, ["opm"]),
            base=61.0,
            cap=80.0,
        )
        return matched_result(
            "CON_03",
            "con",
            f"Latest operating margin of {opm:.1f}% provides a relatively limited operating buffer",
            confidence,
        )

    return no_match("CON_12", "con")


def add_evidence_based_coverage(
    companies: pd.DataFrame,
    history: pd.DataFrame,
    generated: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Add a real-metric observation only for a missing pro or con type."""

    rows = generated.to_dict("records") if not generated.empty else []
    added_count = 0

    for company in companies.itertuples(index=False):
        company_id = normalise_company_id(company.company_id)
        company_df = history[history["company_id"] == company_id].copy()
        company_df = company_df.sort_values("financial_year", kind="stable")

        available_types = {
            str(row["type"])
            for row in rows
            if normalise_company_id(row["company_id"]) == company_id
        }

        for rule_type, evaluator in (
            ("pro", coverage_pro_result),
            ("con", coverage_con_result),
        ):
            if rule_type in available_types:
                continue

            result = evaluator(company_df)
            if not result.matched or result.confidence_pct <= MIN_CONFIDENCE_EXCLUSIVE:
                continue

            rows.append(
                {
                    "company_id": company_id,
                    "type": result.rule_type,
                    "rule_id": result.rule_id,
                    "text": result.text,
                    "confidence_pct": result.confidence_pct,
                }
            )
            available_types.add(rule_type)
            added_count += 1

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    if result.empty:
        return result, added_count

    result = result.drop_duplicates(
        subset=["company_id", "type", "rule_id"],
        keep="first",
    )
    type_order = pd.Categorical(
        result["type"],
        categories=["pro", "con"],
        ordered=True,
    )
    result = result.assign(_type_order=type_order).sort_values(
        ["company_id", "_type_order", "rule_id"],
        kind="stable",
    )
    return result.drop(columns="_type_order").reset_index(drop=True), added_count


def build_missing_coverage(
    companies: pd.DataFrame,
    history: pd.DataFrame,
    generated: pd.DataFrame,
) -> pd.DataFrame:
    """Identify companies without a qualifying pro or con, without inventing one."""

    if generated.empty:
        coverage = pd.DataFrame(columns=["company_id", "type"])
    else:
        coverage = generated[["company_id", "type"]].drop_duplicates()

    missing_rows: list[dict[str, object]] = []

    for company in companies.itertuples(index=False):
        company_id = normalise_company_id(company.company_id)
        company_history = history[history["company_id"] == company_id]
        available_types = set(
            coverage.loc[coverage["company_id"] == company_id, "type"].tolist()
        )

        if company_history.empty:
            base_reason = "No company-year financial history was available."
        else:
            populated_metrics = int(
                company_history[
                    [
                        "sales",
                        "net_profit",
                        "fcf",
                        "roe",
                        "roce",
                        "opm",
                        "eps",
                        "debt_equity",
                        "interest_coverage",
                    ]
                ].notna().sum().sum()
            )
            base_reason = (
                "Financial history exists, but no rule exceeded its threshold "
                f"with confidence above 60; populated metric cells={populated_metrics}."
            )

        for missing_type in ("pro", "con"):
            if missing_type in available_types:
                continue
            missing_rows.append(
                {
                    "company_id": company_id,
                    "company_name": getattr(company, "company_name", company_id),
                    "broad_sector": getattr(company, "broad_sector", pd.NA),
                    "sub_sector": getattr(company, "sub_sector", pd.NA),
                    "missing_type": missing_type,
                    "reason": base_reason,
                }
            )

    return pd.DataFrame(missing_rows, columns=MISSING_COVERAGE_COLUMNS)


def validate_generated_output(
    companies: pd.DataFrame,
    generated: pd.DataFrame,
) -> None:
    """Raise for structural output defects; coverage gaps are reported separately."""

    if list(generated.columns) != OUTPUT_COLUMNS:
        raise RuntimeError(
            f"Output columns are incorrect. Expected {OUTPUT_COLUMNS}; "
            f"found {generated.columns.tolist()}"
        )

    invalid_types = set(generated["type"].dropna().astype(str)) - {"pro", "con"}
    if invalid_types:
        raise RuntimeError(f"Invalid type values found: {sorted(invalid_types)}")

    invalid_rule_ids = generated[
        ~generated["rule_id"].astype(str).str.fullmatch(r"(?:PRO|CON)_\d{2}")
    ]
    if not invalid_rule_ids.empty:
        raise RuntimeError("Invalid rule IDs found in generated output.")

    invalid_confidence = generated[
        (pd.to_numeric(generated["confidence_pct"], errors="coerce") <= 60)
        | (pd.to_numeric(generated["confidence_pct"], errors="coerce") > 100)
    ]
    if not invalid_confidence.empty:
        raise RuntimeError("All saved confidence scores must be >60 and <=100.")

    unknown_companies = set(generated["company_id"]) - set(companies["company_id"])
    if unknown_companies:
        raise RuntimeError(
            f"Output contains unknown companies: {sorted(unknown_companies)}"
        )


def run_generator() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the complete Day 30 pros/cons pipeline."""

    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"Configured database was not found: {DATABASE_PATH}")

    print(f"Database: {DATABASE_PATH}")

    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        companies = load_companies_and_sectors(connection)
        history = build_financial_history(connection, companies)

    if history.empty:
        raise RuntimeError("No financial-history rows could be built from the database.")

    history = history.sort_values(
        ["company_id", "financial_year"],
        kind="stable",
    ).reset_index(drop=True)

    generated = generate_pros_cons(companies, history)
    generated, coverage_rows_added = add_evidence_based_coverage(
        companies,
        history,
        generated,
    )
    validate_generated_output(companies, generated)
    missing = build_missing_coverage(companies, history, generated)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated.reindex(columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
    missing.reindex(columns=MISSING_COVERAGE_COLUMNS).to_csv(
        MISSING_COVERAGE_PATH,
        index=False,
    )

    company_count = int(companies["company_id"].nunique())
    history_company_count = int(history["company_id"].nunique())
    pro_company_count = int(
        generated.loc[generated["type"] == "pro", "company_id"].nunique()
    )
    con_company_count = int(
        generated.loc[generated["type"] == "con", "company_id"].nunique()
    )

    sector_pairs = companies[["broad_sector", "sub_sector"]].drop_duplicates()
    detected_financial_sectors = sorted(
        {
            str(row.broad_sector)
            for row in sector_pairs.itertuples(index=False)
            if is_financial_sector(row.broad_sector, row.sub_sector)
            and not pd.isna(row.broad_sector)
        }
    )

    print()
    print("Automatic pros and cons generation completed")
    print("=" * 60)
    print(f"Companies table:          {company_count}")
    print(f"Companies with history:   {history_company_count}")
    print(f"Financial-history rows:   {len(history)}")
    print(f"Generated rule rows:      {len(generated)}")
    print(f"Coverage rows added:      {coverage_rows_added}")
    print(f"Companies with pros:      {pro_company_count}")
    print(f"Companies with cons:      {con_company_count}")
    print(f"Coverage gaps:            {len(missing)}")
    print(f"Main output:              {OUTPUT_PATH}")
    print(f"Coverage diagnostics:     {MISSING_COVERAGE_PATH}")
    print(
        "Financial sectors detected: "
        + (", ".join(detected_financial_sectors) if detected_financial_sectors else "None")
    )

    if company_count != EXPECTED_COMPANY_COUNT:
        print(
            f"Warning: expected {EXPECTED_COMPANY_COUNT} companies, "
            f"but the database contains {company_count}."
        )

    if missing.empty:
        print("Coverage check: PASS — every company has at least one pro and one con.")
    else:
        print(
            "Coverage check: REVIEW REQUIRED — some companies still have no "
            "evidence-supported pro or con. Inspect pros_cons_missing_coverage.csv."
        )

    return generated, missing


def main() -> None:
    """CLI entry point for ``python -m src.nlp.pros_cons_generator``."""

    run_generator()


if __name__ == "__main__":
    main()
