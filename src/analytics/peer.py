from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.screener.engine import (
    DEFAULT_CONFIG,
    database_path_from_config,
    load_config,
    load_financial_data,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


METRICS: dict[str, dict[str, Any]] = {
    "roe": {
        "column": "return_on_equity_pct",
        "higher_is_better": True,
    },
    "roce": {
        "column": "return_on_capital_employed_pct",
        "higher_is_better": True,
    },
    "net_profit_margin": {
        "column": "net_profit_margin_pct",
        "higher_is_better": True,
    },
    "debt_to_equity": {
        "column": "debt_to_equity",
        "higher_is_better": False,
    },
    "free_cash_flow": {
        "column": "free_cash_flow_cr",
        "higher_is_better": True,
    },
    "pat_cagr_5yr": {
        "column": "pat_cagr_5yr",
        "higher_is_better": True,
    },
    "revenue_cagr_5yr": {
        "column": "revenue_cagr_5yr",
        "higher_is_better": True,
    },
    "eps_cagr_5yr": {
        "column": "eps_cagr_5yr",
        "higher_is_better": True,
    },
    "interest_coverage": {
        "column": "interest_coverage_filter",
        "higher_is_better": True,
    },
    "asset_turnover": {
        "column": "asset_turnover",
        "higher_is_better": True,
    },
}


def load_peer_groups(
    database_path: str | Path,
) -> pd.DataFrame:
    """Load all company-to-peer-group assignments from SQLite."""

    database_path = Path(database_path)

    with sqlite3.connect(database_path) as connection:
        peer_groups = pd.read_sql_query(
            """
            SELECT
                peer_group_name,
                company_id,
                is_benchmark
            FROM peer_groups
            ORDER BY peer_group_name, company_id
            """,
            connection,
        )

    if peer_groups.empty:
        raise ValueError("The peer_groups table is empty.")

    duplicate_mask = peer_groups.duplicated(
        subset=["peer_group_name", "company_id"],
        keep=False,
    )

    if duplicate_mask.any():
        duplicates = peer_groups.loc[
            duplicate_mask,
            ["peer_group_name", "company_id"],
        ]
        raise ValueError(
            "Duplicate peer-group assignments found:\n"
            + duplicates.to_string(index=False)
        )

    return peer_groups


def build_peer_dataset(
    config_path: str | Path = DEFAULT_CONFIG,
) -> tuple[pd.DataFrame, Path]:
    """Join peer assignments to the latest financial metrics."""

    config = load_config(config_path)
    database_path = database_path_from_config(config)

    financial_data = load_financial_data(config)
    peer_groups = load_peer_groups(database_path)

    required_columns = {
        "company_id",
        "company_name",
        "year",
        "broad_sector",
        "sub_sector",
        "composite_quality_score",
    }

    required_columns.update(
        definition["column"]
        for definition in METRICS.values()
    )

    missing_columns = sorted(
        required_columns.difference(financial_data.columns)
    )

    if missing_columns:
        raise KeyError(
            "Financial data is missing required peer columns: "
            + ", ".join(missing_columns)
        )

    selected_columns = sorted(required_columns)

    peer_data = peer_groups.merge(
        financial_data[selected_columns],
        on="company_id",
        how="left",
        validate="many_to_one",
    )

    missing_financial_data = peer_data[
        peer_data["year"].isna()
    ][["peer_group_name", "company_id"]]

    if not missing_financial_data.empty:
        raise ValueError(
            "Peer companies missing latest-year financial data:\n"
            + missing_financial_data.to_string(index=False)
        )

    peer_data["year"] = peer_data["year"].astype(int)
    peer_data["is_benchmark"] = (
        peer_data["is_benchmark"]
        .fillna(0)
        .astype(int)
    )

    return peer_data, database_path


def percent_rank(
    series: pd.Series,
    higher_is_better: bool,
) -> pd.Series:
    """
    Calculate a 0–1 percent rank.

    Best value receives 1.0 and worst receives 0.0.
    """

    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)

    result = pd.Series(
        np.nan,
        index=series.index,
        dtype=float,
    )

    valid = numeric.dropna()
    count = len(valid)

    if count == 0:
        return result

    if count == 1:
        result.loc[valid.index] = 1.0
        return result

    ranks = valid.rank(
        method="average",
        ascending=higher_is_better,
    )

    result.loc[valid.index] = (
        (ranks - 1)
        / (count - 1)
    ).clip(0, 1)

    return result


