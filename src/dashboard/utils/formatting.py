"""Formatting helpers for dashboard metrics."""

import pandas as pd


def format_metric(
    value,
    suffix: str = "",
    decimals: int = 2,
) -> str:
    if value is None or pd.isna(value):
        return "N/A"

    return f"{value:,.{decimals}f}{suffix}"


def latest_value(data: pd.DataFrame, column: str):
    if data.empty or column not in data.columns:
        return None

    values = data[column].dropna()

    if values.empty:
        return None

    return values.iloc[-1]
