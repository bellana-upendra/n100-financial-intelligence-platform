def free_cash_flow(cfo, cfi):
    return (cfo or 0) + (cfi or 0)


def cfo_pat_ratio(cfo, pat):
    if pat in (None, 0):
        return None
    return cfo / pat


def cfo_quality_label(five_year_average):
    if five_year_average is None:
        return None
    if five_year_average > 1:
        return "High Quality"
    if five_year_average >= 0.5:
        return "Moderate"
    return "Accrual Risk"


def capex_intensity(investing_activity, sales):
    if sales in (None, 0):
        return None
    return abs(investing_activity or 0) / sales * 100


def capex_label(intensity):
    if intensity is None:
        return None
    if intensity < 3:
        return "Asset Light"
    if intensity <= 8:
        return "Moderate"
    return "Capital Intensive"


def fcf_conversion_rate(fcf, operating_profit):
    if operating_profit in (None, 0):
        return None
    return fcf / operating_profit * 100


def cashflow_sign(value):
    return "+" if (value or 0) >= 0 else "-"


def capital_allocation_pattern(cfo, cfi, cff, cfo_pat=None):
    pattern = (
        cashflow_sign(cfo),
        cashflow_sign(cfi),
        cashflow_sign(cff),
    )

    if pattern == ("+", "-", "-"):
        if cfo_pat is not None and cfo_pat > 1:
            return "Shareholder Returns"
        return "Reinvestor"

    patterns = {
        ("+", "+", "-"): "Liquidating Assets",
        ("-", "+", "+"): "Distress Signal",
        ("-", "-", "+"): "Growth Funded by Debt",
        ("+", "+", "+"): "Cash Accumulator",
        ("-", "-", "-"): "Pre-Revenue",
        ("+", "-", "+"): "Mixed",
        ("-", "+", "-"): "Mixed",
    }

    return patterns[pattern]


def average_cfo_pat_ratio(cfo_values, pat_values=None):
    """
    Calculate the average CFO/PAT ratio.

    Usage:
    average_cfo_pat_ratio([100, 120], [80, 100])
    average_cfo_pat_ratio([1.25, 1.20])
    """
    if cfo_values is None:
        return None

    if pat_values is None:
        values = (
            [cfo_values] if isinstance(cfo_values, (int, float)) else list(cfo_values)
        )
        valid_ratios = [value for value in values if value is not None]
    else:
        cfo_list = (
            [cfo_values] if isinstance(cfo_values, (int, float)) else list(cfo_values)
        )
        pat_list = (
            [pat_values] if isinstance(pat_values, (int, float)) else list(pat_values)
        )

        valid_ratios = [
            cfo_pat_ratio(cfo, pat)
            for cfo, pat in zip(cfo_list, pat_list)
            if pat not in (None, 0)
        ]

    if not valid_ratios:
        return None

    return sum(valid_ratios) / len(valid_ratios)


def capital_expenditure(investing_activity):
    """Return capital expenditure as a positive amount."""
    return abs(investing_activity or 0)


def capex_intensity_label(intensity):
    """Return the CapEx intensity classification."""
    return capex_label(intensity)
