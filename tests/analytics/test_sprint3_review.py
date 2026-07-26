from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from src.analytics.peer import (
    METRICS,
    build_peer_dataset,
    calculate_peer_percentiles,
    get_company_peer_data,
)
from src.screener.engine import (
    load_config,
    run_preset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = PROJECT_ROOT / "data" / "processed" / "nifty100.db"
SCREENER_WORKBOOK = PROJECT_ROOT / "output" / "screener_output.xlsx"
PEER_WORKBOOK = PROJECT_ROOT / "output" / "peer_comparison.xlsx"
RADAR_DIRECTORY = PROJECT_ROOT / "reports" / "radar_charts"


EXPECTED_PRESET_COUNTS = {
    "quality_compounder": 22,
    "value_pick": 5,
    "growth_accelerator": 19,
    "dividend_champion": 30,
    "debt_free_blue_chip": 6,
    "turnaround_watch": 32,
}


EXPECTED_PEER_GROUP_COUNTS = {
    "Automobiles": 7,
    "Consumer Finance": 3,
    "FMCG": 7,
    "IT Services": 5,
    "Life Insurance": 4,
    "Oil & Gas": 5,
    "Pharmaceuticals": 5,
    "Power & Utilities": 7,
    "Private Banks": 5,
    "Public Sector Banks": 4,
    "Steel": 4,
}


def test_all_six_presets_have_expected_counts() -> None:
    config = load_config()

    assert set(config["presets"]) == set(EXPECTED_PRESET_COUNTS)

    actual_counts = {
        preset_name: len(run_preset(preset_name))
        for preset_name in config["presets"]
    }

    assert actual_counts == EXPECTED_PRESET_COUNTS


def test_quality_compounder_conditions() -> None:
    result = run_preset("quality_compounder")

    assert not result.empty
    assert result["return_on_equity_pct"].gt(15).all()
    assert result["free_cash_flow_cr"].gt(0).all()
    assert result["revenue_cagr_5yr"].gt(10).all()

    financials = (
        result["broad_sector"]
        .fillna("")
        .astype(str)
        .str.casefold()
        .eq("financials")
    )

    assert (
        financials
        | result["debt_to_equity"].lt(1)
    ).all()


def test_screener_workbook_has_six_sheets() -> None:
    assert SCREENER_WORKBOOK.exists()

    workbook = pd.ExcelFile(SCREENER_WORKBOOK)

    assert len(workbook.sheet_names) == 6
    assert workbook.sheet_names == [
        "Quality Compounder",
        "Value Pick",
        "Growth Accelerator",
        "Dividend Champion",
        "Debt-Free Blue Chip",
        "Turnaround Watch",
    ]


def test_peer_dataset_has_11_groups_and_56_companies() -> None:
    peer_data, _ = build_peer_dataset()

    assert len(peer_data) == 56
    assert peer_data["peer_group_name"].nunique() == 11

    actual_counts = (
        peer_data.groupby("peer_group_name")
        .size()
        .to_dict()
    )

    assert actual_counts == EXPECTED_PEER_GROUP_COUNTS


def test_peer_engine_has_ten_metrics_and_valid_percentiles() -> None:
    peer_data, _ = build_peer_dataset()
    wide_data, long_data = calculate_peer_percentiles(peer_data)

    assert len(METRICS) == 10
    assert long_data["metric"].nunique() == 10
    assert len(long_data) == 560

    percentiles = long_data["percentile_rank"].dropna()

    assert percentiles.between(0, 1).all()
    assert percentiles.min() == 0
    assert percentiles.max() == 1

    assert not long_data.duplicated(
        subset=[
            "company_id",
            "peer_group_name",
            "metric",
            "year",
        ]
    ).any()

    assert len(wide_data) == 56


def test_sqlite_peer_percentiles_table() -> None:
    assert DATABASE_PATH.exists()

    with sqlite3.connect(DATABASE_PATH) as connection:
        table_exists = connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'peer_percentiles'
            """
        ).fetchone()[0]

        row_count = connection.execute(
            "SELECT COUNT(*) FROM peer_percentiles"
        ).fetchone()[0]

        group_count = connection.execute(
            """
            SELECT COUNT(DISTINCT peer_group_name)
            FROM peer_percentiles
            """
        ).fetchone()[0]

        metric_count = connection.execute(
            """
            SELECT COUNT(DISTINCT metric)
            FROM peer_percentiles
            """
        ).fetchone()[0]

        years = connection.execute(
            """
            SELECT DISTINCT year
            FROM peer_percentiles
            ORDER BY year
            """
        ).fetchall()

    assert table_exists == 1
    assert row_count == 560
    assert group_count == 11
    assert metric_count == 10
    assert years == [(2024,)]


def test_it_services_highest_roe_has_highest_percentile() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = pd.read_sql_query(
            """
            SELECT
                company_id,
                value,
                percentile_rank
            FROM peer_percentiles
            WHERE peer_group_name = 'IT Services'
              AND metric = 'roe'
            ORDER BY value DESC
            """,
            connection,
        )

    assert not rows.empty

    highest_roe_row = rows.iloc[0]

    assert highest_roe_row["company_id"] == "TCS"
    assert highest_roe_row["percentile_rank"] == 1.0


def test_it_services_lowest_de_has_highest_percentile() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = pd.read_sql_query(
            """
            SELECT
                company_id,
                value,
                percentile_rank
            FROM peer_percentiles
            WHERE peer_group_name = 'IT Services'
              AND metric = 'debt_to_equity'
            ORDER BY value ASC
            """,
            connection,
        )

    assert not rows.empty

    lowest_de_row = rows.iloc[0]

    assert lowest_de_row["company_id"] == "HCLTECH"
    assert lowest_de_row["percentile_rank"] == 1.0


def test_unassigned_company_returns_required_message() -> None:
    assert get_company_peer_data("INDIGO") == "No peer group assigned"


def test_peer_workbook_has_exactly_11_sheets() -> None:
    assert PEER_WORKBOOK.exists()

    workbook = pd.ExcelFile(PEER_WORKBOOK)

    assert len(workbook.sheet_names) == 11
    assert workbook.sheet_names == list(
        EXPECTED_PEER_GROUP_COUNTS
    )

    actual_counts = {
        sheet_name: (
            len(
                pd.read_excel(
                    PEER_WORKBOOK,
                    sheet_name=sheet_name,
                )
            )
            - 1
        )
        for sheet_name in workbook.sheet_names
    }

    assert actual_counts == EXPECTED_PEER_GROUP_COUNTS


def test_peer_workbook_benchmarks_and_median_rows() -> None:
    it_services = pd.read_excel(
        PEER_WORKBOOK,
        sheet_name="IT Services",
    )

    fmcg = pd.read_excel(
        PEER_WORKBOOK,
        sheet_name="FMCG",
    )

    assert (
        it_services.loc[
            it_services["Company ID"].eq("TCS"),
            "Benchmark",
        ].iloc[0]
        == 1
    )

    assert (
        fmcg.loc[
            fmcg["Company ID"].eq("HINDUNILVR"),
            "Benchmark",
        ].iloc[0]
        == 1
    )

    assert (
        it_services.iloc[-1]["Company ID"]
        == "Peer Group Median"
    )

    assert (
        fmcg.iloc[-1]["Company ID"]
        == "Peer Group Median"
    )


def test_all_92_radar_charts_exist() -> None:
    assert RADAR_DIRECTORY.exists()

    chart_files = list(
        RADAR_DIRECTORY.glob("*_radar.png")
    )

    assert len(chart_files) == 92

    required_samples = {
        "TCS_radar.png",
        "HINDUNILVR_radar.png",
        "INDIGO_radar.png",
    }

    actual_names = {
        path.name
        for path in chart_files
    }

    assert required_samples.issubset(actual_names)

    assert all(
        path.stat().st_size > 0
        for path in chart_files
    )
