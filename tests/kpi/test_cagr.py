import pytest

from src.analytics.cagr import (
    BOTH_NEGATIVE,
    CAGR_OK,
    DECLINE_TO_LOSS,
    INSUFFICIENT,
    TURNAROUND,
    ZERO_BASE,
    calculate_cagr,
)


def test_normal_five_year_cagr():
    value, flag = calculate_cagr(100, 161.051, 5)

    assert value == pytest.approx(10.0, abs=0.001)
    assert flag == CAGR_OK


def test_normal_three_year_cagr():
    value, flag = calculate_cagr(100, 133.1, 3)

    assert value == pytest.approx(10.0, abs=0.001)
    assert flag == CAGR_OK


def test_positive_value_declining_to_zero():
    value, flag = calculate_cagr(100, 0, 5)

    assert value == pytest.approx(-100.0)
    assert flag == CAGR_OK


def test_decline_to_loss():
    value, flag = calculate_cagr(100, -20, 5)

    assert value is None
    assert flag == DECLINE_TO_LOSS


def test_turnaround():
    value, flag = calculate_cagr(-100, 50, 5)

    assert value is None
    assert flag == TURNAROUND


def test_both_values_negative():
    value, flag = calculate_cagr(-100, -50, 5)

    assert value is None
    assert flag == BOTH_NEGATIVE


def test_zero_base():
    value, flag = calculate_cagr(0, 100, 5)

    assert value is None
    assert flag == ZERO_BASE


def test_insufficient_history():
    value, flag = calculate_cagr(
        100,
        150,
        5,
        sufficient=False,
    )

    assert value is None
    assert flag == INSUFFICIENT


def test_missing_start_value():
    value, flag = calculate_cagr(None, 150, 5)

    assert value is None
    assert flag == INSUFFICIENT


def test_missing_end_value():
    value, flag = calculate_cagr(100, None, 5)

    assert value is None
    assert flag == INSUFFICIENT
