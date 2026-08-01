"""Sprint 5 Day 29: NLP parser for analysis.xlsx.

Run from the project root:

    python -m src.nlp.parser

Outputs:

    output/analysis_parsed.csv
    output/parse_failures.csv
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import get_settings


# ============================================================
# PATHS AND SETTINGS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SETTINGS = get_settings()

DATABASE_PATH = Path(SETTINGS.database_path)
OUTPUT_DIR = Path(SETTINGS.output_dir)


# ============================================================
# PARSER CONFIGURATION
# ============================================================

TARGET_FIELDS = (
    "compounded_sales_growth",
    "compounded_profit_growth",
    "stock_price_cagr",
    "roe",
)

REGEX_PATTERN = re.compile(
    r"(\d+)\s*Years?:?\s*([\d.]+)%",
    flags=re.IGNORECASE,
)

PARSED_COLUMNS = [
    "company_id",
    "metric_type",
    "period_years",
    "value_pct",
    "computed_value_pct",
    "divergence_pct",
    "manual_review_flag",
]

FAILURE_COLUMNS = [
    "company_id",
    "metric_type",
    "original_text",
    "failure_reason",
]

DIVERGENCE_THRESHOLD = 5.0


# ============================================================
# NORMALISATION HELPERS
# ============================================================

def normalise_column_name(value: Any) -> str:
    """Convert a column name to lowercase snake_case."""

    cleaned = re.sub(
        r"[^a-z0-9]+",
        "_",
        str(value).strip().lower(),
    )

    return cleaned.strip("_")


def normalise_company_id(value: Any) -> str:
    """Strip whitespace and uppercase a company ticker."""

    if pd.isna(value):
        return ""

    return str(value).strip().upper()


# ============================================================
# ANALYSIS FILE LOADING
# ============================================================

def find_analysis_file() -> Path:
    """Find analysis.xlsx inside the project.

    The documented location is data/raw/analysis.xlsx. Other common
    locations are checked as fallbacks.
    """

    candidates = [
        PROJECT_ROOT / "data" / "raw" / "analysis.xlsx",
        PROJECT_ROOT / "data" / "analysis.xlsx",
        PROJECT_ROOT / "analysis.xlsx",
    ]

    for path in candidates:
        if path.exists():
            return path

    matches = [
        path
        for path in PROJECT_ROOT.rglob("analysis.xlsx")
        if ".venv" not in path.parts
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
    ]

    if matches:
        return sorted(matches)[0]

    raise FileNotFoundError(
        "analysis.xlsx was not found.\n"
        "Expected location:\n"
        f"{PROJECT_ROOT / 'data' / 'raw' / 'analysis.xlsx'}"
    )


def read_analysis_file(path: Path) -> pd.DataFrame:
    """Read analysis.xlsx and validate the required fields.

    Core source workbooks use header=1 because the first row contains
    metadata. header=0 is tried only as a defensive fallback.
    """

    required_columns = {
        "company_id",
        *TARGET_FIELDS,
    }

    attempted_headers: list[tuple[int, list[str]]] = []

    for header_row in (1, 0):
        frame = pd.read_excel(
            path,
            header=header_row,
        )

        frame.columns = [
            normalise_column_name(column)
            for column in frame.columns
        ]

        attempted_headers.append(
            (
                header_row,
                frame.columns.tolist(),
            )
        )

        if required_columns.issubset(frame.columns):
            frame = frame[
                [
                    "company_id",
                    *TARGET_FIELDS,
                ]
            ].copy()

            frame["company_id"] = frame[
                "company_id"
            ].map(normalise_company_id)

            return frame

    raise ValueError(
        "analysis.xlsx is missing one or more required columns.\n"
        f"Required columns: {sorted(required_columns)}\n"
        f"Columns found: {attempted_headers}"
    )


# ============================================================
# TEXT PARSER
# ============================================================

def parse_analysis(
    analysis: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse all matching periods and percentage values.

    One source cell may contain several matches. Each match becomes a
    separate output row.
    """

    parsed_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []

    for row in analysis.itertuples(index=False):
        company_id = normalise_company_id(
            row.company_id
        )

        for metric_type in TARGET_FIELDS:
            raw_value = getattr(
                row,
                metric_type,
            )

            if pd.isna(raw_value):
                text = ""
            else:
                text = str(raw_value).strip()

            if not text:
                failure_rows.append(
                    {
                        "company_id": company_id,
                        "metric_type": metric_type,
                        "original_text": text,
                        "failure_reason": (
                            "missing_or_blank_text"
                        ),
                    }
                )

                continue

            matches = list(
                REGEX_PATTERN.finditer(text)
            )

            if not matches:
                failure_rows.append(
                    {
                        "company_id": company_id,
                        "metric_type": metric_type,
                        "original_text": text,
                        "failure_reason": (
                            "regex_no_match"
                        ),
                    }
                )

                continue

            for match in matches:
                try:
                    period_years = int(
                        match.group(1)
                    )

                    value_pct = float(
                        match.group(2)
                    )

                except ValueError:
                    failure_rows.append(
                        {
                            "company_id": company_id,
                            "metric_type": metric_type,
                            "original_text": (
                                match.group(0)
                            ),
                            "failure_reason": (
                                "invalid_numeric_value"
                            ),
                        }
                    )

                    continue

                parsed_rows.append(
                    {
                        "company_id": company_id,
                        "metric_type": metric_type,
                        "period_years": period_years,
                        "value_pct": value_pct,
                    }
                )

    parsed = pd.DataFrame(
        parsed_rows,
        columns=[
            "company_id",
            "metric_type",
            "period_years",
            "value_pct",
        ],
    )

    failures = pd.DataFrame(
        failure_rows,
        columns=FAILURE_COLUMNS,
    )

    return parsed, failures


