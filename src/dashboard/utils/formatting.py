"""Display and data-cleaning helpers for dashboard pages."""

from __future__ import annotations

import math
import pandas as pd


def format_metric(value, suffix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        if pd.isna(value) or not math.isfinite(float(value)):
            return "N/A"
        return f"{float(value):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def latest_value(data: pd.DataFrame, column: str):
    if data.empty or column not in data.columns:
        return None
    values = pd.to_numeric(data[column], errors="coerce").dropna()
    return None if values.empty else values.iloc[-1]


def clean_year(data: pd.DataFrame) -> pd.DataFrame:
    result = data.copy()
    if "year" in result.columns:
        result["year"] = pd.to_numeric(result["year"], errors="coerce")
        result = result.dropna(subset=["year"])
        result["year"] = result["year"].astype(int)
    return result
