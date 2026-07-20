from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

from src.analytics.cagr import calculate_cagr
from src.analytics.cashflow_kpis import (
    average_cfo_pat_ratio,
    capex_intensity,
    capex_intensity_label,
    capital_allocation_pattern,
    capital_expenditure,
    cashflow_sign,
    cfo_pat_ratio,
    cfo_quality_label,
    fcf_conversion_rate,
    free_cash_flow,
)
from src.analytics.ratios import (
    asset_turnover,
    debt_to_equity,
    high_leverage_flag,
    icr_label,
    icr_warning_flag,
    interest_coverage,
    net_debt,
    net_profit_margin,
    operating_profit_margin,
    opm_mismatch,
    return_on_assets,
    return_on_capital_employed,
    return_on_equity,
)

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "processed" / "nifty100.db"
OUTPUT_DIR = ROOT / "output"


TABLE_COLUMNS = [
    "id",
    "company_id",
    "year",
    "broad_sector",
    "net_profit_margin_pct",
    "operating_profit_margin_pct",
    "operating_profit_margin_variance_pct",
    "return_on_equity_pct",
    "return_on_capital_employed_pct",
    "return_on_assets_pct",
    "roce_sector_median_pct",
    "roce_vs_sector_pct",
    "debt_to_equity",
    "high_leverage_flag",
    "interest_coverage",
    "icr_label",
    "icr_warning_flag",
    "net_debt_cr",
    "asset_turnover",
    "free_cash_flow_cr",
    "capex_cr",
    "capex_intensity_pct",
    "capex_intensity_label",
    "fcf_conversion_rate_pct",
    "earnings_per_share",
    "book_value_per_share",
    "dividend_payout_ratio_pct",
    "total_debt_cr",
    "cash_from_operations_cr",
    "cfo_pat_ratio",
    "cfo_quality_5yr",
    "cfo_quality_label",
    "cfo_sign",
    "cfi_sign",
    "cff_sign",
    "capital_allocation_pattern",
    "revenue_cagr_3yr",
    "revenue_cagr_3yr_flag",
    "revenue_cagr_5yr",
    "revenue_cagr_5yr_flag",
    "revenue_cagr_10yr",
    "revenue_cagr_10yr_flag",
    "pat_cagr_3yr",
    "pat_cagr_3yr_flag",
    "pat_cagr_5yr",
    "pat_cagr_5yr_flag",
    "pat_cagr_10yr",
    "pat_cagr_10yr_flag",
    "eps_cagr_3yr",
    "eps_cagr_3yr_flag",
    "eps_cagr_5yr",
    "eps_cagr_5yr_flag",
    "eps_cagr_10yr",
    "eps_cagr_10yr_flag",
    "composite_quality_score",
]


def is_missing(value):
    return value is None or pd.isna(value)


def add_values(first, second):
    if is_missing(first) and is_missing(second):
        return None

    first = 0 if is_missing(first) else first
    second = 0 if is_missing(second) else second
    return first + second


def book_value_per_share(equity, reserves, face_value):
    if is_missing(equity) or equity <= 0:
        return None

    if is_missing(face_value) or face_value <= 0:
        return None

    reserves = 0 if is_missing(reserves) else reserves
    shareholder_equity = equity + reserves

    if shareholder_equity <= 0:
        return None

    return shareholder_equity * face_value / equity


def add_cagr_columns(data, source_column, prefix):
    lookup = {}

    for row in data[["company_id", "year_number", source_column]].itertuples(
        index=False
    ):
        if not is_missing(row.year_number):
            lookup[(row.company_id, int(row.year_number))] = row[2]

    for window in (3, 5, 10):
        values = []
        flags = []

        for row in data[["company_id", "year_number", source_column]].itertuples(
            index=False
        ):
            if is_missing(row.year_number):
                value, flag = calculate_cagr(
                    None,
                    None,
                    window,
                    sufficient=False,
                )
            else:
                current_year = int(row.year_number)
                current_value = row[2]
                base_value = lookup.get((row.company_id, current_year - window))

                sufficient = not is_missing(current_value) and not is_missing(
                    base_value
                )

                value, flag = calculate_cagr(
                    base_value,
                    current_value,
                    window,
                    sufficient=sufficient,
                )

            values.append(value)
            flags.append(flag)

        data[f"{prefix}_cagr_{window}yr"] = values
        data[f"{prefix}_cagr_{window}yr_flag"] = flags