# ============================================================
# RATIO ENGINE LOADING
# ============================================================

def load_latest_ratio_rows() -> pd.DataFrame:
    """Load the latest financial_ratios row for each company."""

    if not DATABASE_PATH.exists():
        print(
            "Warning: Ratio Engine database was not found."
        )
        print(
            f"Database path: {DATABASE_PATH}"
        )
        print(
            "CAGR cross-validation will be skipped."
        )

        return pd.DataFrame()

    with sqlite3.connect(
        DATABASE_PATH
    ) as connection:
        table_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'financial_ratios'
            LIMIT 1
            """
        ).fetchone()

        if table_exists is None:
            print(
                "Warning: financial_ratios table was not found."
            )
            print(
                "CAGR cross-validation will be skipped."
            )

            return pd.DataFrame()

        ratios = pd.read_sql_query(
            """
            SELECT *
            FROM financial_ratios
            """,
            connection,
        )

    ratios.columns = [
        normalise_column_name(column)
        for column in ratios.columns
    ]

    if ratios.empty:
        print(
            "Warning: financial_ratios table is empty."
        )

        return pd.DataFrame()

    if "company_id" not in ratios.columns:
        print(
            "Warning: company_id is missing from "
            "financial_ratios."
        )

        return pd.DataFrame()

    ratios["company_id"] = ratios[
        "company_id"
    ].map(normalise_company_id)

    if "year" in ratios.columns:
        ratios["_sort_year"] = pd.to_numeric(
            ratios["year"],
            errors="coerce",
        )
    else:
        ratios["_sort_year"] = 0

    if "id" in ratios.columns:
        ratios["_sort_id"] = pd.to_numeric(
            ratios["id"],
            errors="coerce",
        )
    else:
        ratios["_sort_id"] = 0

    ratios = ratios.sort_values(
        [
            "company_id",
            "_sort_year",
            "_sort_id",
        ],
        kind="stable",
    )

    latest = ratios.groupby(
        "company_id",
        sort=False,
    ).tail(1)

    latest = latest.drop(
        columns=[
            "_sort_year",
            "_sort_id",
        ],
        errors="ignore",
    )

    return latest.set_index(
        "company_id",
        drop=False,
    )


# ============================================================
# CAGR CROSS-VALIDATION
# ============================================================

def ratio_column_for(
    metric_type: str,
    period_years: int,
) -> str | None:
    """Return the matching Ratio Engine CAGR column."""

    if metric_type == "compounded_sales_growth":
        return (
            f"revenue_cagr_{period_years}yr"
        )

    if metric_type == "compounded_profit_growth":
        return (
            f"pat_cagr_{period_years}yr"
        )

    # The current Ratio Engine does not contain stock-price CAGR.
    #
    # The source ROE value represents a multi-year ROE statistic.
    # Comparing it with the latest annual ROE would not be accurate.
    return None


def cross_validate(
    parsed: pd.DataFrame,
    latest_ratios: pd.DataFrame,
    threshold: float = DIVERGENCE_THRESHOLD,
) -> pd.DataFrame:
    """Compare parsed CAGR values with Ratio Engine CAGR values."""

    output_rows: list[dict[str, Any]] = []

    for row in parsed.itertuples(index=False):
        computed_value: float | None = None
        divergence: float | None = None
        manual_review = False

        ratio_column = ratio_column_for(
            metric_type=row.metric_type,
            period_years=int(
                row.period_years
            ),
        )

        can_compare = (
            ratio_column is not None
            and not latest_ratios.empty
            and row.company_id
            in latest_ratios.index
            and ratio_column
            in latest_ratios.columns
        )

        if can_compare:
            ratio_row = latest_ratios.loc[
                row.company_id
            ]

            if isinstance(
                ratio_row,
                pd.DataFrame,
            ):
                ratio_row = ratio_row.iloc[-1]

            numeric_value = pd.to_numeric(
                ratio_row.get(
                    ratio_column
                ),
                errors="coerce",
            )

            if pd.notna(numeric_value):
                computed_value = float(
                    numeric_value
                )

                divergence = abs(
                    float(row.value_pct)
                    - computed_value
                )

                manual_review = (
                    divergence > threshold
                )

        output_rows.append(
            {
                "company_id": row.company_id,
                "metric_type": row.metric_type,
                "period_years": int(
                    row.period_years
                ),
                "value_pct": float(
                    row.value_pct
                ),
                "computed_value_pct": (
                    round(computed_value, 4)
                    if computed_value is not None
                    else None
                ),
                "divergence_pct": (
                    round(divergence, 4)
                    if divergence is not None
                    else None
                ),
                "manual_review_flag": (
                    manual_review
                ),
            }
        )

    return pd.DataFrame(
        output_rows,
        columns=PARSED_COLUMNS,
    )


# ============================================================
# OUTPUT WRITING
# ============================================================

def write_outputs(
    parsed: pd.DataFrame,
    failures: pd.DataFrame,
) -> tuple[Path, Path]:
    """Write analysis_parsed.csv and parse_failures.csv."""

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    parsed_output = (
        OUTPUT_DIR
        / "analysis_parsed.csv"
    )

    failure_output = (
        OUTPUT_DIR
        / "parse_failures.csv"
    )

    # reindex ensures column headers are written even when there
    # are no output rows.
    parsed.reindex(
        columns=PARSED_COLUMNS
    ).to_csv(
        parsed_output,
        index=False,
    )

    failures.reindex(
        columns=FAILURE_COLUMNS
    ).to_csv(
        failure_output,
        index=False,
    )

    return (
        parsed_output,
        failure_output,
    )


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_parser() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the complete Sprint 5 Day 29 parser."""

    analysis_file = find_analysis_file()

    print(
        f"Reading analysis file: {analysis_file}"
    )

    analysis = read_analysis_file(
        analysis_file
    )

    parsed, failures = parse_analysis(
        analysis
    )

    latest_ratios = load_latest_ratio_rows()

    parsed = cross_validate(
        parsed=parsed,
        latest_ratios=latest_ratios,
        threshold=DIVERGENCE_THRESHOLD,
    )

    if not parsed.empty:
        parsed = parsed.sort_values(
            [
                "company_id",
                "metric_type",
                "period_years",
            ],
            kind="stable",
        ).reset_index(
            drop=True
        )

    if not failures.empty:
        failures = failures.sort_values(
            [
                "company_id",
                "metric_type",
            ],
            kind="stable",
        ).reset_index(
            drop=True
        )

    parsed_output, failure_output = write_outputs(
        parsed=parsed,
        failures=failures,
    )

    manual_review_count = (
        int(
            parsed[
                "manual_review_flag"
            ].sum()
        )
        if not parsed.empty
        else 0
    )

    print()
    print(
        "NLP analysis parser completed"
    )
    print(
        "=" * 50
    )
    print(
        f"Parsed rows:       {len(parsed)}"
    )
    print(
        f"Parse failures:    {len(failures)}"
    )
    print(
        f"Manual reviews:    {manual_review_count}"
    )
    print(
        f"Parsed output:     {parsed_output}"
    )
    print(
        f"Failures output:   {failure_output}"
    )

    return parsed, failures


def main() -> None:
    """CLI entry point for python -m src.nlp.parser."""

    run_parser()


if __name__ == "__main__":
    main()