def calculate_peer_percentiles(
    peer_data: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate ten metric ranks within peer group and year."""

    wide_data = peer_data.copy()
    group_columns = ["peer_group_name", "year"]

    for metric_name, definition in METRICS.items():
        source_column = definition["column"]
        percentile_column = f"{metric_name}_percentile"

        wide_data[percentile_column] = (
            wide_data.groupby(
                group_columns,
                group_keys=False,
                dropna=False,
            )[source_column]
            .transform(
                lambda values: percent_rank(
                    values,
                    higher_is_better=bool(
                        definition["higher_is_better"]
                    ),
                )
            )
        )

    long_frames: list[pd.DataFrame] = []

    for metric_name, definition in METRICS.items():
        source_column = definition["column"]
        percentile_column = f"{metric_name}_percentile"

        metric_frame = wide_data[
            [
                "company_id",
                "peer_group_name",
                "year",
                source_column,
                percentile_column,
            ]
        ].copy()

        metric_frame = metric_frame.rename(
            columns={
                source_column: "value",
                percentile_column: "percentile_rank",
            }
        )

        metric_frame["metric"] = metric_name

        metric_frame = metric_frame[
            [
                "company_id",
                "peer_group_name",
                "metric",
                "value",
                "percentile_rank",
                "year",
            ]
        ]

        long_frames.append(metric_frame)

    long_data = pd.concat(
        long_frames,
        ignore_index=True,
    )

    long_data["value"] = pd.to_numeric(
        long_data["value"],
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)

    long_data["percentile_rank"] = pd.to_numeric(
        long_data["percentile_rank"],
        errors="coerce",
    ).clip(0, 1)

    long_data = long_data.sort_values(
        [
            "peer_group_name",
            "metric",
            "percentile_rank",
            "company_id",
        ],
        ascending=[True, True, False, True],
        na_position="last",
    ).reset_index(drop=True)

    return wide_data, long_data


def create_peer_percentiles_table(
    connection: sqlite3.Connection,
) -> None:
    """Create the required peer_percentiles table."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS peer_percentiles (
            company_id TEXT NOT NULL,
            peer_group_name TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            percentile_rank REAL,
            year INTEGER NOT NULL,
            PRIMARY KEY (
                company_id,
                peer_group_name,
                metric,
                year
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_peer_percentiles_group_metric
        ON peer_percentiles (
            peer_group_name,
            metric,
            year
        )
        """
    )


def save_peer_percentiles(
    long_data: pd.DataFrame,
    database_path: str | Path,
) -> int:
    """Replace percentile rows for the calculated year."""

    if long_data.empty:
        raise ValueError("No percentile rows were generated.")

    database_path = Path(database_path)

    years = sorted(
        int(year)
        for year in long_data["year"].dropna().unique()
    )

    records = []

    for row in long_data.itertuples(index=False):
        records.append(
            (
                str(row.company_id),
                str(row.peer_group_name),
                str(row.metric),
                None if pd.isna(row.value) else float(row.value),
                (
                    None
                    if pd.isna(row.percentile_rank)
                    else float(row.percentile_rank)
                ),
                int(row.year),
            )
        )

    with sqlite3.connect(database_path) as connection:
        create_peer_percentiles_table(connection)

        for year in years:
            connection.execute(
                "DELETE FROM peer_percentiles WHERE year = ?",
                (year,),
            )

        connection.executemany(
            """
            INSERT OR REPLACE INTO peer_percentiles (
                company_id,
                peer_group_name,
                metric,
                value,
                percentile_rank,
                year
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            records,
        )

        connection.commit()

    return len(records)


def get_company_peer_data(
    company_id: str,
    wide_data: pd.DataFrame | None = None,
    config_path: str | Path = DEFAULT_CONFIG,
) -> pd.DataFrame | str:
    """Return company peer data or the required unassigned message."""

    normalised_company_id = str(company_id).strip().upper()

    if wide_data is None:
        peer_data, _ = build_peer_dataset(config_path)
        wide_data, _ = calculate_peer_percentiles(peer_data)

    company_rows = wide_data[
        wide_data["company_id"]
        .astype(str)
        .str.strip()
        .str.upper()
        .eq(normalised_company_id)
    ].copy()

    if company_rows.empty:
        return "No peer group assigned"

    return company_rows.reset_index(drop=True)


def validate_peer_results(
    wide_data: pd.DataFrame,
    long_data: pd.DataFrame,
) -> None:
    """Validate the required peer-ranking output."""

    peer_group_count = wide_data["peer_group_name"].nunique()

    if peer_group_count != 11:
        raise AssertionError(
            f"Expected 11 peer groups, found {peer_group_count}."
        )

    metric_count = long_data["metric"].nunique()

    if metric_count != 10:
        raise AssertionError(
            f"Expected 10 metrics, found {metric_count}."
        )

    non_null_percentiles = long_data[
        "percentile_rank"
    ].dropna()

    if not non_null_percentiles.between(0, 1).all():
        raise AssertionError(
            "Percentile ranks must be between 0 and 1."
        )

    expected_rows = len(wide_data) * len(METRICS)

    if len(long_data) != expected_rows:
        raise AssertionError(
            f"Expected {expected_rows} percentile rows, "
            f"generated {len(long_data)}."
        )

    duplicate_count = long_data.duplicated(
        subset=[
            "company_id",
            "peer_group_name",
            "metric",
            "year",
        ]
    ).sum()

    if duplicate_count:
        raise AssertionError(
            f"Found {duplicate_count} duplicate percentile rows."
        )


def print_group_summary(
    wide_data: pd.DataFrame,
    long_data: pd.DataFrame,
    inserted_rows: int,
    database_path: Path,
) -> None:
    """Print the Day 18 completion summary."""

    print("Peer percentile engine completed")
    print("===============================")
    print(f"Database: {database_path}")
    print(f"Assigned companies: {len(wide_data)}")
    print(f"Peer groups: {wide_data['peer_group_name'].nunique()}")
    print(f"Metrics: {long_data['metric'].nunique()}")
    print(f"Rows inserted: {inserted_rows}")

    print("\nCompanies per peer group:")
    print(
        wide_data.groupby("peer_group_name")
        .size()
        .to_string()
    )


def main() -> None:
    peer_data, database_path = build_peer_dataset()
    wide_data, long_data = calculate_peer_percentiles(peer_data)

    validate_peer_results(
        wide_data,
        long_data,
    )

    inserted_rows = save_peer_percentiles(
        long_data,
        database_path,
    )

    print_group_summary(
        wide_data,
        long_data,
        inserted_rows,
        database_path,
    )


if __name__ == "__main__":
    main()