def add_cfo_quality(data):
    cfo_lookup = {}
    pat_lookup = {}

    for row in data[
        [
            "company_id",
            "year_number",
            "operating_activity",
            "net_profit",
        ]
    ].itertuples(index=False):
        if not is_missing(row.year_number):
            key = (row.company_id, int(row.year_number))
            cfo_lookup[key] = row.operating_activity
            pat_lookup[key] = row.net_profit

    averages = []

    for row in data[["company_id", "year_number"]].itertuples(index=False):
        if is_missing(row.year_number):
            averages.append(None)
            continue

        current_year = int(row.year_number)
        years = range(current_year - 4, current_year + 1)

        cfo_values = [cfo_lookup.get((row.company_id, year)) for year in years]

        pat_values = [pat_lookup.get((row.company_id, year)) for year in years]

        complete = not any(is_missing(value) for value in cfo_values + pat_values)

        if not complete:
            averages.append(None)
            continue

        averages.append(average_cfo_pat_ratio(cfo_values, pat_values))

    data["cfo_quality_5yr"] = averages
    data["cfo_quality_label"] = data["cfo_quality_5yr"].map(cfo_quality_label)


def quality_score(row):
    checks = [
        not is_missing(row["net_profit_margin_pct"])
        and row["net_profit_margin_pct"] > 10,
        not is_missing(row["return_on_equity_pct"])
        and row["return_on_equity_pct"] > 15,
        not is_missing(row["return_on_capital_employed_pct"])
        and row["return_on_capital_employed_pct"] > 15,
        not is_missing(row["debt_to_equity"])
        and (
            row["debt_to_equity"] < 1
            or str(row["broad_sector"]).strip().casefold() == "financials"
        ),
        (not is_missing(row["interest_coverage"]) and row["interest_coverage"] > 3)
        or row["icr_label"] == "Debt Free",
        not is_missing(row["free_cash_flow_cr"]) and row["free_cash_flow_cr"] > 0,
        not is_missing(row["revenue_cagr_5yr"]) and row["revenue_cagr_5yr"] > 10,
        row["cfo_quality_label"] == "High Quality",
    ]

    return round(sum(checks) / len(checks) * 100, 2)


def create_logs(data):
    entries = []

    for row in data.itertuples(index=False):
        if opm_mismatch(
            row.computed_opm_pct,
            row.opm_percentage,
        ):
            difference = abs(row.computed_opm_pct - row.opm_percentage)

            entries.append(
                f"company_id={row.company_id} | "
                f"year={row.year} | metric=OPM | "
                f"computed={row.computed_opm_pct:.4f} | "
                f"source={row.opm_percentage:.4f} | "
                f"difference={difference:.4f} | "
                "category=formula discrepancy | "
                "explanation=OPM difference exceeds 1%"
            )

        equity = add_values(
            row.equity_capital,
            row.reserves,
        )

        if equity is not None and equity <= 0:
            entries.append(
                f"company_id={row.company_id} | "
                f"year={row.year} | metric=ROE | "
                "category=data source issue | "
                "explanation=Non-positive shareholder equity"
            )

    # Compare the latest computed ROE and ROCE with
    # the source values stored in companies.xlsx.
    latest = (
        data.dropna(subset=["year_number"])
        .sort_values("year_number")
        .groupby("company_id", as_index=False)
        .tail(1)
    )

    for row in latest.itertuples(index=False):
        if not is_missing(row.return_on_equity_pct) and not is_missing(
            row.source_company_roe_pct
        ):
            roe_difference = abs(row.return_on_equity_pct - row.source_company_roe_pct)

            if roe_difference > 5:
                entries.append(
                    f"company_id={row.company_id} | "
                    f"year={row.year} | metric=ROE | "
                    f"computed={row.return_on_equity_pct:.4f} | "
                    f"source={row.source_company_roe_pct:.4f} | "
                    f"difference={roe_difference:.4f} | "
                    "category=data source issue | "
                    "explanation=Computed ROE is used for "
                    "analytics; source ROE is retained for "
                    "display and comparison only"
                )

        if not is_missing(row.return_on_capital_employed_pct) and not is_missing(
            row.source_company_roce_pct
        ):
            roce_difference = abs(
                row.return_on_capital_employed_pct - row.source_company_roce_pct
            )

            if roce_difference > 5:
                entries.append(
                    f"company_id={row.company_id} | "
                    f"year={row.year} | metric=ROCE | "
                    f"computed="
                    f"{row.return_on_capital_employed_pct:.4f} | "
                    f"source={row.source_company_roce_pct:.4f} | "
                    f"difference={roce_difference:.4f} | "
                    "category=formula discrepancy | "
                    "explanation=Computed ROCE uses EBIT "
                    "divided by equity capital, reserves "
                    "and borrowings"
                )

    insufficient_cashflows = (
        data["capital_allocation_pattern"] == "Insufficient Data"
    ).sum()

    if insufficient_cashflows:
        entries.append(
            "metric=capital_allocation | "
            f"insufficient_rows={insufficient_cashflows} | "
            "category=data source issue | "
            "explanation=Cash-flow values are missing for "
            "these company-year records; no artificial "
            "capital-allocation pattern was assigned"
        )

    entries.append(
        "metric=financial_ratios_row_count | "
        "expected=>=1100 | "
        f"available_unique_company_years={len(data)} | "
        "category=data source issue | "
        "explanation=No duplicate or synthetic rows inserted"
    )

    financial_count = (
        data[["company_id", "broad_sector"]]
        .drop_duplicates("company_id")["broad_sector"]
        .astype(str)
        .str.strip()
        .str.casefold()
        .eq("financials")
        .sum()
    )

    if financial_count != 19:
        entries.append(
            "metric=financial_sector_company_count | "
            "requirement_count=19 | "
            f"source_count={financial_count} | "
            "category=version difference | "
            "explanation=Source sector classifications retained"
        )

    log_path = OUTPUT_DIR / "ratio_edge_cases.log"
    log_path.write_text(
        "\n".join(entries) + "\n",
        encoding="utf-8",
    )

    return len(entries)


