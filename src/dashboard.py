from __future__ import annotations

import sqlite3
import pandas as pd
from src.config import get_settings


def main():
    settings = get_settings()
    if not settings.database_path.exists():
        raise SystemExit("Run the load target first.")

    with sqlite3.connect(settings.database_path) as conn:
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            conn,
        )["name"].tolist()
        rows = []
        for table in sorted(tables):
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            rows.append({"table": table, "row_count": count})

    output = settings.output_dir / "dashboard_summary.csv"
    pd.DataFrame(rows).to_csv(output, index=False)
    print(f"Created {output}")


if __name__ == "__main__":
    main()
