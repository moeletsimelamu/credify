"""Loan servicing vocabulary and the read-only state the servicing engines work on.

Pure: the service layer copies database rows into these frozen dataclasses, asks the
engines what should happen, and persists the answer. Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

SERVICING_VERSION = "servicing-1.0.0"
ZERO = Decimal("0.00")


class LoanStatus(StrEnum):
    PENDING_DISBURSEMENT = "PENDING_DISBURSEMENT"
    ACTIVE = "ACTIVE"
    IN_ARREARS = "IN_ARREARS"
    SETTLED = "SETTLED"
    CLOSED = "CLOSED"


REPAYABLE = frozenset({LoanStatus.ACTIVE, LoanStatus.IN_ARREARS})


class ScheduleReason(StrEnum):
    ORIGINATION = "ORIGINATION"
    PREPAYMENT_RECAST = "PREPAYMENT_RECAST"


class InstalmentStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    DUE = "DUE"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"  # remaining instalments of a loan settled early


class ChargeType(StrEnum):
    LATE_FEE = "LATE_FEE"
    EARLY_SETTLEMENT_FEE = "EARLY_SETTLEMENT_FEE"


class TransactionType(StrEnum):
    DISBURSEMENT = "DISBURSEMENT"
    REPAYMENT = "REPAYMENT"
    CHARGE = "CHARGE"
    SETTLEMENT = "SETTLEMENT"
    REVERSAL = "REVERSAL"


class Component(StrEnum):
    """Where part of a payment went (FINANCIAL_CALCULATIONS.md §5)."""

    CHARGE = "CHARGE"
    SERVICE_FEE = "SERVICE_FEE"
    INTEREST = "INTEREST"
    PRINCIPAL = "PRINCIPAL"
    PREPAYMENT = "PREPAYMENT"


class ArrearsBucket(StrEnum):
    CURRENT = "CURRENT"
    DPD_1_30 = "1-30"
    DPD_31_60 = "31-60"
    DPD_61_90 = "61-90"
    DPD_90_PLUS = "90+"


@dataclass(frozen=True, slots=True)
class InstalmentState:
    """One instalment of the effective schedule, with what has been paid against it."""

    number: int
    due_date: date
    interest_due: Decimal
    principal_due: Decimal
    service_fee_due: Decimal
    interest_paid: Decimal = ZERO
    principal_paid: Decimal = ZERO
    service_fee_paid: Decimal = ZERO
    cancelled: bool = False

    @property
    def total_due(self) -> Decimal:
        return self.interest_due + self.principal_due + self.service_fee_due

    @property
    def interest_outstanding(self) -> Decimal:
        return ZERO if self.cancelled else self.interest_due - self.interest_paid

    @property
    def principal_outstanding(self) -> Decimal:
        return self.principal_due - self.principal_paid

    @property
    def service_fee_outstanding(self) -> Decimal:
        return ZERO if self.cancelled else self.service_fee_due - self.service_fee_paid

    @property
    def outstanding(self) -> Decimal:
        return self.interest_outstanding + self.principal_outstanding + self.service_fee_outstanding

    @property
    def paid(self) -> Decimal:
        return self.interest_paid + self.principal_paid + self.service_fee_paid

    @property
    def is_settled(self) -> bool:
        """Nothing more is owed on it (paid in full, or cancelled with its principal repaid)."""
        return self.outstanding == 0


@dataclass(frozen=True, slots=True)
class ChargeState:
    key: str  # the charge row's id
    charge_type: ChargeType
    amount: Decimal
    amount_paid: Decimal
    charged_on: date
    instalment_number: int | None = None

    @property
    def outstanding(self) -> Decimal:
        return self.amount - self.amount_paid


def outstanding_principal(instalments: tuple[InstalmentState, ...]) -> Decimal:
    return sum((i.principal_outstanding for i in instalments), ZERO)


def outstanding_charges(charges: tuple[ChargeState, ...]) -> Decimal:
    return sum((c.outstanding for c in charges), ZERO)
