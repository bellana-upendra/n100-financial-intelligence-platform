import pytest

from src.analytics.cashflow_kpis import (
    average_cfo_pat_ratio,
    capex_intensity,
    capex_intensity_label,
    capital_allocation_pattern,
    capital_expenditure,
    cfo_pat_ratio,
    cfo_quality_label,
    fcf_conversion_rate,
    free_cash_flow,
)


def test_free_cash_flow_positive():
    assert free_cash_flow(500, -200) == pytest.approx(300)


def test_negative_free_cash_flow_is_allowed():
    assert free_cash_flow(100, -300) == pytest.approx(-200)


def test_cfo_pat_zero_pat_returns_none():
    assert cfo_pat_ratio(500, 0) is None


def test_five_year_average_cfo_pat_ratio():
    result = average_cfo_pat_ratio(
        [100, 110, 120, 130, 140],
        [100, 100, 100, 100, 100],
    )
    assert result == pytest.approx(1.2)


def test_cfo_quality_labels():
    assert cfo_quality_label(1.2) == "High Quality"
    assert cfo_quality_label(0.75) == "Moderate"
    assert cfo_quality_label(0.3) == "Accrual Risk"


def test_capital_expenditure_absolute_value():
    assert capital_expenditure(-250) == pytest.approx(250)


def test_capex_intensity_and_labels():
    assert capex_intensity(-20, 1000) == pytest.approx(2)
    assert capex_intensity_label(2) == "Asset Light"
    assert capex_intensity_label(5) == "Moderate"
    assert capex_intensity_label(10) == "Capital Intensive"


def test_fcf_conversion_zero_operating_profit():
    assert fcf_conversion_rate(100, 0) is None


def test_reinvestor_and_shareholder_returns():
    assert capital_allocation_pattern(500, -200, -100, 0.8) == "Reinvestor"
    assert capital_allocation_pattern(500, -200, -100, 1.2) == "Shareholder Returns"


def test_remaining_capital_allocation_patterns():
    assert capital_allocation_pattern(100, 50, -20) == "Liquidating Assets"
    assert capital_allocation_pattern(-100, 50, 20) == "Distress Signal"
    assert capital_allocation_pattern(-100, -50, 20) == "Growth Funded by Debt"
    assert capital_allocation_pattern(100, 50, 20) == "Cash Accumulator"
    assert capital_allocation_pattern(-100, -50, -20) == "Pre-Revenue"
    assert capital_allocation_pattern(100, -50, 20) == "Mixed"
