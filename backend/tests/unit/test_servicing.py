"""Pure servicing engines: allocation waterfall, arrears, late fees, recast and settlement
(FINANCIAL_CALCULATIONS.md §5–§7)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.loans.engine import allocation, amortisation, arrears, settlement
from app.modules.loans.engine.ledger import (
    ArrearsBucket,
    ChargeState,
    ChargeType,
    Component,
    InstalmentState,
    InstalmentStatus,
    outstanding_principal,
)

D = Decimal
DISBURSED = date(2026, 11, 18)
EXAMPLE_B = amortisation.LoanTerms(
    amount=D("30000.00"),
    annual_interest_rate=D("0.215"),
    term_months=24,
    monthly_service_fee=D("69.00"),
)


def _states(schedule: amortisation.Schedule, paid_through: int = 0) -> tuple[InstalmentState, ...]:
    """Instalment states with the first ``paid_through`` instalments paid in full."""
    return tuple(
        InstalmentState(
            number=row.number,
            due_date=row.due_date,
            interest_due=row.interest,
            principal_due=row.principal,
            service_fee_due=row.service_fee,
            interest_paid=row.interest if row.number <= paid_through else D("0.00"),
            principal_paid=row.principal if row.number <= paid_through else D("0.00"),
            service_fee_paid=row.service_fee if row.number <= paid_through else D("0.00"),
        )
        for row in schedule.instalments
    )


@pytest.fixture(scope="module")
def schedule() -> amortisation.Schedule:
    return amortisation.build_schedule(EXAMPLE_B, DISBURSED)


def _fee(key: str, amount: str, charged_on: date, number: int | None = None) -> ChargeState:
    return ChargeState(key, ChargeType.LATE_FEE, D(amount), D("0.00"), charged_on, number)


# --- Allocation waterfall ------------------------------------------------------------------


def test_payment_on_the_due_date(schedule: amortisation.Schedule) -> None:
    result = allocation.allocate(D("1617.95"), date(2026, 12, 18), _states(schedule))
    assert [(a.component, a.amount, a.instalment_number) for a in result.allocations] == [
        (Component.SERVICE_FEE, D("69.00"), 1),
        (Component.INTEREST, D("537.50"), 1),
        (Component.PRINCIPAL, D("1011.45"), 1),
    ]
    assert result.prepayment == 0


def test_overdue_components_are_paid_across_instalments_first(
    schedule: amortisation.Schedule,
) -> None:
    states = _states(schedule)
    charges = (_fee("b", "100.00", date(2027, 1, 24)), _fee("a", "100.00", date(2026, 12, 24)))
    # Instalments 1 and 2 overdue on 2027-02-18, instalment 3 due that day.
    result = allocation.allocate(D("400.00"), date(2027, 2, 18), states, charges)
    assert [
        (a.component, a.amount, a.charge_key or a.instalment_number) for a in result.allocations
    ] == [
        (Component.CHARGE, D("100.00"), "a"),  # oldest charge first
        (Component.CHARGE, D("100.00"), "b"),
        (Component.SERVICE_FEE, D("69.00"), 1),  # every overdue fee before any interest
        (Component.SERVICE_FEE, D("69.00"), 2),
        (Component.INTEREST, D("62.00"), 1),
    ]


def test_future_instalments_are_never_paid_early(schedule: amortisation.Schedule) -> None:
    result = allocation.allocate(D("2000.00"), date(2026, 12, 10), _states(schedule))
    assert result.allocations == ()
    assert result.prepayment == D("2000.00")  # paying ahead is a prepayment (§5)


def test_due_today_follows_overdue(schedule: amortisation.Schedule) -> None:
    result = allocation.allocate(D("1700.00"), date(2027, 1, 18), _states(schedule))
    instalment_1 = sum(a.amount for a in result.allocations if a.instalment_number == 1)
    instalment_2 = sum(a.amount for a in result.allocations if a.instalment_number == 2)
    assert (instalment_1, instalment_2) == (D("1617.95"), D("82.05"))
    # Instalment 2 is current: its fee and then its interest.
    assert [a.component for a in result.allocations if a.instalment_number == 2] == [
        Component.SERVICE_FEE,
        Component.INTEREST,
    ]


@pytest.mark.parametrize("amount", [D("0.00"), D("-1.00"), D("1.005")])
def test_invalid_payments(amount: Decimal, schedule: amortisation.Schedule) -> None:
    with pytest.raises(allocation.AllocationError):
        allocation.allocate(amount, date(2026, 12, 18), _states(schedule))


def test_amount_due(schedule: amortisation.Schedule) -> None:
    states = _states(schedule)
    assert allocation.amount_due(date(2026, 12, 17), states) == 0
    assert allocation.amount_due(date(2027, 1, 18), states) == D("3235.90")
    charges = (_fee("a", "100.00", date(2026, 12, 24), 1),)
    assert allocation.amount_due(date(2027, 1, 18), states, charges) == D("3335.90")


@settings(max_examples=200, deadline=None)
@given(
    amount=st.decimals(min_value=D("0.01"), max_value=D("60000"), places=2),
    day=st.integers(min_value=0, max_value=800),
    paid_through=st.integers(min_value=0, max_value=6),
)
def test_allocation_properties(amount: Decimal, day: int, paid_through: int) -> None:
    schedule = amortisation.build_schedule(EXAMPLE_B, DISBURSED)
    states = _states(schedule, paid_through)
    value_date = DISBURSED + timedelta(days=day)
    result = allocation.allocate(amount, value_date, states)
    assert result.applied + result.prepayment == amount
    assert result.prepayment >= 0
    by_number = {s.number: s for s in states}
    for item in result.allocations:
        assert item.amount > 0
        inst = by_number[item.instalment_number or 0]
        assert inst.due_date <= value_date  # nothing is applied to a future instalment
    # A prepayment only happens once everything due has been paid.
    if result.prepayment:
        assert result.applied == allocation.amount_due(value_date, states)


# --- Arrears and late fees -----------------------------------------------------------------


def test_instalment_status(schedule: amortisation.Schedule) -> None:
    first = _states(schedule)[0]  # due 2026-12-18
    assert arrears.instalment_status(first, date(2026, 12, 17)) is InstalmentStatus.SCHEDULED
    assert arrears.instalment_status(first, date(2026, 12, 18)) is InstalmentStatus.DUE
    assert arrears.instalment_status(first, date(2026, 12, 19)) is InstalmentStatus.OVERDUE
    partial = replace(first, interest_paid=D("1.00"))
    assert arrears.instalment_status(partial, date(2026, 12, 1)) is InstalmentStatus.PARTIALLY_PAID
    assert arrears.instalment_status(partial, date(2027, 1, 1)) is InstalmentStatus.OVERDUE
    paid = _states(schedule, paid_through=1)[0]
    assert arrears.instalment_status(paid, date(2030, 1, 1)) is InstalmentStatus.PAID
    cancelled = replace(first, cancelled=True)
    assert arrears.instalment_status(cancelled, date(2030, 1, 1)) is InstalmentStatus.CANCELLED


@pytest.mark.parametrize(
    ("dpd", "expected"),
    [
        (0, ArrearsBucket.CURRENT),
        (1, ArrearsBucket.DPD_1_30),
        (30, ArrearsBucket.DPD_1_30),
        (31, ArrearsBucket.DPD_31_60),
        (60, ArrearsBucket.DPD_31_60),
        (61, ArrearsBucket.DPD_61_90),
        (90, ArrearsBucket.DPD_61_90),
        (91, ArrearsBucket.DPD_90_PLUS),
    ],
)
def test_buckets(dpd: int, expected: ArrearsBucket) -> None:
    assert arrears.bucket(dpd) is expected


def test_position(schedule: amortisation.Schedule) -> None:
    states = _states(schedule, paid_through=1)
    charges = (_fee("a", "100.00", date(2027, 1, 24), 2),)
    position = arrears.position(states, charges, date(2027, 2, 20))
    assert position.days_past_due == 33  # instalment 2, due 2027-01-18
    assert position.bucket is ArrearsBucket.DPD_31_60
    assert position.overdue_instalments == 2
    assert position.arrears_amount == D("1617.95") * 2 + D("100.00")
    assert position.outstanding_principal == D("28988.55")
    interest_and_fees = sum(s.interest_due + s.service_fee_due for s in states[1:3])
    assert position.amount_owed == D("28988.55") + interest_and_fees + D("100.00")
    clear = arrears.position(states, (), date(2026, 12, 18))
    assert (clear.days_past_due, clear.arrears_amount) == (0, D("0.00"))


def test_late_fees_after_grace_once_per_instalment(schedule: amortisation.Schedule) -> None:
    states = _states(schedule)
    fee, grace = D("100.00"), 5
    assert (
        arrears.late_fees_due(states, (), date(2026, 12, 23), late_fee=fee, grace_period_days=grace)
        == []
    )
    (charged,) = arrears.late_fees_due(
        states, (), date(2026, 12, 24), late_fee=fee, grace_period_days=grace
    )
    assert (charged.instalment_number, charged.charged_on) == (1, date(2026, 12, 24))
    assert charged.amount == fee
    # Dated when it became chargeable, whenever the job runs; never charged twice.
    later = arrears.late_fees_due(
        states, (), date(2027, 1, 30), late_fee=fee, grace_period_days=grace
    )
    assert [(f.instalment_number, f.charged_on) for f in later] == [
        (1, date(2026, 12, 24)),
        (2, date(2027, 1, 24)),
    ]
    existing = (_fee("x", "100.00", date(2026, 12, 24), 1),)
    again = arrears.late_fees_due(
        states, existing, date(2027, 1, 30), late_fee=fee, grace_period_days=grace
    )
    assert [f.instalment_number for f in again] == [2]
    assert (
        arrears.late_fees_due(states, (), date(2027, 1, 30), late_fee=D("0"), grace_period_days=5)
        == []
    )
    paid = _states(schedule, paid_through=2)
    assert (
        arrears.late_fees_due(paid, (), date(2027, 1, 30), late_fee=fee, grace_period_days=grace)
        == []
    )


# --- Prepayment recast ---------------------------------------------------------------------


def test_recast_keeps_the_instalment_and_shortens_the_term(
    schedule: amortisation.Schedule,
) -> None:
    balance = schedule.instalments[0].closing_balance - D("5000.00")
    rows = amortisation.recast(
        balance,
        annual_interest_rate=EXAMPLE_B.annual_interest_rate,
        level=schedule.level_instalment,
        first_number=2,
        disbursement_date=DISBURSED,
        monthly_service_fee=D("69.00"),
    )
    assert rows[0].number == 2
    assert rows[0].opening_balance == D("23988.55")
    assert rows[0].due_date == date(2027, 1, 18)
    assert all(row.instalment == schedule.level_instalment for row in rows[:-1])
    assert rows[-1].instalment <= schedule.level_instalment
    assert rows[-1].closing_balance == 0
    assert sum(row.principal for row in rows) == balance
    assert len(rows) < 23
    assert all(row.service_fee == D("69.00") for row in rows)


@pytest.mark.parametrize(
    ("balance", "level", "first"),
    [(D("0.00"), D("100.00"), 1), (D("1.005"), D("100.00"), 1), (D("10.00"), D("1.00"), 0)],
)
def test_recast_rejects_bad_inputs(balance: Decimal, level: Decimal, first: int) -> None:
    with pytest.raises(amortisation.ScheduleError):
        amortisation.recast(
            balance,
            annual_interest_rate=D("0.1"),
            level=level,
            first_number=first,
            disbursement_date=DISBURSED,
        )


def test_recast_rejects_an_instalment_below_the_interest() -> None:
    with pytest.raises(amortisation.ScheduleError, match="no longer covers the interest"):
        amortisation.recast(
            D("100000.00"),
            annual_interest_rate=D("0.24"),
            level=D("1000.00"),  # interest alone is 2,000.00 a month
            first_number=1,
            disbursement_date=DISBURSED,
        )


@settings(max_examples=150, deadline=None)
@given(
    prepay_cents=st.integers(min_value=1, max_value=2_000_000),
    after=st.integers(min_value=1, max_value=20),
)
def test_recast_properties(prepay_cents: int, after: int) -> None:
    schedule = amortisation.build_schedule(EXAMPLE_B, DISBURSED)
    remaining = schedule.instalments[after - 1].closing_balance
    prepayment = min(D(prepay_cents) / 100, remaining - D("0.01"))
    balance = remaining - prepayment
    rows = amortisation.recast(
        balance,
        annual_interest_rate=EXAMPLE_B.annual_interest_rate,
        level=schedule.level_instalment,
        first_number=after + 1,
        disbursement_date=DISBURSED,
    )
    assert rows[-1].closing_balance == 0
    assert sum(row.principal for row in rows) == balance
    assert len(rows) <= EXAMPLE_B.term_months - after
    assert [row.number for row in rows] == list(range(after + 1, after + 1 + len(rows)))
    for previous, row in itertools.pairwise(rows):
        assert row.opening_balance == previous.closing_balance
        assert row.opening_balance < previous.opening_balance


# --- Early settlement ----------------------------------------------------------------------


def _quote(
    states: tuple[InstalmentState, ...], as_of: date, **overrides: object
) -> settlement.SettlementQuote:
    kwargs: dict[str, object] = {
        "disbursed_on": DISBURSED,
        "annual_interest_rate": EXAMPLE_B.annual_interest_rate,
        "early_settlement_fee_rate": D("0"),
        "late_fee": D("100.00"),
        "grace_period_days": 5,
    }
    kwargs.update(overrides)
    return settlement.quote(states, (), as_of, **kwargs)  # type: ignore[arg-type]


def test_worked_example(schedule: amortisation.Schedule) -> None:
    # FINANCIAL_CALCULATIONS.md §7: after instalment 3 the balance is 26,910.96; settling
    # 17 days later accrues 269.48 of interest.
    states = _states(schedule, paid_through=3)
    quote = _quote(states, date(2027, 3, 7))
    assert quote.outstanding_principal == D("26910.96")
    assert (quote.accrual_start, quote.accrued_days) == (date(2027, 2, 18), 17)
    assert quote.accrued_interest == D("269.48")
    assert quote.arrears == 0
    assert quote.total == D("27180.44")
    allocations = settlement.allocations(quote, states, ())
    assert sum(a.amount for a in allocations) == quote.total
    assert allocations[0] == allocation.Allocation(
        Component.INTEREST, D("269.48"), instalment_number=4
    )


def test_settlement_fee_and_arrears(schedule: amortisation.Schedule) -> None:
    states = _states(schedule, paid_through=1)
    # Instalment 2 (due 2027-01-18) is unpaid; settle on 2027-01-28.
    quote = _quote(states, date(2027, 1, 28), early_settlement_fee_rate=D("0.01"))
    assert quote.accrual_start == date(2027, 1, 18)
    assert quote.arrears == states[1].total_due
    assert quote.projected_late_fees == D("100.00")  # instalment 2 is past its grace period
    # Principal already due is in the arrears and does not accrue again.
    assert quote.principal_not_yet_due == quote.outstanding_principal - states[1].principal_due
    assert quote.settlement_fee == D("289.89")  # 1 % of 28,988.55
    with pytest.raises(settlement.SettlementError, match="projected late fees"):
        settlement.allocations(quote, states, ())


def test_settlement_on_the_disbursement_day_and_before_it(
    schedule: amortisation.Schedule,
) -> None:
    states = _states(schedule)
    assert _quote(states, DISBURSED).total == D("30000.00")
    with pytest.raises(settlement.SettlementError):
        _quote(states, DISBURSED - timedelta(days=1))


def test_settlement_mismatch_is_detected(schedule: amortisation.Schedule) -> None:
    states = _states(schedule, paid_through=3)
    quote = _quote(states, date(2027, 3, 7))
    paid_more = _states(schedule, paid_through=4)
    with pytest.raises(settlement.SettlementError, match="does not match"):
        settlement.allocations(quote, paid_more, ())


@settings(max_examples=150, deadline=None)
@given(extra_days=st.integers(min_value=0, max_value=120), paid_through=st.integers(0, 23))
def test_settlement_properties(extra_days: int, paid_through: int) -> None:
    schedule = amortisation.build_schedule(EXAMPLE_B, DISBURSED)
    states = _states(schedule, paid_through)
    # Settle some time after the last paid instalment (later ones may have fallen due).
    last_paid = schedule.instalments[paid_through - 1].due_date if paid_through else DISBURSED
    as_of = last_paid + timedelta(days=extra_days)
    quote = _quote(states, as_of, late_fee=D("0"))
    assert quote.total >= outstanding_principal(states)
    allocations = settlement.allocations(quote, states, ())
    assert sum((a.amount for a in allocations), D("0")) == quote.total
    # Accrued interest never exceeds the interest scheduled for the period in progress.
    future = [s for s in states if s.due_date > as_of]
    if future:
        assert quote.accrued_interest <= future[0].interest_due
    else:
        assert quote.accrued_interest == 0