def run_ratio_engine():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as connection:
        companies = pd.read_sql_query(
            """
            SELECT company_id,
                   company_name,
                   face_value,
                   roce_percentage AS source_company_roce_pct,
                   roe_percentage AS source_company_roe_pct,
                   broad_sector,
                   sub_sector
            FROM companies
            """,
            connection,
        )

        profit_loss = pd.read_sql_query(
            "SELECT * FROM profitandloss",
            connection,
        )

        balance_sheet = pd.read_sql_query(
            "SELECT * FROM balancesheet",
            connection,
        )

        cashflow = pd.read_sql_query(
            "SELECT * FROM cashflow",
            connection,
        )

        old_ratios = pd.read_sql_query(
            "SELECT * FROM financial_ratios",
            connection,
        )

        keys = pd.concat(
            [
                profit_loss[["company_id", "year"]],
                balance_sheet[["company_id", "year"]],
                cashflow[["company_id", "year"]],
            ],
            ignore_index=True,
        ).drop_duplicates()

        data = keys.merge(
            profit_loss,
            on=["company_id", "year"],
            how="left",
        )

        data = data.merge(
            balance_sheet,
            on=["company_id", "year"],
            how="left",
        )

        data = data.merge(
            cashflow,
            on=["company_id", "year"],
            how="left",
        )

        data = data.merge(
            companies,
            on="company_id",
            how="left",
        )

        fallback_targets = [
            "net_profit_margin_pct",
            "operating_profit_margin_pct",
            "return_on_equity_pct",
            "debt_to_equity",
            "interest_coverage",
            "asset_turnover",
            "free_cash_flow_cr",
            "capex_cr",
            "earnings_per_share",
            "book_value_per_share",
            "dividend_payout_ratio_pct",
            "total_debt_cr",
            "cash_from_operations_cr",
        ]

        available_old_columns = [
            column for column in fallback_targets if column in old_ratios.columns
        ]

        old_ratios = old_ratios[["company_id", "year"] + available_old_columns].copy()

        old_ratios = old_ratios.rename(
            columns={column: f"source_{column}" for column in available_old_columns}
        )

        data = data.merge(
            old_ratios,
            on=["company_id", "year"],
            how="left",
        )

        excluded = {
            "company_id",
            "year",
            "company_name",
            "broad_sector",
            "sub_sector",
        }

        for column in data.columns:
            if column not in excluded:
                data[column] = pd.to_numeric(
                    data[column],
                    errors="coerce",
                )

        data["year_number"] = pd.to_numeric(
            data["year"].astype(str).str.extract(r"(\d{4})", expand=False),
            errors="coerce",
        )

        data["ebit"] = [
            add_values(pbt, interest)
            for pbt, interest in zip(
                data["profit_before_tax"],
                data["interest"],
            )
        ]

        data["computed_opm_pct"] = [
            operating_profit_margin(op, sales)
            for op, sales in zip(
                data["operating_profit"],
                data["sales"],
            )
        ]

        data["net_profit_margin_pct"] = [
            net_profit_margin(pat, sales)
            for pat, sales in zip(
                data["net_profit"],
                data["sales"],
            )
        ]

        data["operating_profit_margin_pct"] = data["computed_opm_pct"]

        data["operating_profit_margin_variance_pct"] = (
            data["computed_opm_pct"] - data["opm_percentage"]
        ).abs()

        data["return_on_equity_pct"] = [
            return_on_equity(pat, equity, reserves)
            for pat, equity, reserves in zip(
                data["net_profit"],
                data["equity_capital"],
                data["reserves"],
            )
        ]

        data["return_on_capital_employed_pct"] = [
            return_on_capital_employed(
                ebit,
                equity,
                reserves,
                debt,
            )
            for ebit, equity, reserves, debt in zip(
                data["ebit"],
                data["equity_capital"],
                data["reserves"],
                data["borrowings"],
            )
        ]

        data["return_on_assets_pct"] = [
            return_on_assets(pat, assets)
            for pat, assets in zip(
                data["net_profit"],
                data["total_assets"],
            )
        ]

        data["debt_to_equity"] = [
            debt_to_equity(debt, equity, reserves)
            for debt, equity, reserves in zip(
                data["borrowings"],
                data["equity_capital"],
                data["reserves"],
            )
        ]

        data["high_leverage_flag"] = [
            high_leverage_flag(ratio, sector)
            for ratio, sector in zip(
                data["debt_to_equity"],
                data["broad_sector"],
            )
        ]

        data["interest_coverage"] = [
            interest_coverage(op, other, interest)
            for op, other, interest in zip(
                data["operating_profit"],
                data["other_income"],
                data["interest"],
            )
        ]

        data["icr_label"] = [
            icr_label(value, interest)
            for value, interest in zip(
                data["interest_coverage"],
                data["interest"],
            )
        ]

        data["icr_warning_flag"] = data["interest_coverage"].map(icr_warning_flag)

        data["net_debt_cr"] = [
            net_debt(debt, investments)
            for debt, investments in zip(
                data["borrowings"],
                data["investments"],
            )
        ]

        data["asset_turnover"] = [
            asset_turnover(sales, assets)
            for sales, assets in zip(
                data["sales"],
                data["total_assets"],
            )
        ]

        data["free_cash_flow_cr"] = [
            None if is_missing(cfo) and is_missing(cfi) else free_cash_flow(cfo, cfi)
            for cfo, cfi in zip(
                data["operating_activity"],
                data["investing_activity"],
            )
        ]

        data["capex_cr"] = data["investing_activity"].map(capital_expenditure)

        data["capex_intensity_pct"] = [
            capex_intensity(cfi, sales)
            for cfi, sales in zip(
                data["investing_activity"],
                data["sales"],
            )
        ]

        data["capex_intensity_label"] = data["capex_intensity_pct"].map(
            capex_intensity_label
        )

        data["fcf_conversion_rate_pct"] = [
            fcf_conversion_rate(fcf, op)
            for fcf, op in zip(
                data["free_cash_flow_cr"],
                data["operating_profit"],
            )
        ]

        data["earnings_per_share"] = data["eps"]

        data["book_value_per_share"] = [
            book_value_per_share(equity, reserves, face)
            for equity, reserves, face in zip(
                data["equity_capital"],
                data["reserves"],
                data["face_value"],
            )
        ]

        data["dividend_payout_ratio_pct"] = data["dividend_payout"]

        data["total_debt_cr"] = data["borrowings"]

        data["cash_from_operations_cr"] = data["operating_activity"]

        data["cfo_pat_ratio"] = [
            cfo_pat_ratio(cfo, pat)
            for cfo, pat in zip(
                data["operating_activity"],
                data["net_profit"],
            )
        ]

        add_cfo_quality(data)

        data["cfo_sign"] = [
            cashflow_sign(value) if not is_missing(value) else ""
            for value in data["operating_activity"]
        ]

        data["cfi_sign"] = [
            cashflow_sign(value) if not is_missing(value) else ""
            for value in data["investing_activity"]
        ]

        data["cff_sign"] = [
            cashflow_sign(value) if not is_missing(value) else ""
            for value in data["financing_activity"]
        ]

        data["capital_allocation_pattern"] = [
            (
                capital_allocation_pattern(cfo, cfi, cff, ratio)
                if all(not is_missing(value) for value in (cfo, cfi, cff))
                else "Insufficient Data"
            )
            for cfo, cfi, cff, ratio in zip(
                data["operating_activity"],
                data["investing_activity"],
                data["financing_activity"],
                data["cfo_pat_ratio"],
            )
        ]

        add_cagr_columns(data, "sales", "revenue")
        add_cagr_columns(data, "net_profit", "pat")
        add_cagr_columns(data, "eps", "eps")

        data["roce_sector_median_pct"] = data.groupby(["year", "broad_sector"])[
            "return_on_capital_employed_pct"
        ].transform("median")

        data["roce_vs_sector_pct"] = (
            data["return_on_capital_employed_pct"] - data["roce_sector_median_pct"]
        )

        for target in available_old_columns:
            source = f"source_{target}"

            data[target] = data[target].combine_first(data[source])

        data["composite_quality_score"] = data.apply(
            quality_score,
            axis=1,
        )

        data["id"] = range(1, len(data) + 1)

        data["high_leverage_flag"] = (
            data["high_leverage_flag"].fillna(False).astype(int)
        )

        data["icr_warning_flag"] = data["icr_warning_flag"].fillna(False).astype(int)

        capital_allocation = data[
            [
                "company_id",
                "year",
                "cfo_sign",
                "cfi_sign",
                "cff_sign",
                "capital_allocation_pattern",
            ]
        ].rename(columns={"capital_allocation_pattern": "pattern_label"})

        capital_allocation.to_csv(
            OUTPUT_DIR / "capital_allocation.csv",
            index=False,
        )

        latest = (
            data.dropna(subset=["year_number"])
            .sort_values("year_number")
            .groupby("company_id", as_index=False)
            .tail(1)
        )

        screener = latest[
            (latest["return_on_equity_pct"] > 15) & (latest["debt_to_equity"] < 1)
        ][
            [
                "company_id",
                "company_name",
                "year",
                "broad_sector",
                "return_on_equity_pct",
                "debt_to_equity",
            ]
        ].sort_values(
            "return_on_equity_pct",
            ascending=False,
        )

        screener.to_csv(
            OUTPUT_DIR / "screener_preview.csv",
            index=False,
        )

        edge_case_count = create_logs(data)

        result = data[TABLE_COLUMNS].copy()

        result.to_sql(
            "financial_ratios",
            connection,
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=200,
        )

        connection.execute("""
            CREATE UNIQUE INDEX
            IF NOT EXISTS idx_financial_ratios_company_year
            ON financial_ratios(company_id, year)
            """)

        connection.commit()

        row_count = connection.execute(
            "SELECT COUNT(*) FROM financial_ratios"
        ).fetchone()[0]

        company_count = connection.execute("""
            SELECT COUNT(DISTINCT company_id)
            FROM financial_ratios
            """).fetchone()[0]

        summary = (
            "Sprint 2 Ratio Engine Summary\n"
            f"Financial ratio rows: {row_count}\n"
            f"Companies represented: {company_count}\n"
            f"Financial-ratio columns: {len(TABLE_COLUMNS)}\n"
            f"Screener result count: {len(screener)}\n"
            f"Edge-case log entries: {edge_case_count}\n"
        )

        (OUTPUT_DIR / "ratio_engine_summary.txt").write_text(
            summary,
            encoding="utf-8",
        )

        print(summary)

        if company_count != 92:
            raise RuntimeError(f"Expected 92 companies but found {company_count}")

        if row_count < 1100:
            print(
                "WARNING: The source contains only "
                f"{row_count} valid unique company-year records."
            )

        print("Ratio engine completed successfully.")
