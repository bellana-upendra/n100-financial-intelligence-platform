"""
Sprint 4 - Risk Analytics Engine

Calculates company risk score using:
- Debt level
- Interest coverage
- Profit stability
"""

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/processed/nifty100.db")


def calculate_risk():

    conn = sqlite3.connect(DB_PATH)

    df = pd.read_sql(
        """
        SELECT *
        FROM financial_ratios
        WHERE year = 2024
        """,
        conn,
    )

    conn.close()

    result = df[["company_id"]].copy()

    result["risk_score"] = 50

    if "debt_to_equity" in df.columns:

        debt_score = 100 - (df["debt_to_equity"].clip(0, 2) * 50)

        result["risk_score"] = debt_score

    result["risk_category"] = pd.cut(
        result["risk_score"],
        bins=[-1, 40, 70, 100],
        labels=["High Risk", "Medium Risk", "Low Risk"],
    )

    return result


if __name__ == "__main__":

    data = calculate_risk()

    print("Risk Analytics Completed")
    print("========================")

    print(data.head(20).to_string(index=False))
