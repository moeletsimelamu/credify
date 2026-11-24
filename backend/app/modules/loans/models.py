"""Loan accounts, schedules, charges and the transaction log (DATABASE.md §3.6).

The transaction log and its allocations are append-only and are the source of truth.
Paid amounts on instalments and charges, and the cached balances on the account, are
updated in the same transaction as the log and can be reconciled from it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import (
    Base,
    CreatedAt,
    Timestamps,
    UUIDPrimaryKey,
    enum_type,
    money_type,
    rate_type,
)
from app.core.money import Currency
from app.modules.loans.engine.ledger import (
    ChargeType,
    Component,
    InstalmentStatus,
    LoanStatus,
    ScheduleReason,
    TransactionType,
)

ACCOUNT_NUMBER_SEQUENCE = Sequence("loan_account_number_seq", metadata=Base.metadata)


class LoanAccount(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "loan_accounts"
    __table_args__ = (
        CheckConstraint("approved_amount > 0", name="approved_amount_positive"),
        CheckConstraint("principal = approved_amount + initiation_fee", name="principal_financed"),
        CheckConstraint("term_months BETWEEN 1 AND 120", name="term_range"),
        CheckConstraint(
            "annual_interest_rate >= 0 AND annual_interest_rate < 1", name="interest_rate_range"
        ),
        CheckConstraint(
            "initiation_fee >= 0 AND monthly_service_fee >= 0 AND late_fee >= 0",
            name="fees_non_negative",
        ),
        CheckConstraint("grace_period_days >= 0", name="grace_period_non_negative"),
        CheckConstraint(
            "early_settlement_fee_rate >= 0 AND early_settlement_fee_rate < 1",
            name="early_settlement_fee_rate_range",
        ),
        CheckConstraint(
            "outstanding_principal >= 0 AND outstanding_principal <= principal",
            name="outstanding_principal_range",
        ),
        CheckConstraint("arrears_amount >= 0 AND days_past_due >= 0", name="arrears_non_negative"),
        CheckConstraint("(status = 'SETTLED') = (settled_on IS NOT NULL)", name="settled_on"),
        Index("ix_loan_accounts_status", "status"),
    )

    account_number: Mapped[str] = mapped_column(String(20), unique=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), unique=True
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applicants.id", ondelete="RESTRICT"), index=True
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lending_decisions.id", ondelete="RESTRICT"), unique=True
    )
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
    # Terms frozen from the decision and the application's product snapshot.
    approved_amount: Mapped[Decimal] = mapped_column(money_type())
    initiation_fee: Mapped[Decimal] = mapped_column(money_type())
    principal: Mapped[Decimal] = mapped_column(money_type())
    annual_interest_rate: Mapped[Decimal] = mapped_column(rate_type())
    term_months: Mapped[int] = mapped_column(SmallInteger)
    instalment_amount: Mapped[Decimal] = mapped_column(money_type())
    monthly_service_fee: Mapped[Decimal] = mapped_column(money_type())
    late_fee: Mapped[Decimal] = mapped_column(money_type())
    grace_period_days: Mapped[int] = mapped_column(SmallInteger)
    early_settlement_fee_rate: Mapped[Decimal] = mapped_column(rate_type())
    disbursed_on: Mapped[date] = mapped_column(Date)
    first_due_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date] = mapped_column(Date)
    status: Mapped[LoanStatus] = mapped_column(enum_type(LoanStatus, "loan_status"))
    # Cached position, reconciled from the transaction log.
    outstanding_principal: Mapped[Decimal] = mapped_column(money_type())
    arrears_amount: Mapped[Decimal] = mapped_column(money_type())
    days_past_due: Mapped[int] = mapped_column(Integer)
    position_as_of: Mapped[date] = mapped_column(Date)
    settled_on: Mapped[date | None] = mapped_column(Date)
    version_id: Mapped[int] = mapped_column(Integer, server_default="1")

    __mapper_args__ = {"version_id_col": version_id}  # noqa: RUF012


class RepaymentSchedule(UUIDPrimaryKey, CreatedAt, Base):
    """A schedule version. A prepayment recast adds a version from the next instalment on."""

    __tablename__ = "repayment_schedules"
    __table_args__ = (
        UniqueConstraint("loan_account_id", "version", name="uq_repayment_schedules_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_repayment_schedules_one_current",
            "loan_account_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    loan_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_accounts.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[ScheduleReason] = mapped_column(enum_type(ScheduleReason, "schedule_reason"))
    engine_version: Mapped[str] = mapped_column(String(40))
    first_instalment_number: Mapped[int] = mapped_column(SmallInteger)
    is_current: Mapped[bool] = mapped_column(Boolean)
    # The repayment whose prepayment caused this version (NULL for the original schedule).
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("loan_transactions.id", ondelete="RESTRICT")
    )


class ScheduleInstalment(UUIDPrimaryKey, Base):
    __tablename__ = "schedule_instalments"
    __table_args__ = (
        UniqueConstraint("schedule_id", "instalment_number", name="uq_schedule_instalments_number"),
        CheckConstraint(
            "interest_due >= 0 AND principal_due > 0 AND service_fee_due >= 0",
            name="amounts_due_valid",
        ),
        CheckConstraint(
            "total_due = interest_due + principal_due + service_fee_due", name="total_due"
        ),
        CheckConstraint(
            "interest_paid >= 0 AND principal_paid >= 0 AND service_fee_paid >= 0",
            name="paid_non_negative",
        ),
        CheckConstraint(
            "interest_paid <= interest_due AND principal_paid <= principal_due "
            "AND service_fee_paid <= service_fee_due",
            name="paid_within_due",
        ),
        CheckConstraint("closing_balance = opening_balance - principal_due", name="balance_step"),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repayment_schedules.id", ondelete="RESTRICT")
    )
    instalment_number: Mapped[int] = mapped_column(SmallInteger)
    due_date: Mapped[date] = mapped_column(Date)
    opening_balance: Mapped[Decimal] = mapped_column(money_type())
    interest_due: Mapped[Decimal] = mapped_column(money_type())
    principal_due: Mapped[Decimal] = mapped_column(money_type())
    service_fee_due: Mapped[Decimal] = mapped_column(money_type())
    total_due: Mapped[Decimal] = mapped_column(money_type())
    closing_balance: Mapped[Decimal] = mapped_column(money_type())
    interest_paid: Mapped[Decimal] = mapped_column(money_type(), server_default="0")
    principal_paid: Mapped[Decimal] = mapped_column(money_type(), server_default="0")
    service_fee_paid: Mapped[Decimal] = mapped_column(money_type(), server_default="0")
    status: Mapped[InstalmentStatus] = mapped_column(
        enum_type(InstalmentStatus, "instalment_status")
    )


class LoanCharge(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "loan_charges"
    __table_args__ = (
        # One late fee per instalment, ever (NULL instalments are exempt: settlement fees).
        UniqueConstraint(
            "schedule_instalment_id", "charge_type", name="uq_loan_charges_instalment_type"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("amount_paid >= 0 AND amount_paid <= amount", name="paid_within_amount"),
        CheckConstraint(
            "(charge_type = 'LATE_FEE') = (schedule_instalment_id IS NOT NULL)",
            name="late_fee_has_instalment",
        ),
    )

    loan_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_accounts.id", ondelete="RESTRICT"), index=True
    )
    schedule_instalment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_instalments.id", ondelete="RESTRICT")
    )
    charge_type: Mapped[ChargeType] = mapped_column(enum_type(ChargeType, "charge_type"))
    amount: Mapped[Decimal] = mapped_column(money_type())
    amount_paid: Mapped[Decimal] = mapped_column(money_type(), server_default="0")
    charged_on: Mapped[date] = mapped_column(Date)


class LoanTransaction(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only: the account statement."""

    __tablename__ = "loan_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_loan_transactions_account_value_date", "loan_account_id", "value_date"),
    )

    loan_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_accounts.id", ondelete="RESTRICT")
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        enum_type(TransactionType, "transaction_type")
    )
    amount: Mapped[Decimal] = mapped_column(money_type())
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
    value_date: Mapped[date] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    channel: Mapped[str] = mapped_column(String(20), server_default="SIMULATED")
    reference: Mapped[str] = mapped_column(String(200))
    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("loan_transactions.id", ondelete="RESTRICT")
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    # What the borrower owes after this transaction, as at its value date: outstanding
    # principal + interest and fees due by then + unpaid charges.
    balance_after: Mapped[Decimal] = mapped_column(money_type())


class PaymentAllocation(UUIDPrimaryKey, Base):
    """Append-only: where each part of a payment went."""

    __tablename__ = "payment_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(component = 'CHARGE') = (loan_charge_id IS NOT NULL)", name="charge_target"
        ),
        CheckConstraint(
            "(component IN ('SERVICE_FEE', 'INTEREST', 'PRINCIPAL')) "
            "= (schedule_instalment_id IS NOT NULL)",
            name="instalment_target",
        ),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_transactions.id", ondelete="RESTRICT"), index=True
    )
    schedule_instalment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_instalments.id", ondelete="RESTRICT")
    )
    loan_charge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("loan_charges.id", ondelete="RESTRICT")
    )
    component: Mapped[Component] = mapped_column(enum_type(Component, "component"))
    amount: Mapped[Decimal] = mapped_column(money_type())
