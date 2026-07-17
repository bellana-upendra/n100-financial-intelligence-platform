from __future__ import annotations

import sqlite3
from datetime import datetime
import pandas as pd

from src.config import get_settings


def main():
    settings = get_settings()
    lines = ["# Sprint 1 Data Foundation Report", "", f"Generated: {datetime.now():%Y-%m-%d %H:%M}", ""]

    if settings.database_path.exists():
        with sqlite3.connect(settings.database_path) as conn:
            tables = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
                conn,
            )["name"].tolist()
            lines += ["## Database row counts", ""]
            for table in tables:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                lines.append(f"- `{table}`: {count}")
            lines.append(f"- Foreign-key violations: {len(conn.execute('PRAGMA foreign_key_check').fetchall())}")

    validation = settings.output_dir / "validation_failures.csv"
    if validation.exists():
        df = pd.read_csv(validation)
        lines += ["", "## Data-quality results", ""]
        if df.empty:
            lines.append("- No failures.")
        else:
            summary = df.groupby(["severity", "rule_id"]).size().reset_index(name="count")
            for row in summary.itertuples(index=False):
                lines.append(f"- {row.severity} {row.rule_id}: {row.count}")

    lines += [
        "", "## Sign-off", "",
        "- [ ] 92 companies loaded",
        "- [ ] Foreign-key check returns zero rows",
        "- [ ] Zero unresolved CRITICAL failures",
        "- [ ] 35+ unit tests pass",
        "- [ ] Five-company manual review completed",
        "- [ ] Sprint review signed off",
    ]

    output = settings.output_dir / "sprint1_report.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Created {output}")


if __name__ == "__main__":
    main()
