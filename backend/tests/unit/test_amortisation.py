"""Amortisation engine (FINANCIAL_CALCULATIONS.md §3).

Three independent kinds of evidence:

* golden vectors copied from the specification's worked examples and from spreadsheet
  ``PMT`` values;
* a cross-check against ``numpy-financial`` (a test-only dependency, never used by the app);
* property-based tests over random valid inputs.
"""

from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal

import numpy_financial as npf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.loans.engine.amortisation import (
    VERSION,
    LoanTerms,
    ScheduleError,
    build_schedule,
    level_instalment,
)
from app.modules.loans.engine.calendar import add_months

D = Decimal
START = date(2026, 11, 18)


def _schedule(amount: str, rate: str, term: int, **extra: str) -> object:
    terms = LoanTerms(D(amount), D(rate), term, **{k: D(v) for k, v in extra.items()})
    return build_schedule(terms, START)


# --- Golden vectors --------------------------------------------------------------------------


def test_worked_example_a() -> None:
    """§3.1: 100,000.00 at 12 % over 12 months; matches spreadsheet PMT(1%,12,-100000)."""
    schedule = build_schedule(LoanTerms(D("100000.00"), D("0.12"), 12), START)
    rows = schedule.instalments
    assert schedule.level_instalment == D("8884.88")
    expected = {
        1: ("8884.88", "1000.00", "7884.88", "92115.12"),
        2: ("8884.88", "921.15", "7963.73", "84151.39"),
        3: ("8884.88", "841.51", "8043.37", "76108.02"),
        11: ("8884.88", "175.07", "8709.81", "8796.88"),
        12: ("8884.85", "87.97", "8796.88", "0.00"),
    }
    for number, (instalment, interest, principal, closing) in expected.items():
        row = rows[number - 1]
        assert (row.instalment, row.interest, row.principal, row.closing_balance) == (
            D(instalment),
            D(interest),
            D(principal),
            D(closing),
        )
    assert schedule.total_interest == D("6618.53")
    assert schedule.total_repayable == D("106618.53")


def test_worked_example_b() -> None:
    """§3.2: 30,000.00 at 21.5 % over 24 months."""
    schedule = build_schedule(LoanTerms(D("30000.00"), D("0.215"), 24), START)
    assert schedule.level_instalment == D("1548.95")
    assert schedule.instalments[-1].instalment == D("1548.87")
    assert schedule.total_interest == D("7174.72")
    # §7 relies on this balance after instalment 3.
    assert schedule.instalments[2].closing_balance == D("26910.96")


@pytest.mark.parametrize(
    ("amount", "rate", "term", "instalment"),
    [
        # Spreadsheet PMT values, rounded to cents.
        ("250000.00", "0.115", 120, "3514.89"),  # long term
        ("10000.00", "0.60", 6, "1970.17"),  # high rate
        ("5000.00", "0.24", 1, "5100.00"),  # one month: principal plus one month's interest
    ],
)
def test_golden_instalments(amount: str, rate: str, term: int, instalment: str) -> None:
    schedule = build_schedule(LoanTerms(D(amount), D(rate), term), START)
    assert schedule.level_instalment == D(instalment)
    assert schedule.instalments[-1].closing_balance == D("0.00")


def test_one_month_term_has_a_single_closing_instalment() -> None:
    schedule = build_schedule(LoanTerms(D("5000.00"), D("0.24"), 1), START)
    (row,) = schedule.instalments
    assert (row.interest, row.principal, row.instalment) == (
        D("100.00"),
        D("5000.00"),
        D("5100.00"),
    )


def test_zero_rate_splits_evenly_and_the_last_instalment_absorbs_the_remainder() -> None:
    schedule = build_schedule(LoanTerms(D("1000.00"), D("0"), 3), START)
    assert [row.instalment for row in schedule.instalments] == [
        D("333.33"),
        D("333.33"),
        D("333.34"),
    ]
    assert schedule.total_interest == D("0.00")


