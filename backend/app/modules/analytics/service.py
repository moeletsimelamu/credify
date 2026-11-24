"""Portfolio figures (FINANCIAL_CALCULATIONS.md §8). Read-only queries; nothing is written.

Totals are grouped by currency and never summed across currencies. Period filters are
calendar dates in the business timezone (SAST).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.clock import BUSINESS_TZ
from app.core.money import Currency, quantize_ratio
from app.modules.applications.domain.state_machine import ApplicationStatus
from app.modules.applications.models import LoanApplication
from app.modules.decisions.domain.reason_codes import DecisionOutcome
from app.modules.decisions.models import LendingDecision
from app.modules.loans.engine import arrears
from app.modules.loans.engine.ledger import (
    REPAYABLE,
    ZERO,
    ArrearsBucket,
    InstalmentStatus,
    LoanStatus,
    TransactionType,
)
from app.modules.loans.models import (
    LoanAccount,
    LoanTransaction,
    RepaymentSchedule,
    ScheduleInstalment,
)


@dataclass(slots=True)
class BucketFigures:
    accounts: int = 0
    outstanding_principal: Decimal = ZERO


@dataclass(slots=True)
class CurrencyPortfolio:
    currency: Currency
    applications_by_status: dict[str, int] = field(default_factory=dict)
    approved: int = 0
    declined: int = 0
    loans_by_status: dict[str, int] = field(default_factory=dict)
    loans_disbursed: int = 0
    principal_disbursed: Decimal = ZERO
    outstanding_principal: Decimal = ZERO
    outstanding_principal_in_arrears: Decimal = ZERO
    repayments_received: Decimal = ZERO
    amount_due: Decimal = ZERO
    amount_collected: Decimal = ZERO
    arrears_buckets: dict[ArrearsBucket, BucketFigures] = field(
        default_factory=lambda: {bucket: BucketFigures() for bucket in ArrearsBucket}
    )

    @property
    def approval_rate(self) -> Decimal | None:
        decided = self.approved + self.declined
        return quantize_ratio(Decimal(self.approved) / decided) if decided else None

    @property
    def arrears_rate(self) -> Decimal | None:
        if not self.outstanding_principal:
            return None
        return quantize_ratio(self.outstanding_principal_in_arrears / self.outstanding_principal)

    @property
    def collection_rate(self) -> Decimal | None:
        return quantize_ratio(self.amount_collected / self.amount_due) if self.amount_due else None


@dataclass(frozen=True, slots=True)
class Portfolio:
    date_from: date
    date_to: date
    collections_through: date
    currencies: list[CurrencyPortfolio]


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=BUSINESS_TZ)


def portfolio(session: Session, *, date_from: date, date_to: date, today: date) -> Portfolio:
    by_currency: dict[Currency, CurrencyPortfolio] = {}

    def figures(currency: Currency) -> CurrencyPortfolio:
        if currency not in by_currency:
            by_currency[currency] = CurrencyPortfolio(currency=currency)
        return by_currency[currency]

    start, end = _start_of(date_from), _start_of(date_to + timedelta(days=1))

    # Applications by current status (all time).
    for currency, app_status, count in session.execute(
        select(LoanApplication.currency, LoanApplication.status, func.count()).group_by(
            LoanApplication.currency, LoanApplication.status
        )
    ).all():
        figures(currency).applications_by_status[ApplicationStatus(app_status).value] = count

    # Decisions recorded in the period.
    for currency, decision, count in session.execute(
        select(LoanApplication.currency, LendingDecision.decision, func.count())
        .join(LoanApplication, LoanApplication.id == LendingDecision.application_id)
        .where(LendingDecision.created_at >= start, LendingDecision.created_at < end)
        .group_by(LoanApplication.currency, LendingDecision.decision)
    ).all():
        target = figures(currency)
        if decision is DecisionOutcome.APPROVED:
            target.approved = count
        else:
            target.declined = count

    # Loan accounts: status, disbursements in the period, outstanding and arrears now.
    accounts = session.execute(
        select(
            LoanAccount.currency,
            LoanAccount.status,
            LoanAccount.disbursed_on,
            LoanAccount.principal,
            LoanAccount.outstanding_principal,
            LoanAccount.days_past_due,
        )
    ).all()
    for currency, loan_status, disbursed_on, principal, outstanding, dpd in accounts:
        target = figures(currency)
        target.loans_by_status[loan_status.value] = (
            target.loans_by_status.get(loan_status.value, 0) + 1
        )
        if date_from <= disbursed_on <= date_to:
            target.loans_disbursed += 1
            target.principal_disbursed += principal
        if loan_status in REPAYABLE:
            target.outstanding_principal += outstanding
            if loan_status is LoanStatus.IN_ARREARS:
                target.outstanding_principal_in_arrears += outstanding
            bucket = target.arrears_buckets[arrears.bucket(dpd)]
            bucket.accounts += 1
            bucket.outstanding_principal += outstanding

    # Repayments received in the period (value dates).
    for currency, total in session.execute(
        select(LoanTransaction.currency, func.sum(LoanTransaction.amount))
        .where(
            LoanTransaction.transaction_type.in_(
                [TransactionType.REPAYMENT, TransactionType.SETTLEMENT]
            ),
            LoanTransaction.value_date >= date_from,
            LoanTransaction.value_date <= date_to,
        )
        .group_by(LoanTransaction.currency)
    ).all():
        figures(currency).repayments_received = total

    # Collection rate: paid ÷ due for instalments of the effective schedule due in the period
    # (and not after today). Cancelled instalments of settled loans owed no interest or fees.
    collections_through = min(date_to, today)
    # An instalment row is effective unless a later schedule version of the same loan starts
    # at or before its number (that version replaced it).
    later = aliased(RepaymentSchedule)
    replaced = (
        select(later.id)
        .where(
            later.loan_account_id == RepaymentSchedule.loan_account_id,
            later.version > RepaymentSchedule.version,
            later.first_instalment_number <= ScheduleInstalment.instalment_number,
        )
        .exists()
    )
    paid = (
        ScheduleInstalment.interest_paid
        + ScheduleInstalment.principal_paid
        + ScheduleInstalment.service_fee_paid
    )
    totals: defaultdict[Currency, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for currency, due, collected in session.execute(
        select(LoanAccount.currency, func.sum(ScheduleInstalment.total_due), func.sum(paid))
        .join(RepaymentSchedule, RepaymentSchedule.id == ScheduleInstalment.schedule_id)
        .join(LoanAccount, LoanAccount.id == RepaymentSchedule.loan_account_id)
        .where(
            ScheduleInstalment.due_date >= date_from,
            ScheduleInstalment.due_date <= collections_through,
            ScheduleInstalment.status != InstalmentStatus.CANCELLED,
            ~replaced,
        )
        .group_by(LoanAccount.currency)
    ).all():
        totals[currency] = [due, collected]
    for currency, (due, collected) in totals.items():
        target = figures(currency)
        target.amount_due, target.amount_collected = due, collected

    return Portfolio(
        date_from=date_from,
        date_to=date_to,
        collections_through=collections_through,
        currencies=[by_currency[c] for c in sorted(by_currency)],
    )
