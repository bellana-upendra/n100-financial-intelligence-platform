"""
Sprint 4 - Investment Ranking Engine

Combines:
- Financial quality
- Peer percentile performance
- Growth performance
- Risk score

Generates final investment ranking.
"""

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/processed/nifty100.db")


def load_data():
    conn = sqlite3.connect(DB_PATH)

    ratios = pd.read_sql(
        """
        SELECT *
        FROM financial_ratios
        WHERE year = 2024
        """,
        conn,
    )

    peers = pd.read_sql(
        """
        SELECT
            company_id,
            AVG(percentile_rank) AS peer_score
        FROM peer_percentiles
        GROUP BY company_id
        """,
        conn,
    )

    conn.close()

    return ratios, peers


def normalize(series):
    minimum = series.min()
    maximum = series.max()

    if maximum == minimum:
        return pd.Series([50] * len(series))

    return ((series - minimum) / (maximum - minimum)) * 100


def generate_ranking():

    ratios, peers = load_data()

    df = ratios.merge(peers, on="company_id", how="left")

    df["peer_score"] = df["peer_score"].fillna(0) * 100

    # Financial score

    financial_columns = []

    metric_mapping = {
        "return_on_equity_pct": "roe",
        "return_on_capital_employed_pct": "roce",
        "net_profit_margin_pct": "net_profit_margin",
        "revenue_cagr_3yr": "revenue_growth",
        "pat_cagr_3yr": "pat_growth",
    }

    for source, name in metric_mapping.items():
        if source in df.columns:
            df[name + "_score"] = normalize(
                pd.to_numeric(df[source], errors="coerce").fillna(0)
            )
            financial_columns.append(name + "_score")

    df["financial_score"] = df[financial_columns].mean(axis=1)

    # Growth score
    growth_columns = [c for c in df.columns if "growth" in c and c.endswith("_score")]

    if growth_columns:
        df["growth_score"] = df[growth_columns].mean(axis=1)
    else:
        df["growth_score"] = 50

    # Risk placeholder
    df["risk_score"] = 50

    # Final investment score

    df["investment_score"] = (
        df["financial_score"] * 0.40
        + df["peer_score"] * 0.25
        + df["growth_score"] * 0.25
        + df["risk_score"] * 0.10
    )

    df["rank"] = (
        df["investment_score"]
        .fillna(0)
        .rank(ascending=False, method="dense")
        .astype(int)
    )

    return df.sort_values("rank")


if __name__ == "__main__":

    result = generate_ranking()

    print("Investment Ranking Completed")
    print("============================")
    print(
        result[["company_id", "investment_score", "rank"]]
        .head(20)
        .to_string(index=False)
    )
