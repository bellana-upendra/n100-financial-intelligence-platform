from pathlib import Path
import sqlite3
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_settings

settings = get_settings()
output_path = settings.output_dir / "manual_ratio_spot_check.csv"

query = """
SELECT
    f.company_id,
    f.year,
    p.net_profit,
    b.equity_capital,
    b.reserves,
    f.return_on_equity_pct AS database_roe,
    p.sales AS current_sales,
    previous.sales AS sales_five_years_ago,
    f.revenue_cagr_5yr AS database_revenue_cagr_5yr
FROM financial_ratios AS f
JOIN profitandloss AS p
  ON f.company_id = p.company_id
 AND f.year = p.year
JOIN balancesheet AS b
  ON f.company_id = b.company_id
 AND f.year = b.year
JOIN profitandloss AS previous
  ON previous.company_id = f.company_id
 AND CAST(previous.year AS INTEGER)
     = CAST(f.year AS INTEGER) - 5
WHERE f.company_id IN (?, ?, ?)
  AND CAST(f.year AS INTEGER) = ?
ORDER BY f.company_id
"""

with sqlite3.connect(settings.database_path) as connection:
    data = pd.read_sql_query(
        query,
        connection,
        params=["BEL", "HDFCBANK", "TCS", 2024],
    )

data["manual_roe"] = (
    data["net_profit"] / (data["equity_capital"] + data["reserves"]) * 100
)

data["manual_revenue_cagr_5yr"] = (
    (data["current_sales"] / data["sales_five_years_ago"]) ** (1 / 5) - 1
) * 100

data["roe_difference"] = (data["manual_roe"] - data["database_roe"]).abs()

data["cagr_difference"] = (
    data["manual_revenue_cagr_5yr"] - data["database_revenue_cagr_5yr"]
).abs()

data["status"] = ((data["roe_difference"] < 0.1) & (data["cagr_difference"] < 0.1)).map(
    {
        True: "PASS",
        False: "CHECK",
    }
)

data.to_csv(output_path, index=False)

print(data.to_string(index=False))
print(f"\nSaved: {output_path}")
