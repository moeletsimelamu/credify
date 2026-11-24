"""Payment allocation waterfall (FINANCIAL_CALCULATIONS.md §5).

A payment on value date ``v`` is applied, in this order:

1. outstanding charges (late fees), oldest first;
2. service fees of overdue instalments (due before ``v``), oldest first;
3. interest of overdue instalments, oldest first;
4. principal of overdue instalments, oldest first;
5. the instalment(s) due on ``v``: service fee, interest, principal;
6. anything left is a prepayment of principal.

Pure and exact: every allocation is in whole cents and the allocations sum to the payment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.money import CENT
from app.modules.loans.engine.ledger import ZERO, ChargeState, Component, InstalmentState

INSTALMENT_COMPONENTS = (Component.SERVICE_FEE, Component.INTEREST, Component.PRINCIPAL)


def _outstanding(instalment: InstalmentState, component: Component) -> Decimal:
    if component is Component.SERVICE_FEE:
        return instalment.service_fee_outstanding
    if component is Component.INTEREST:
        return instalment.interest_outstanding
    return instalment.principal_outstanding


class AllocationError(ValueError):
    """The payment cannot be allocated (for example, it is not in whole cents)."""


@dataclass(frozen=True, slots=True)
class Allocation:
    component: Component
    amount: Decimal
    instalment_number: int | None = None
    charge_key: str | None = None


@dataclass(frozen=True, slots=True)
class AllocationResult:
    amount: Decimal
    allocations: tuple[Allocation, ...]  # excludes the prepayment
    prepayment: Decimal

    @property
    def applied(self) -> Decimal:
        return sum((a.amount for a in self.allocations), ZERO)


def allocate(
    amount: Decimal,
    value_date: date,
    instalments: Iterable[InstalmentState],
    charges: Iterable[ChargeState] = (),
) -> AllocationResult:
    if amount <= 0 or amount != amount.quantize(CENT):
        raise AllocationError("a payment must be a positive amount in whole cents")

    remaining = amount
    allocations: list[Allocation] = []

    def add(
        outstanding: Decimal,
        component: Component,
        *,
        instalment_number: int | None = None,
        charge_key: str | None = None,
    ) -> None:
        nonlocal remaining
        portion = min(remaining, outstanding)
        if portion > 0:
            allocations.append(Allocation(component, portion, instalment_number, charge_key))
            remaining -= portion

    for charge in sorted(charges, key=lambda c: (c.charged_on, c.key)):
        add(charge.outstanding, Component.CHARGE, charge_key=charge.key)

    live = sorted((i for i in instalments if not i.cancelled), key=lambda i: i.number)
    overdue = [i for i in live if i.due_date < value_date]
    current = [i for i in live if i.due_date == value_date]
    # Overdue: each component across every overdue instalment before the next component.
    for component in INSTALMENT_COMPONENTS:
        for inst in overdue:
            add(_outstanding(inst, component), component, instalment_number=inst.number)
    # Due on the value date: instalment by instalment.
    for inst in current:
        for component in INSTALMENT_COMPONENTS:
            add(_outstanding(inst, component), component, instalment_number=inst.number)
    return AllocationResult(amount=amount, allocations=tuple(allocations), prepayment=remaining)


def amount_due(
    value_date: date,
    instalments: Iterable[InstalmentState],
    charges: Iterable[ChargeState] = (),
) -> Decimal:
    """Everything the waterfall would apply before a prepayment: due instalments + charges."""
    due = sum(
        (i.outstanding for i in instalments if not i.cancelled and i.due_date <= value_date),
        ZERO,
    )
    return due + sum((c.outstanding for c in charges), ZERO)
