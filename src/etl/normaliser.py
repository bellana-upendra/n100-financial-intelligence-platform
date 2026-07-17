from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


def normalize_year(value: Any) -> int | None:
    """Convert common year and financial-year formats to an integer year."""

    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, pd.Timestamp):
        return int(value.year)

    if hasattr(value, "year") and not isinstance(value, str):
        try:
            year = int(value.year)
            return year if 1900 <= year <= 2100 else None
        except (TypeError, ValueError):
            pass

    if isinstance(value, (int, float)):
        year = int(value)
        return year if 1900 <= year <= 2100 else None

    text = str(value).strip().upper()

    if not text:
        return None

    # Plain year: 2024
    if re.fullmatch(r"(?:19|20)\d{2}", text):
        return int(text)

    # FY2024 or FY 2024
    match = re.fullmatch(r"FY\s*((?:19|20)\d{2})", text)

    if match:
        return int(match.group(1))

    # FY24, FY'24 or FY 24
    match = re.fullmatch(r"FY\s*'?\s*(\d{2})", text)

    if match:
        return 2000 + int(match.group(1))

    # Financial-year ranges: 2023-24, 2023/24, 2023-2024
    match = re.fullmatch(
        r"((?:19|20)\d{2})\s*[-/]\s*(\d{2}|(?:19|20)\d{2})",
        text,
    )

    if match:
        start_year = int(match.group(1))
        end_text = match.group(2)

        if len(end_text) == 4:
            return int(end_text)

        end_year = (start_year // 100) * 100 + int(end_text)

        if end_year < start_year:
            end_year += 100

        return end_year

    # ISO date: 2024-03-31 or 2024/03/31
    match = re.fullmatch(
        r"((?:19|20)\d{2})[-/]\d{1,2}[-/]\d{1,2}",
        text,
    )

    if match:
        return int(match.group(1))

    # Day-first date: 31-03-2024 or 31/03/2024
    match = re.fullmatch(
        r"\d{1,2}[-/]\d{1,2}[-/]((?:19|20)\d{2})",
        text,
    )

    if match:
        return int(match.group(1))

    # Month-year values: Mar-24 or March 2024
    month_pattern = (
        r"(?:JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|"
        r"JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|"
        r"OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
    )

    match = re.fullmatch(
        rf"{month_pattern}\s*[-/' ]\s*(\d{{2}}|(?:19|20)\d{{2}})",
        text,
    )

    if match:
        year_text = match.group(1)

        if len(year_text) == 4:
            return int(year_text)

        return 2000 + int(year_text)

    return None


def normalize_ticker(value: Any) -> str | None:
    """Normalize NSE/BSE ticker values."""

    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    # Remove exchange prefixes.
    text = re.sub(
        r"^(NSE|BSE)\s*[:\-]\s*",
        "",
        text,
    )

    # Remove Yahoo Finance suffixes.
    text = re.sub(
        r"\.(NS|BO)$",
        "",
        text,
    )

    # Standardize spaces and underscores.
    text = text.replace(" ", "")
    text = text.replace("_", "-")

    # Keep letters, numbers, &, periods and hyphens.
    text = re.sub(
        r"[^A-Z0-9&.\-]",
        "",
        text,
    )

    # Replace multiple hyphens with one.
    text = re.sub(
        r"-{2,}",
        "-",
        text,
    )

    return text or None


def snake_case(name: Any) -> str:
    """Convert a source column name to lowercase snake_case."""

    text = str(name).strip()

    text = re.sub(
        r"[%/()]+",
        " ",
        text,
    )

    text = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        text,
    )

    text = re.sub(
        r"_+",
        "_",
        text,
    )

    return text.strip("_").lower()


def normalise_dataframe(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """Apply standard column and value normalization to a DataFrame."""

    result = dataframe.copy()

    result.columns = [
        snake_case(column)
        for column in result.columns
    ]

    # Remove completely empty rows.
    result = result.dropna(
        how="all",
    ).reset_index(drop=True)

    # Normalize year.
    if "year" in result.columns:
        result["year"] = (
            result["year"]
            .map(normalize_year)
            .astype("Int64")
        )

    # Normalize ticker-type columns.
    for column in (
        "ticker",
        "nse_symbol",
    ):
        if column in result.columns:
            result[column] = result[column].map(
                normalize_ticker
            )

    # Normalize date fields.
    for column in (
        "listing_date",
        "document_date",
        "date",
    ):
        if column in result.columns:
            result[column] = (
                pd.to_datetime(
                    result[column],
                    errors="coerce",
                )
                .dt.strftime("%Y-%m-%d")
            )

    return result