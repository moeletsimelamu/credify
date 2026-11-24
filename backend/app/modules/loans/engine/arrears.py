"""Arrears, late fees and instalment status (FINANCIAL_CALCULATIONS.md §6).

Everything is evaluated *as of* an explicit date, so tests and demos can simulate the
passage of time without touching a real clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.modules.loans.engine.ledger import (
    ZERO,
    ArrearsBucket,
    ChargeState,
    ChargeType,
    InstalmentState,
    InstalmentStatus,
    outstanding_charges,
    outstanding_principal,
)


def instalment_status(instalment: InstalmentState, as_of: date) -> InstalmentStatus:
    if instalment.cancelled:
        return InstalmentStatus.CANCELLED
    if instalment.is_settled:
        return InstalmentStatus.PAID
    if as_of > instalment.due_date:
        return InstalmentStatus.OVERDUE
    if instalment.paid > 0:
        return InstalmentStatus.PARTIALLY_PAID
    if as_of == instalment.due_date:
        return InstalmentStatus.DUE
    return InstalmentStatus.SCHEDULED


def _overdue(instalments: Iterable[InstalmentState], as_of: date) -> list[InstalmentState]:
    return sorted(
        (i for i in instalments if instalment_status(i, as_of) is InstalmentStatus.OVERDUE),
        key=lambda i: i.number,
    )


def days_past_due(instalments: Iterable[InstalmentState], as_of: date) -> int:
    """Days since the oldest unpaid instalment fell due (0 when nothing is overdue)."""
    overdue = _overdue(instalments, as_of)
    return (as_of - overdue[0].due_date).days if overdue else 0


def bucket(dpd: int) -> ArrearsBucket:
    if dpd <= 0:
        return ArrearsBucket.CURRENT
    if dpd <= 30:
        return ArrearsBucket.DPD_1_30
    if dpd <= 60:
        return ArrearsBucket.DPD_31_60
    if dpd <= 90:
        return ArrearsBucket.DPD_61_90
    return ArrearsBucket.DPD_90_PLUS


def late_fee_date(due_date: date, grace_period_days: int) -> date:
    """The first day a still-unpaid instalment attracts a late fee: after the grace period."""
    return due_date + timedelta(days=grace_period_days + 1)


@dataclass(frozen=True, slots=True)
class LateFee:
    instalment_number: int
    charged_on: date
    amount: Decimal


def late_fees_due(
    instalments: Iterable[InstalmentState],
    charges: Iterable[ChargeState],
    as_of: date,
    *,
    late_fee: Decimal,
    grace_period_days: int,
) -> list[LateFee]:
    """Late fees that should exist by ``as_of`` but have not been charged yet.

    One fee per instalment, ever: an instalment that already carries a late fee is skipped.
    The fee is dated the day it became chargeable, whenever the job happens to run.
    """
    if late_fee <= 0:
        return []
    charged = {
        c.instalment_number
        for c in charges
        if c.charge_type is ChargeType.LATE_FEE and c.instalment_number is not None
    }
    fees = []
    for inst in sorted(instalments, key=lambda i: i.number):
        if inst.cancelled or inst.is_settled or inst.number in charged:
            continue
        chargeable_on = late_fee_date(inst.due_date, grace_period_days)
        if as_of >= chargeable_on:
            fees.append(LateFee(inst.number, chargeable_on, late_fee))
    return fees


@dataclass(frozen=True, slots=True)
class ArrearsPosition:
    as_of: date
    days_past_due: int
    bucket: ArrearsBucket
    overdue_instalments: int
    arrears_amount: Decimal  # unpaid overdue instalments + unpaid charges
    outstanding_principal: Decimal
    amount_owed: Decimal  # principal + interest/fees due on or before as_of + charges


def position(
    instalments: tuple[InstalmentState, ...], charges: tuple[ChargeState, ...], as_of: date
) -> ArrearsPosition:
    overdue = _overdue(instalments, as_of)
    dpd = days_past_due(instalments, as_of)
    unpaid_charges = outstanding_charges(charges)
    due_non_principal = sum(
        (
            i.interest_outstanding + i.service_fee_outstanding
            for i in instalments
            if i.due_date <= as_of
        ),
        ZERO,
    )
    principal = outstanding_principal(instalments)
    return ArrearsPosition(
        as_of=as_of,
        days_past_due=dpd,
        bucket=bucket(dpd),
        overdue_instalments=len(overdue),
        arrears_amount=sum((i.outstanding for i in overdue), ZERO) + unpaid_charges,
        outstanding_principal=principal,
        amount_owed=principal + due_non_principal + unpaid_charges,
    )