def test_fees_service_fee_is_added_and_initiation_fee_is_capitalised() -> None:
    terms = LoanTerms(
        D("30000.00"),
        D("0.215"),
        24,
        initiation_fee=D("1000.00"),
        monthly_service_fee=D("69.00"),
    )
    schedule = build_schedule(terms, START)
    assert schedule.principal == D("31000.00")
    assert schedule.level_instalment == level_instalment(D("31000.00"), D("0.215"), 24)
    first = schedule.instalments[0]
    assert first.total_due == first.instalment + D("69.00")
    assert schedule.total_service_fees == D("69.00") * 24
    assert sum(row.principal for row in schedule.instalments) == D("31000.00")
    # Cost of credit = everything repaid beyond the amount: interest + all fees.
    assert schedule.cost_of_credit == (
        schedule.total_interest + schedule.total_service_fees + D("1000.00")
    )


def test_service_fee_does_not_accrue_interest() -> None:
    without = build_schedule(LoanTerms(D("30000.00"), D("0.215"), 24), START)
    with_fee = build_schedule(
        LoanTerms(D("30000.00"), D("0.215"), 24, monthly_service_fee=D("69.00")), START
    )
    assert with_fee.total_interest == without.total_interest


def test_version_is_recorded() -> None:
    assert build_schedule(LoanTerms(D("1000.00"), D("0.1"), 6), START).version == VERSION


# --- Due dates -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2027, 1, 31), 1, date(2027, 2, 28)),
        (date(2027, 1, 31), 2, date(2027, 3, 31)),  # from the original day, not the clamped one
        (date(2028, 1, 31), 1, date(2028, 2, 29)),  # leap year
        (date(2026, 11, 18), 2, date(2027, 1, 18)),  # year rollover
        (date(2026, 8, 31), 1, date(2026, 9, 30)),
        (date(2026, 11, 18), 0, date(2026, 11, 18)),
    ],
)
def test_add_months(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


def test_add_months_rejects_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        add_months(START, -1)


def test_due_dates_follow_the_disbursement_day() -> None:
    schedule = build_schedule(LoanTerms(D("6000.00"), D("0.1"), 3), date(2027, 1, 31))
    assert [row.due_date for row in schedule.instalments] == [
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]


# --- Invalid input ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terms", "fragment"),
    [
        (LoanTerms(D("0.00"), D("0.1"), 12), "amount"),
        (LoanTerms(D("100.001"), D("0.1"), 12), "amount"),
        (LoanTerms(D("1000.00"), D("0.1"), 0), "term_months"),
        (LoanTerms(D("1000.00"), D("0.1"), 601), "term_months"),
        (LoanTerms(D("1000.00"), D("1"), 12), "annual_interest_rate"),
        (LoanTerms(D("1000.00"), D("-0.01"), 12), "annual_interest_rate"),
        (LoanTerms(D("1000.00"), D("0.1"), 12, initiation_fee=D("-1.00")), "initiation_fee"),
        (LoanTerms(D("1000.00"), D("0.1"), 12, monthly_service_fee=D("0.001")), "service_fee"),
    ],
)
def test_invalid_terms(terms: LoanTerms, fragment: str) -> None:
    with pytest.raises(ScheduleError, match=fragment):
        build_schedule(terms, START)


@pytest.mark.parametrize(
    ("amount", "rate", "term"),
    [
        ("1.00", "0", 60),  # A rounds up to 0.02 and would clear the balance early
        ("1.00", "0", 360),  # A rounds down to 0.00 and would never reduce it
        ("100.00", "0.6", 360),  # interest swallows the whole instalment
    ],
)
def test_terms_that_cannot_amortise_in_cents_are_rejected(
    amount: str, rate: str, term: int
) -> None:
    with pytest.raises(ScheduleError, match="whole-cent"):
        build_schedule(LoanTerms(D(amount), D(rate), term), START)


# --- Independent cross-check (numpy-financial) ----------------------------------------------


