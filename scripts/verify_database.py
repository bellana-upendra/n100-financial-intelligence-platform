from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_settings

TABLES = [
    "sectors",
    "companies",
    "profitandloss",
    "balancesheet",
    "cashflow",
    "analysis",
    "documents",
    "prosandcons",
    "stock_prices",
    "peer_groups",
    "financial_ratios",
    "market_cap",
]


def main():
    settings = get_settings()
    if not settings.database_path.exists():
        raise SystemExit(f"Database not found: {settings.database_path}")

    with sqlite3.connect(settings.database_path) as conn:
        for table in TABLES:
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                print(f"{table}: {count}")
            except sqlite3.Error as exc:
                print(f"{table}: ERROR - {exc}")
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        print(f"foreign_key_violations: {len(fk_rows)}")

    validation = settings.output_dir / "validation_failures.csv"
    if validation.exists():
        df = pd.read_csv(validation)
        critical = int((df.get("severity") == "CRITICAL").sum()) if not df.empty else 0
        print(f"critical_validation_failures: {critical}")


if __name__ == "__main__":
    main()
