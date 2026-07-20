from __future__ import annotations

import math


def _number(value):
    """Convert a value to float and treat None/NaN as missing."""
    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result):
        return None

    return result


def net_profit_margin(net_profit, sales):
    net_profit = _number(net_profit)
    sales = _number(sales)

    if net_profit is None or sales in (None, 0):
        return None

    return (net_profit / sales) * 100


def operating_profit_margin(operating_profit, sales):
    operating_profit = _number(operating_profit)
    sales = _number(sales)

    if operating_profit is None or sales in (None, 0):
        return None

    return (operating_profit / sales) * 100


def opm_mismatch(computed_opm, source_opm, tolerance=1.0):
    computed_opm = _number(computed_opm)
    source_opm = _number(source_opm)

    if computed_opm is None or source_opm is None:
        return False

    return abs(computed_opm - source_opm) > tolerance


def return_on_equity(net_profit, equity_capital, reserves):
    net_profit = _number(net_profit)
    equity_capital = _number(equity_capital) or 0
    reserves = _number(reserves) or 0

    equity = equity_capital + reserves

    if net_profit is None or equity <= 0:
        return None

    return (net_profit / equity) * 100


def return_on_capital_employed(
    ebit,
    equity_capital,
    reserves,
    borrowings,
):
    ebit = _number(ebit)
    equity_capital = _number(equity_capital) or 0
    reserves = _number(reserves) or 0
    borrowings = _number(borrowings) or 0

    capital_employed = equity_capital + reserves + borrowings

    if ebit is None or capital_employed <= 0:
        return None

    return (ebit / capital_employed) * 100


def return_on_assets(net_profit, total_assets):
    net_profit = _number(net_profit)
    total_assets = _number(total_assets)

    if net_profit is None or total_assets in (None, 0):
        return None

    return (net_profit / total_assets) * 100


def debt_to_equity(borrowings, equity_capital, reserves):
    borrowings = _number(borrowings) or 0

    # Debt-free companies must return zero.
    if borrowings == 0:
        return 0.0

    equity_capital = _number(equity_capital) or 0
    reserves = _number(reserves) or 0
    equity = equity_capital + reserves

    if equity <= 0:
        return None

    return borrowings / equity


def high_leverage_flag(de_ratio, broad_sector):
    de_ratio = _number(de_ratio)

    if de_ratio is None or broad_sector is None:
        return False

    is_financial = str(broad_sector).strip().casefold() == "financials"

    return de_ratio > 5 and not is_financial


def interest_coverage(operating_profit, other_income, interest):
    operating_profit = _number(operating_profit)
    other_income = _number(other_income)
    interest = _number(interest)

    if interest in (None, 0):
        return None

    ebit_proxy = (operating_profit or 0) + (other_income or 0)
    return ebit_proxy / interest


def icr_label(icr, interest=None):
    interest = _number(interest)
    icr = _number(icr)

    if interest == 0:
        return "Debt Free"

    if icr is None:
        return "Not Available"

    return f"{icr:.2f}x"


def icr_warning_flag(icr):
    icr = _number(icr)
    return icr is not None and icr < 1.5


def net_debt(borrowings, investments):
    borrowings = _number(borrowings) or 0
    investments = _number(investments) or 0

    return borrowings - investments


def asset_turnover(sales, total_assets):
    sales = _number(sales)
    total_assets = _number(total_assets)

    if sales is None or total_assets in (None, 0):
        return None

    return sales / total_assets
