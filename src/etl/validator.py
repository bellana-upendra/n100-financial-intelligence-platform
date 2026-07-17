from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


FAILURE_COLUMNS = [
    "rule_id", "severity", "table", "row_index",
    "company_id", "year", "message"
]


def failure(rule_id, severity, table, row_index, message, row=None):
    row = row if row is not None else pd.Series(dtype=object)
    return {
        "rule_id": rule_id,
        "severity": severity,
        "table": table,
        "row_index": row_index,
        "company_id": row.get("company_id"),
        "year": row.get("year"),
        "message": message,
    }


def selected_rows(df, mask):
    for idx in df.index[mask.fillna(False)]:
        yield int(idx), df.loc[idx]


def validate_frames(frames: dict[str, pd.DataFrame], db_path: Path) -> pd.DataFrame:
    failures = []
    companies = frames.get("companies", pd.DataFrame())

    if not companies.empty and "company_id" in companies:
        mask = companies["company_id"].isna() | companies["company_id"].duplicated(keep=False)
        for idx, row in selected_rows(companies, mask):
            failures.append(failure("DQ-01", "CRITICAL", "companies", idx, "company_id is null or duplicated", row))

    for table in ("profitandloss", "balancesheet", "cashflow", "financial_ratios"):
        df = frames.get(table, pd.DataFrame())
        if not df.empty and {"company_id", "year"}.issubset(df.columns):
            mask = df[["company_id", "year"]].isna().any(axis=1) | df.duplicated(["company_id", "year"], keep=False)
            for idx, row in selected_rows(df, mask):
                failures.append(failure("DQ-02", "CRITICAL", table, idx, "(company_id, year) is null or duplicated", row))

    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        for item in fk_rows:
            failures.append(failure("DQ-03", "CRITICAL", str(item[0]), item[1], f"Foreign-key violation: {item}"))

    bs = frames.get("balancesheet", pd.DataFrame())
    if not bs.empty and {"total_assets", "total_liabilities"}.issubset(bs.columns):
        denom = bs[["total_assets", "total_liabilities"]].abs().max(axis=1).replace(0, np.nan)
        diff_pct = (bs["total_assets"] - bs["total_liabilities"]).abs() / denom
        for idx, row in selected_rows(bs, diff_pct > 0.01):
            failures.append(failure("DQ-04", "WARNING", "balancesheet", idx, "Assets and liabilities differ by more than 1%", row))

    pnl = frames.get("profitandloss", pd.DataFrame())
    if not pnl.empty and {"sales", "operating_profit", "opm_percent"}.issubset(pnl.columns):
        expected = np.where(pnl["sales"].abs() > 0, pnl["operating_profit"] / pnl["sales"] * 100, np.nan)
        mask = (pd.Series(expected, index=pnl.index) - pnl["opm_percent"]).abs() > 0.5
        for idx, row in selected_rows(pnl, mask):
            failures.append(failure("DQ-05", "WARNING", "profitandloss", idx, "OPM cross-check failed", row))

    if not pnl.empty and "sales" in pnl:
        for idx, row in selected_rows(pnl, pnl["sales"] <= 0):
            failures.append(failure("DQ-06", "WARNING", "profitandloss", idx, "Sales must be positive", row))

    cf = frames.get("cashflow", pd.DataFrame())
    needed = {"cash_from_operating", "cash_from_investing", "cash_from_financing", "net_cash_flow"}
    if not cf.empty and needed.issubset(cf.columns):
        expected = cf["cash_from_operating"] + cf["cash_from_investing"] + cf["cash_from_financing"]
        for idx, row in selected_rows(cf, (expected - cf["net_cash_flow"]).abs() > 1.0):
            failures.append(failure("DQ-07", "WARNING", "cashflow", idx, "Net cash reconciliation failed", row))

    if not pnl.empty and {"tax", "profit_before_tax"}.issubset(pnl.columns):
        rate = pd.Series(np.where(pnl["profit_before_tax"] > 0, pnl["tax"] / pnl["profit_before_tax"] * 100, np.nan), index=pnl.index)
        for idx, row in selected_rows(pnl, (rate < 0) | (rate > 60)):
            failures.append(failure("DQ-08", "WARNING", "profitandloss", idx, "Tax rate outside 0%-60%", row))

    if not pnl.empty and "dividend_payout_percent" in pnl:
        mask = (pnl["dividend_payout_percent"] < 0) | (pnl["dividend_payout_percent"] > 100)
        for idx, row in selected_rows(pnl, mask):
            failures.append(failure("DQ-09", "WARNING", "profitandloss", idx, "Dividend payout outside 0%-100%", row))

    url_re = re.compile(r"^https?://", re.I)
    for table, column in (("companies", "website_url"), ("documents", "document_url")):
        df = frames.get(table, pd.DataFrame())
        if not df.empty and column in df:
            mask = df[column].notna() & ~df[column].astype(str).str.match(url_re)
            for idx, row in selected_rows(df, mask):
                failures.append(failure("DQ-10", "WARNING", table, idx, f"Invalid URL in {column}", row))

    if not pnl.empty and {"eps", "net_profit"}.issubset(pnl.columns):
        mask = ((pnl["eps"] < 0) & (pnl["net_profit"] > 0)) | ((pnl["eps"] > 0) & (pnl["net_profit"] < 0))
        for idx, row in selected_rows(pnl, mask):
            failures.append(failure("DQ-11", "WARNING", "profitandloss", idx, "EPS sign inconsistent with net profit", row))

    if not companies.empty:
        ids = [c for c in ("bse_code", "ticker", "nse_symbol") if c in companies]
        if ids:
            for idx, row in selected_rows(companies, companies[ids].isna().all(axis=1)):
                failures.append(failure("DQ-12", "WARNING", "companies", idx, "No BSE/NSE/ticker identifier", row))

    for table in ("profitandloss", "balancesheet", "cashflow"):
        df = frames.get(table, pd.DataFrame())
        if not df.empty and {"company_id", "year"}.issubset(df.columns):
            coverage = df.groupby("company_id")["year"].nunique()
            for company_id, count in coverage[coverage < 5].items():
                failures.append(failure("DQ-13", "WARNING", table, None, f"Only {int(count)} years available", pd.Series({"company_id": company_id})))

    prices = frames.get("stock_prices", pd.DataFrame())
    if not prices.empty and "close" in prices:
        for idx, row in selected_rows(prices, prices["close"] <= 0):
            failures.append(failure("DQ-14", "CRITICAL", "stock_prices", idx, "Close price must be positive", row))

    for table in ("profitandloss", "balancesheet", "cashflow", "financial_ratios"):
        df = frames.get(table, pd.DataFrame())
        if not df.empty and "year" in df:
            mask = df["year"].isna() | (df["year"] < 1990) | (df["year"] > 2100)
            for idx, row in selected_rows(df, mask):
                failures.append(failure("DQ-15", "CRITICAL", table, idx, "Invalid year", row))

    required = {
        "companies": ["company_id", "company_name"],
        "sectors": ["sector_id", "sector_name"],
        "profitandloss": ["company_id", "year"],
        "balancesheet": ["company_id", "year"],
        "cashflow": ["company_id", "year"],
        "stock_prices": ["company_id", "date", "close"],
    }
    for table, columns in required.items():
        df = frames.get(table, pd.DataFrame())
        if df.empty:
            failures.append(failure("DQ-16", "CRITICAL", table, None, "Required table is missing or empty"))
            continue
        for column in [c for c in columns if c not in df.columns]:
            failures.append(failure("DQ-16", "CRITICAL", table, None, f"Missing required column: {column}"))
        existing = [c for c in columns if c in df.columns]
        if existing:
            for idx, row in selected_rows(df, df[existing].isna().any(axis=1)):
                failures.append(failure("DQ-16", "CRITICAL", table, idx, "Required field contains null", row))

    return pd.DataFrame(failures, columns=FAILURE_COLUMNS)
