from __future__ import annotations

import math

CAGR_OK = "OK"
DECLINE_TO_LOSS = "DECLINE_TO_LOSS"
TURNAROUND = "TURNAROUND"
BOTH_NEGATIVE = "BOTH_NEGATIVE"
ZERO_BASE = "ZERO_BASE"
INSUFFICIENT = "INSUFFICIENT"


def _number(value):
    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(result):
        return None

    return result


def calculate_cagr(
    start_value,
    end_value,
    years,
    sufficient=True,
):
    """
    Calculate CAGR and return a tuple containing:
    (calculated_value, status_flag)
    """
    start_value = _number(start_value)
    end_value = _number(end_value)

    if (
        not sufficient
        or start_value is None
        or end_value is None
        or years is None
        or years <= 0
    ):
        return None, INSUFFICIENT

    if start_value == 0:
        return None, ZERO_BASE

    if start_value > 0 and end_value < 0:
        return None, DECLINE_TO_LOSS

    if start_value < 0 and end_value > 0:
        return None, TURNAROUND

    if start_value < 0 and end_value < 0:
        return None, BOTH_NEGATIVE

    if start_value > 0 and end_value >= 0:
        cagr = ((end_value / start_value) ** (1 / years) - 1) * 100

        return cagr, CAGR_OK

    return None, INSUFFICIENT
