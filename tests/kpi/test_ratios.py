import pytest

from src.analytics.ratios import (
    asset_turnover,
    debt_to_equity,
    high_leverage_flag,
    icr_label,
    icr_warning_flag,
    interest_coverage,
    net_debt,
    net_profit_margin,
    operating_profit_margin,
    opm_mismatch,
    return_on_assets,
    return_on_capital_employed,
    return_on_equity,
)

# Day 08 — eight profitability tests


def test_net_profit_margin_normal():
    assert net_profit_margin(200, 1000) == pytest.approx(20.0)


def test_net_profit_margin_zero_sales():
    assert net_profit_margin(200, 0) is None


def test_operating_profit_margin_normal():
    assert operating_profit_margin(150, 1000) == pytest.approx(15.0)


def test_opm_difference_over_one_percent_is_mismatch():
    assert opm_mismatch(15.5, 14.0) is True
    assert opm_mismatch(15.0, 14.5) is False


def test_return_on_equity_normal():
    result = return_on_equity(120, 200, 400)
    assert result == pytest.approx(20.0)


def test_return_on_equity_negative_equity():
    assert return_on_equity(120, 100, -200) is None


def test_return_on_capital_employed_normal():
    result = return_on_capital_employed(180, 200, 400, 300)
    assert result == pytest.approx(20.0)


def test_return_on_assets_zero_assets():
    assert return_on_assets(100, 0) is None


# Day 09 — eight leverage and efficiency tests


def test_debt_to_equity_debt_free_returns_zero():
    assert debt_to_equity(0, 200, 300) == 0.0


def test_debt_to_equity_normal():
    result = debt_to_equity(250, 200, 300)
    assert result == pytest.approx(0.5)


def test_high_leverage_flag_for_non_financial_company():
    assert high_leverage_flag(6.0, "Industrials") is True


def test_high_leverage_suppressed_for_financials():
    assert high_leverage_flag(8.0, "Financials") is False


def test_interest_coverage_zero_interest_returns_none():
    assert interest_coverage(200, 20, 0) is None


def test_icr_label_for_debt_free_company():
    assert icr_label(None, interest=0) == "Debt Free"


def test_low_interest_coverage_warning():
    assert icr_warning_flag(1.2) is True
    assert icr_warning_flag(2.0) is False


def test_net_debt_and_asset_turnover():
    assert net_debt(500, 150) == pytest.approx(350)
    assert asset_turnover(1000, 800) == pytest.approx(1.25)
