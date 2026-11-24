"""Early settlement quotes (FINANCIAL_CALCULATIONS.md §7).

For a settlement date ``d``, with ``t`` the latest due date on or before ``d`` (or the
disbursement date when none has passed):

    accrued_interest = q(principal not yet due × R × (d − t).days / 365)
    settlement_fee   = q(outstanding principal × early_settlement_fee_rate)
    settlement       = principal not yet due + accrued_interest
                     + everything due on or before d and unpaid (interest, principal, fees)
                     + unpaid charges (including late fees that will have been charged by d)
                     + settlement_fee

Principal that is already due is counted once, in the arrears, and accrues nothing further
(there is no penalty interest in v1). Day count is Actual/365 Fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from app.core.money import FINANCIAL_CONTEXT, quantize_money
from app.modules.loans.engine import arrears
from app.modules.loans.engine.allocation import Allocation, allocate
from app.modules.loans.engine.ledger import (
    ZERO,
    ChargeState,
    Component,
    InstalmentState,
    outstanding_charges,
    outstanding_principal,
)

DAYS_PER_YEAR = Decimal(365)


class SettlementError(ValueError):
    """A quote cannot be produced for the requested date."""


@dataclass(frozen=True, slots=True)
class SettlementQuote:
    as_of: date
    accrual_start: date  # t
    accrued_days: int
    outstanding_principal: Decimal
    principal_not_yet_due: Decimal
    accrued_interest: Decimal
    arrears_interest: Decimal
    arrears_principal: Decimal
    arrears_service_fees: Decimal
    outstanding_charges: Decimal  # charged already
    projected_late_fees: Decimal  # will have been charged by as_of
    settlement_fee: Decimal

    @property
    def arrears(self) -> Decimal:
        return self.arrears_interest + self.arrears_principal + self.arrears_service_fees

    @property
    def total(self) -> Decimal:
        return (
            self.principal_not_yet_due
            + self.accrued_interest
            + self.arrears
            + self.outstanding_charges
            + self.projected_late_fees
            + self.settlement_fee
        )


def quote(
    instalments: tuple[InstalmentState, ...],
    charges: tuple[ChargeState, ...],
    as_of: date,
    *,
    disbursed_on: date,
    annual_interest_rate: Decimal,
    early_settlement_fee_rate: Decimal,
    late_fee: Decimal,
    grace_period_days: int,
) -> SettlementQuote:
    if as_of < disbursed_on:
        raise SettlementError("a settlement date cannot be before the disbursement date")
    live = tuple(i for i in instalments if not i.cancelled)
    due = [i for i in live if i.due_date <= as_of]
    future = [i for i in live if i.due_date > as_of]
    accrual_start = max((i.due_date for i in due), default=disbursed_on)
    days = (as_of - accrual_start).days
    not_yet_due = sum((i.principal_outstanding for i in future), ZERO)
    principal = outstanding_principal(live)
    projected = arrears.late_fees_due(
        live, charges, as_of, late_fee=late_fee, grace_period_days=grace_period_days
    )
    with localcontext(FINANCIAL_CONTEXT):
        accrued = quantize_money(not_yet_due * annual_interest_rate * days / DAYS_PER_YEAR)
        fee = quantize_money(principal * early_settlement_fee_rate)
    return SettlementQuote(
        as_of=as_of,
        accrual_start=accrual_start,
        accrued_days=days,
        outstanding_principal=principal,
        principal_not_yet_due=not_yet_due,
        accrued_interest=accrued,
        arrears_interest=sum((i.interest_outstanding for i in due), ZERO),
        arrears_principal=sum((i.principal_outstanding for i in due), ZERO),
        arrears_service_fees=sum((i.service_fee_outstanding for i in due), ZERO),
        outstanding_charges=outstanding_charges(charges),
        projected_late_fees=sum((f.amount for f in projected), ZERO),
        settlement_fee=fee,
    )


def allocations(
    settlement: SettlementQuote,
    instalments: tuple[InstalmentState, ...],
    charges: tuple[ChargeState, ...],
) -> tuple[Allocation, ...]:
    """How a payment of exactly ``settlement.total`` is applied on ``settlement.as_of``.

    ``charges`` must already include every charge the quote counts (late fees charged up to
    the settlement date and the settlement fee itself), so nothing is projected any more.
    """
    if settlement.projected_late_fees:
        raise SettlementError("charge the projected late fees before allocating a settlement")
    live = tuple(i for i in instalments if not i.cancelled)
    due_part = settlement.arrears + outstanding_charges(charges)
    result: list[Allocation] = []
    if due_part > 0:
        waterfall = allocate(due_part, settlement.as_of, live, charges)
        if waterfall.prepayment:
            raise SettlementError("the settlement does not match the loan's current position")
        result.extend(waterfall.allocations)
    future = sorted((i for i in live if i.due_date > settlement.as_of), key=lambda i: i.number)
    if settlement.accrued_interest > 0:
        # Accrued interest belongs to the period in progress: the first instalment not yet due.
        result.append(
            Allocation(
                Component.INTEREST, settlement.accrued_interest, instalment_number=future[0].number
            )
        )
    for inst in future:
        if inst.principal_outstanding > 0:
            result.append(
                Allocation(
                    Component.PRINCIPAL, inst.principal_outstanding, instalment_number=inst.number
                )
            )
    if sum((a.amount for a in result), ZERO) != settlement.total:
        raise SettlementError("the settlement does not match the loan's current position")
    return tuple(result)