@pytest.mark.parametrize(
    ("amount", "rate", "term"),
    [
        ("100000.00", "0.12", 12),
        ("30000.00", "0.215", 24),
        ("250000.00", "0.115", 120),
        ("10000.00", "0.60", 6),
        ("75000.00", "0.0999", 60),
        ("5000.00", "0.24", 1),
    ],
)
def test_matches_numpy_financial(amount: str, rate: str, term: int) -> None:
    schedule = build_schedule(LoanTerms(D(amount), D(rate), term), START)
    monthly = float(rate) / 12
    pmt = float(npf.pmt(monthly, term, -float(amount)))
    assert abs(float(schedule.level_instalment) - pmt) <= 0.005 + 1e-9
    # Our interest uses cent-rounded balances; the float model does not. They drift by at
    # most a cent per period.
    for row in schedule.instalments:
        ipmt = float(npf.ipmt(monthly, row.number, term, -float(amount)))
        assert abs(float(row.interest) - ipmt) <= 0.01 * row.number
    expected_interest = pmt * term - float(amount)
    assert abs(float(schedule.total_interest) - expected_interest) <= 0.01 * term


# --- Properties ------------------------------------------------------------------------------

amounts = st.decimals(min_value=D("1000.00"), max_value=D("5000000.00"), places=2)
rates = st.decimals(min_value=D("0"), max_value=D("0.36"), places=6)
terms = st.integers(min_value=1, max_value=120)
fees = st.decimals(min_value=D("0.00"), max_value=D("500.00"), places=2)


def rounding_bound(annual_rate: Decimal, term: int) -> Decimal:
    """How far the final instalment can drift from the level one.

    Each period adds at most one cent of rounding error (half a cent on the instalment, half
    on the interest), and the error already in the balance accrues interest. So the drift is
    bounded by 0.01 × Σ(1 + r)^k for k = 0 … n, which is 0.01 × (n + 1) at a zero rate.
    """
    growth = 1 + annual_rate / 12
    return D("0.01") * sum((growth**k for k in range(term + 1)), D(0))


def test_rounding_bound_reduces_to_one_cent_per_period_at_zero_rate() -> None:
    assert rounding_bound(D(0), 12) == D("0.13")


@settings(max_examples=300, deadline=None)
@given(amount=amounts, rate=rates, term=terms, fee=fees, capitalised=fees)
def test_schedule_invariants(
    amount: Decimal, rate: Decimal, term: int, fee: Decimal, capitalised: Decimal
) -> None:
    terms_ = LoanTerms(amount, rate, term, initiation_fee=capitalised, monthly_service_fee=fee)
    schedule = build_schedule(terms_, START)
    rows = schedule.instalments
    level = schedule.level_instalment

    assert len(rows) == term
    assert rows[-1].closing_balance == D("0.00")
    assert sum(row.principal for row in rows) == terms_.principal
    assert all(row.interest >= 0 for row in rows)
    assert all(row.instalment == level for row in rows[:-1])
    assert abs(rows[-1].instalment - level) <= rounding_bound(rate, term)
    balances = [terms_.principal, *(row.closing_balance for row in rows)]
    assert all(later < earlier for earlier, later in itertools.pairwise(balances))
    for row in rows:
        assert row.instalment == row.interest + row.principal
        assert row.total_due == row.instalment + fee
        assert row.closing_balance == row.opening_balance - row.principal
        assert row.interest == row.interest.quantize(D("0.01"))
    assert schedule.total_repayable == sum(row.total_due for row in rows)


@settings(max_examples=200, deadline=None)
@given(
    amount=st.decimals(min_value=D("0.01"), max_value=D("1000000.00"), places=2),
    rate=st.decimals(min_value=D("0"), max_value=D("0.999999"), places=6),
    term=st.integers(min_value=1, max_value=600),
)
def test_any_input_gives_a_valid_schedule_or_a_clear_error(
    amount: Decimal, rate: Decimal, term: int
) -> None:
    try:
        schedule = build_schedule(LoanTerms(amount, rate, term), START)
    except ScheduleError:
        return
    assert schedule.instalments[-1].closing_balance == D("0.00")
    assert all(row.principal > 0 for row in schedule.instalments)
