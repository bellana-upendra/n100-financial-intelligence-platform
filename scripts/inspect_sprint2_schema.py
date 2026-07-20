from pathlib import Path
import sqlite3

DB_PATH = Path("data/processed/nifty100.db")

TABLES = [
    "companies",
    "profitandloss",
    "balancesheet",
    "cashflow",
    "financial_ratios",
]

with sqlite3.connect(DB_PATH) as conn:
    print(f"Database: {DB_PATH.resolve()}\n")

    for table in TABLES:
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

        columns = [
            row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        ]

        print(f"{table}: {count} rows")
        print("Columns:", ", ".join(columns))
        print("-" * 80)

    union_count = conn.execute("""
        SELECT COUNT(*)
        FROM (
            SELECT company_id, year FROM profitandloss
            UNION
            SELECT company_id, year FROM balancesheet
            UNION
            SELECT company_id, year FROM cashflow
        )
    """).fetchone()[0]

    print("Distinct company-year union:", union_count)
