from __future__ import annotations

import sqlite3
import numpy as np
import pandas as pd

from src.config import get_settings


def main():
    settings = get_settings()
    if not settings.database_path.exists():
        raise SystemExit("Run the load target first.")

    with sqlite3.connect(settings.database_path) as conn:
        pnl = pd.read_sql_query("SELECT * FROM profitandloss", conn)
        bs = pd.read_sql_query("SELECT * FROM balancesheet", conn)
        if pnl.empty:
            raise SystemExit("profitandloss is empty.")

        df = pnl.merge(bs, on=["company_id", "year"], how="left")
        sales = df["sales"].replace(0, np.nan)
        ratios = pd.DataFrame({
            "company_id": df["company_id"],
            "year": df["year"],
            "opm_percent": df["operating_profit"] / sales * 100,
            "npm_percent": df["net_profit"] / sales * 100,
            "eps": df.get("eps"),
        })
        if {"borrowings", "equity_capital", "reserves"}.issubset(df.columns):
            equity = (df["equity_capital"].fillna(0) + df["reserves"].fillna(0)).replace(0, np.nan)
            ratios["debt_to_equity"] = df["borrowings"] / equity

        ratios.to_sql("financial_ratios", conn, if_exists="append", index=False)
        conn.commit()

    print(f"Calculated {len(ratios)} ratio rows.")


if __name__ == "__main__":
    main()
