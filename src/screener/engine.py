from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "screener_config.yaml"


def load_config(config_path: str | Path = DEFAULT_CONFIG) -> dict:
    """Load the analyst-editable screener configuration."""

    path = Path(config_path)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if not path.exists():
        raise FileNotFoundError(f"Screener configuration not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not config:
        raise ValueError("Screener configuration is empty.")

    return config


def database_path_from_config(config: dict) -> Path:
    """Return the configured SQLite database path."""

    database_path = Path(config["database"]["path"])

    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    if not database_path.exists():
        raise FileNotFoundError(f"Database not found: {database_path}")

    return database_path


def get_latest_year(connection: sqlite3.Connection, config: dict) -> int:
    """Return the configured year or latest financial-ratio year."""

    configured_year = config.get("settings", {}).get("latest_year")

    if configured_year is not None:
        return int(configured_year)

    row = connection.execute("SELECT MAX(year) FROM financial_ratios").fetchone()

    if row is None or row[0] is None:
        raise ValueError("No financial-ratio years were found.")

    return int(row[0])


def load_financial_data(config: dict) -> pd.DataFrame:
    """
    Load the latest company metrics required by the screener.

    Data is combined from financial_ratios, companies,
    market_cap and profitandloss.
    """

    database_path = database_path_from_config(config)

    with sqlite3.connect(database_path) as connection:
        latest_year = get_latest_year(connection, config)
        previous_year = latest_year - 1
        fcf_start_year = latest_year - 5

        query = """
        SELECT
            fr.*,
            c.company_name,
            c.sub_sector,
            c.broad_sector AS company_broad_sector,
            c.market_cap_category,
            mc.market_cap_crore,
            mc.enterprise_value_crore,
            mc.pe_ratio,
            mc.pb_ratio,
            mc.ev_ebitda,
            mc.dividend_yield_pct,
            pl.sales,
            pl.net_profit,
            pl.dividend_payout AS pnl_dividend_payout,
            previous_fr.debt_to_equity AS previous_debt_to_equity,
            fcf_start.free_cash_flow_cr AS fcf_5yr_start_cr
        FROM financial_ratios AS fr
        LEFT JOIN companies AS c
            ON fr.company_id = c.company_id
        LEFT JOIN market_cap AS mc
            ON fr.company_id = mc.company_id
            AND fr.year = mc.year
        LEFT JOIN profitandloss AS pl
            ON fr.company_id = pl.company_id
            AND fr.year = pl.year
        LEFT JOIN financial_ratios AS previous_fr
           ON fr.company_id = previous_fr.company_id
           AND previous_fr.year = ?
        LEFT JOIN financial_ratios AS fcf_start
           ON fr.company_id = fcf_start.company_id
           AND fcf_start.year = ?
        WHERE fr.year = ?
        ORDER BY fr.company_id
        """

        dataframe = pd.read_sql_query(
            query,
            connection,
            params=(previous_year, fcf_start_year, latest_year),
        )

    if dataframe.empty:
        raise ValueError(f"No financial data found for {latest_year}.")

    dataframe["broad_sector"] = dataframe["broad_sector"].fillna(
        dataframe["company_broad_sector"]
    )

    dataframe["dividend_payout_pct"] = dataframe["dividend_payout_ratio_pct"].fillna(
        dataframe["pnl_dividend_payout"]
    )

    dataframe["latest_fcf_positive"] = dataframe[
        "free_cash_flow_cr"
    ].notna() & dataframe["free_cash_flow_cr"].gt(0)

    dataframe["debt_to_equity_declining"] = (
        dataframe["debt_to_equity"].notna()
        & dataframe["previous_debt_to_equity"].notna()
        & dataframe["debt_to_equity"].lt(dataframe["previous_debt_to_equity"])
    )

    dataframe["interest_coverage_filter"] = dataframe["interest_coverage"].copy()

    debt_free_mask = (
        dataframe["icr_label"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .eq("debt free")
    )

    dataframe.loc[
        debt_free_mask,
        "interest_coverage_filter",
    ] = float("inf")

    start_fcf = pd.to_numeric(
        dataframe["fcf_5yr_start_cr"],
        errors="coerce",
    )

    latest_fcf = pd.to_numeric(
        dataframe["free_cash_flow_cr"],
        errors="coerce",
    )

    valid_fcf_cagr = start_fcf.gt(0) & latest_fcf.gt(0)

    dataframe["fcf_cagr_5yr_pct"] = np.nan

    dataframe.loc[
        valid_fcf_cagr,
        "fcf_cagr_5yr_pct",
    ] = (
        (latest_fcf.loc[valid_fcf_cagr] / start_fcf.loc[valid_fcf_cagr]) ** (1 / 5) - 1
    ) * 100

    dataframe = calculate_composite_quality_score(dataframe)

    return dataframe


def winsorised_score(
    series: pd.Series,
    higher_is_better: bool = True,
) -> pd.Series:
    """Winsorise a metric at P10/P90 and scale it to 0-100."""

    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)

    valid = numeric.dropna()

    if valid.empty:
        return pd.Series(
            50.0,
            index=series.index,
            dtype=float,
        )

    p10 = float(valid.quantile(0.10))
    p90 = float(valid.quantile(0.90))

    if np.isclose(p10, p90):
        result = pd.Series(
            50.0,
            index=series.index,
            dtype=float,
        )
    else:
        clipped = numeric.clip(
            lower=p10,
            upper=p90,
        )

        result = 100 * (clipped - p10) / (p90 - p10)

        if not higher_is_better:
            result = 100 - result

        result = result.fillna(50.0)

    return result.clip(0, 100)


def calculate_weighted_scores(
    dataframe: pd.DataFrame,
    keep_components: bool = True,
) -> pd.DataFrame:
    """Calculate the required weighted quality score."""

    scored = dataframe.copy()

    components = {
        "roe_score": winsorised_score(scored["return_on_equity_pct"]),
        "roce_score": winsorised_score(scored["return_on_capital_employed_pct"]),
        "npm_score": winsorised_score(scored["net_profit_margin_pct"]),
        "fcf_cagr_score": winsorised_score(scored["fcf_cagr_5yr_pct"]),
        "cfo_pat_score": winsorised_score(scored["cfo_pat_ratio"]),
        "fcf_positive_score": (
            scored["free_cash_flow_cr"].fillna(0).gt(0).astype(float) * 100
        ),
        "revenue_growth_score": winsorised_score(scored["revenue_cagr_5yr"]),
        "pat_growth_score": winsorised_score(scored["pat_cagr_5yr"]),
        "de_score": winsorised_score(
            scored["debt_to_equity"],
            higher_is_better=False,
        ),
        "icr_score": winsorised_score(scored["interest_coverage_filter"]),
    }

    debt_free_mask = (
        scored["icr_label"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
        .eq("debt free")
    )

    components["icr_score"] = components["icr_score"].mask(
        debt_free_mask,
        100.0,
    )

    weights = {
        "roe_score": 0.15,
        "roce_score": 0.10,
        "npm_score": 0.10,
        "fcf_cagr_score": 0.15,
        "cfo_pat_score": 0.10,
        "fcf_positive_score": 0.05,
        "revenue_growth_score": 0.10,
        "pat_growth_score": 0.10,
        "de_score": 0.10,
        "icr_score": 0.05,
    }

    composite = pd.Series(
        0.0,
        index=scored.index,
        dtype=float,
    )

    for component_name, weight in weights.items():
        component_score = components[component_name]

        if keep_components:
            scored[component_name] = component_score.round(2)

        composite += component_score * weight

    scored["composite_quality_score"] = composite.clip(0, 100).round(2)

    return scored


def calculate_composite_quality_score(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """Calculate global and within-sector composite quality scores."""

    scored = calculate_weighted_scores(
        dataframe,
        keep_components=True,
    )

    scored["sector_relative_score"] = np.nan

    sector_values = scored["broad_sector"].fillna("Unassigned").astype(str)

    for sector_name in sector_values.unique():
        sector_index = scored.index[sector_values.eq(sector_name)]

        sector_data = calculate_weighted_scores(
            scored.loc[sector_index],
            keep_components=False,
        )

        scored.loc[
            sector_index,
            "sector_relative_score",
        ] = sector_data["composite_quality_score"]

    scored["sector_relative_score"] = (
        scored["sector_relative_score"].clip(0, 100).round(2)
    )

    return scored


def compare_series(
    series: pd.Series,
    operator: str,
    threshold: Any,
) -> pd.Series:
    """Apply one supported threshold operator."""

    operator = operator.lower().strip()

    comparisons = {
        "gt": lambda: series.gt(threshold),
        "gte": lambda: series.ge(threshold),
        "lt": lambda: series.lt(threshold),
        "lte": lambda: series.le(threshold),
        "eq": lambda: series.eq(threshold),
        "ne": lambda: series.ne(threshold),
    }

    if operator not in comparisons:
        supported = ", ".join(comparisons)
        raise ValueError(
            f"Unsupported operator '{operator}'. " f"Supported operators: {supported}"
        )

    return comparisons[operator]().fillna(False)


def apply_filter(
    dataframe: pd.DataFrame,
    metric_name: str,
    rule: dict,
    config: dict,
) -> pd.Series:
    """Return the Boolean mask for one screener condition."""

    metrics = config.get("metrics", {})

    if metric_name not in metrics:
        raise KeyError(f"Metric is not configured: {metric_name}")

    column = metrics[metric_name]["column"]

    if column not in dataframe.columns:
        raise KeyError(
            f"Column '{column}' for metric '{metric_name}' "
            "is missing from the financial dataset."
        )

    operator = str(rule["operator"]).lower()
    threshold = rule["value"]

    mask = compare_series(
        dataframe[column],
        operator,
        threshold,
    )

    # D/E maximum filters do not apply to Financials companies.
    if (
        metric_name == "debt_to_equity"
        and operator in {"lt", "lte"}
        and rule.get("skip_financials", True)
    ):
        financial_sector_name = config.get("settings", {}).get(
            "financial_sector_name", "Financials"
        )

        financials_mask = (
            dataframe["broad_sector"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.casefold()
            .eq(str(financial_sector_name).strip().casefold())
        )

        mask = mask | financials_mask

    return mask


def apply_filters(
    dataframe: pd.DataFrame,
    filters: dict,
    config: dict,
) -> pd.DataFrame:
    """Apply multiple threshold filters to a financial DataFrame."""

    combined_mask = pd.Series(
        True,
        index=dataframe.index,
        dtype=bool,
    )

    for metric_name, rule in filters.items():
        metric_mask = apply_filter(
            dataframe,
            metric_name,
            rule,
            config,
        )
        combined_mask &= metric_mask

    result = dataframe.loc[combined_mask].copy()

    if "composite_quality_score" not in result.columns:
        result["composite_quality_score"] = pd.NA

    result = result.sort_values(
        ["composite_quality_score", "company_id"],
        ascending=[False, True],
        na_position="last",
    )

    return result.reset_index(drop=True)


def run_preset(
    preset_name: str,
    config_path: str | Path = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Load and execute one configured preset screener."""

    config = load_config(config_path)
    presets = config.get("presets", {})

    if preset_name not in presets:
        available = ", ".join(presets)
        raise KeyError(
            f"Unknown preset '{preset_name}'. " f"Available presets: {available}"
        )

    dataframe = load_financial_data(config)
    preset = presets[preset_name]

    return apply_filters(
        dataframe,
        preset["filters"],
        config,
    )


def parse_custom_filter(value: str) -> tuple[str, dict]:
    """
    Parse metric:operator:value from the command line.

    Example: roe:gt:15
    """

    parts = value.split(":", maxsplit=2)

    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "Filter must use metric:operator:value format."
        )

    metric, operator, raw_threshold = parts

    lowered = raw_threshold.strip().lower()

    if lowered == "true":
        threshold: Any = True
    elif lowered == "false":
        threshold = False
    else:
        try:
            threshold = float(raw_threshold)
        except ValueError:
            threshold = raw_threshold

    return metric, {
        "operator": operator,
        "value": threshold,
    }


def display_results(
    dataframe: pd.DataFrame,
    title: str,
) -> None:
    """Print a compact screener result summary."""

    print(f"\n{title}")
    print("=" * len(title))
    print(f"Companies returned: {len(dataframe)}")

    display_columns = [
        "company_id",
        "company_name",
        "broad_sector",
        "return_on_equity_pct",
        "debt_to_equity",
        "free_cash_flow_cr",
        "revenue_cagr_5yr",
        "composite_quality_score",
    ]

    available_columns = [
        column for column in display_columns if column in dataframe.columns
    ]

    if dataframe.empty:
        print("No companies matched the selected conditions.")
    else:
        print(dataframe[available_columns].head(20).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="N100 financial screener engine")

    parser.add_argument(
        "--preset",
        help="Run one configured preset screener.",
    )

    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        help=(
            "Custom filter in metric:operator:value format. "
            "It may be supplied multiple times."
        ),
    )

    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Display the available preset screeners.",
    )

    args = parser.parse_args()
    config = load_config()

    if args.list_presets:
        print("Available presets:")

        for name, definition in config["presets"].items():
            print(f"- {name}: {definition['display_name']}")

        return

    if args.preset:
        result = run_preset(args.preset)
        display_name = config["presets"][args.preset]["display_name"]
        display_results(result, display_name)
        return

    if args.filter:
        custom_filters = dict(parse_custom_filter(item) for item in args.filter)

        dataframe = load_financial_data(config)
        result = apply_filters(
            dataframe,
            custom_filters,
            config,
        )

        display_results(result, "Custom Screener")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
