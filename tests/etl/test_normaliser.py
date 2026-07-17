import pytest

from src.etl.normaliser import normalize_ticker, normalize_year


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2024, 2024),
        (2024.0, 2024),
        ("2024", 2024),
        ("FY2024", 2024),
        ("FY 2024", 2024),
        ("FY24", 2024),
        ("FY'24", 2024),
        ("2023-24", 2024),
        ("2023/24", 2024),
        ("2023-2024", 2024),
        ("31-03-2024", 2024),
        ("Mar-24", 2024),
        ("March 2024", 2024),
        ("2024-03-31", 2024),
        (" 2025 ", 2025),
        (None, None),
        ("", None),
        ("not-a-year", None),
        (1899, None),
        (2200, None),
    ],
)
def test_normalize_year(value, expected):
    assert normalize_year(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("reliance", "RELIANCE"),
        (" RELIANCE ", "RELIANCE"),
        ("NSE:RELIANCE", "RELIANCE"),
        ("BSE: 500325", "500325"),
        ("RELIANCE.NS", "RELIANCE"),
        ("500325.BO", "500325"),
        ("M&M", "M&M"),
        ("BAJAJ-AUTO", "BAJAJ-AUTO"),
        ("BAJAJ_AUTO", "BAJAJ-AUTO"),
        ("abc ltd", "ABCLTD"),
        ("A--B", "A-B"),
        ("A@B", "AB"),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_ticker(value, expected):
    assert normalize_ticker(value) == expected
