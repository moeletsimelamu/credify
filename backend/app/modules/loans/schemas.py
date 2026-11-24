from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.money import Currency, MoneyAmount, PositiveMoney
from app.modules.loans.engine import arrears
from app.modules.loans.engine.amortisation import Schedule
from app.modules.loans.engine.ledger import (
    ArrearsBucket,
    ChargeType,
    Component,
    InstalmentStatus,
    LoanStatus,
    ScheduleReason,
    TransactionType,
)
from app.modules.loans.engine.settlement import SettlementQuote
from app.modules.loans.models import LoanAccount


class ScheduleRow(BaseModel):
    number: int
    due_date: date
    opening_balance: MoneyAmount
    instalment: MoneyAmount
    interest: MoneyAmount
    principal: MoneyAmount
    service_fee: MoneyAmount
    total_due: MoneyAmount
    closing_balance: MoneyAmount


class RepaymentSchedulePreview(BaseModel):
    """An illustrative schedule. Nothing is stored and no loan exists."""

    engine_version: str
    currency: Currency
    amount: MoneyAmount
    initiation_fee: MoneyAmount
    principal: MoneyAmount  # amount + capitalised initiation fee
    annual_interest_rate: Decimal
    term_months: int
    monthly_service_fee: MoneyAmount
    disbursement_date: date
    level_instalment: MoneyAmount  # amortising instalment, service fee excluded
    first_total_due: MoneyAmount  # what the first month costs, service fee included
    total_interest: MoneyAmount
    total_service_fees: MoneyAmount
    total_repayable: MoneyAmount
    cost_of_credit: MoneyAmount
    instalments: list[ScheduleRow]

    @classmethod
    def from_schedule(cls, schedule: Schedule, currency: Currency) -> RepaymentSchedulePreview:
        terms = schedule.terms
        return cls(
            engine_version=schedule.version,
            currency=currency,
            amount=terms.amount,
            initiation_fee=terms.initiation_fee,
            principal=schedule.principal,
            annual_interest_rate=terms.annual_interest_rate,
            term_months=terms.term_months,
            monthly_service_fee=terms.monthly_service_fee,
            disbursement_date=schedule.disbursement_date,
            level_instalment=schedule.level_instalment,
            first_total_due=schedule.first_total_due,
            total_interest=schedule.total_interest,
            total_service_fees=schedule.total_service_fees,
            total_repayable=schedule.total_repayable,
            cost_of_credit=schedule.cost_of_credit,
            instalments=[
                ScheduleRow(
                    number=row.number,
                    due_date=row.due_date,
                    opening_balance=row.opening_balance,
                    instalment=row.instalment,
                    interest=row.interest,
                    principal=row.principal,
                    service_fee=row.service_fee,
                    total_due=row.total_due,
                    closing_balance=row.closing_balance,
                )
                for row in schedule.instalments
            ],
        )


# --- Loan accounts -------------------------------------------------------------------------

IdempotencyKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,100}$")]


class LoanAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, validate_by_name=True, validate_by_alias=True)
    id: uuid.UUID
    account_number: str
    application_id: uuid.UUID
    applicant_id: uuid.UUID
    decision_id: uuid.UUID
    status: LoanStatus
    currency: Currency
    approved_amount: MoneyAmount
    initiation_fee: MoneyAmount
    principal: MoneyAmount  # amount financed: approved amount + capitalised initiation fee
    annual_interest_rate: Decimal
    term_months: int
    instalment_amount: MoneyAmount  # amortising instalment, service fee excluded
    monthly_service_fee: MoneyAmount
    late_fee: MoneyAmount
    grace_period_days: int
    early_settlement_fee_rate: Decimal
    disbursed_on: date
    first_due_date: date
    maturity_date: date
    outstanding_principal: MoneyAmount
    arrears_amount: MoneyAmount
    days_past_due: int
    arrears_bucket: ArrearsBucket
    position_as_of: date  # the date the cached position above was calculated for
    settled_on: date | None
    version: int = Field(validation_alias="version_id")
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _bucket(cls, data: Any) -> Any:
        if isinstance(data, LoanAccount):
            return {
                **{
                    column.key: getattr(data, column.key)
                    for column in LoanAccount.__table__.columns
                },
                "arrears_bucket": arrears.bucket(data.days_past_due),
            }
        return data


class InstalmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, validate_by_name=True, validate_by_alias=True)
    number: int = Field(validation_alias="instalment_number")
    due_date: date
    opening_balance: MoneyAmount
    interest_due: MoneyAmount
    principal_due: MoneyAmount
    service_fee_due: MoneyAmount
    total_due: MoneyAmount
    closing_balance: MoneyAmount
    interest_paid: MoneyAmount
    principal_paid: MoneyAmount
    service_fee_paid: MoneyAmount
    status: InstalmentStatus


class EffectiveInstalmentRead(InstalmentRead):
    schedule_version: int


class ScheduleVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    reason: ScheduleReason
    engine_version: str
    first_instalment_number: int
    is_current: bool
    transaction_id: uuid.UUID | None
    created_at: datetime


class ScheduleVersionDetail(ScheduleVersionRead):
    instalments: list[InstalmentRead]


class LoanScheduleRead(BaseModel):
    """The effective schedule: each instalment from the newest version that contains it."""

    loan_account_id: uuid.UUID
    currency: Currency
    current_version: int
    instalments: list[EffectiveInstalmentRead]


class AllocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    component: Component
    amount: MoneyAmount
    schedule_instalment_id: uuid.UUID | None
    loan_charge_id: uuid.UUID | None


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    loan_account_id: uuid.UUID
    transaction_type: TransactionType
    amount: MoneyAmount
    currency: Currency
    value_date: date
    channel: str
    reference: str
    recorded_by: uuid.UUID | None
    balance_after: MoneyAmount
    created_at: datetime
    allocations: list[AllocationRead] = []


class ChargeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    charge_type: ChargeType
    amount: MoneyAmount
    amount_paid: MoneyAmount
    charged_on: date
    schedule_instalment_id: uuid.UUID | None


class RepaymentCreate(BaseModel):
    """A simulated repayment. No money moves; the amount is applied through the waterfall."""

    model_config = ConfigDict(extra="forbid")
    amount: PositiveMoney
    value_date: date | None = None  # defaults to today's business date


class SettlementCreate(BaseModel):
    """Pay today's settlement quote. The amount must equal the quote exactly."""

    model_config = ConfigDict(extra="forbid")
    amount: PositiveMoney


class PaymentRead(BaseModel):
    transaction: TransactionRead
    account: LoanAccountRead
    replayed: bool  # true when an Idempotency-Key was replayed: nothing new was recorded


class SettlementQuoteRead(BaseModel):
    loan_account_id: uuid.UUID
    currency: Currency
    as_of: date
    accrual_start: date
    accrued_days: int
    outstanding_principal: MoneyAmount
    principal_not_yet_due: MoneyAmount
    accrued_interest: MoneyAmount
    arrears_interest: MoneyAmount
    arrears_principal: MoneyAmount
    arrears_service_fees: MoneyAmount
    outstanding_charges: MoneyAmount
    projected_late_fees: MoneyAmount
    settlement_fee: MoneyAmount
    total: MoneyAmount
    engine_version: str
    note: str = (
        "Valid only for the as_of date. Paying exactly this amount on that date settles the loan."
    )

    @classmethod
    def from_quote(
        cls, account: LoanAccount, quote: SettlementQuote, engine_version: str
    ) -> SettlementQuoteRead:
        return cls(
            loan_account_id=account.id,
            currency=account.currency,
            as_of=quote.as_of,
            accrual_start=quote.accrual_start,
            accrued_days=quote.accrued_days,
            outstanding_principal=quote.outstanding_principal,
            principal_not_yet_due=quote.principal_not_yet_due,
            accrued_interest=quote.accrued_interest,
            arrears_interest=quote.arrears_interest,
            arrears_principal=quote.arrears_principal,
            arrears_service_fees=quote.arrears_service_fees,
            outstanding_charges=quote.outstanding_charges,
            projected_late_fees=quote.projected_late_fees,
            settlement_fee=quote.settlement_fee,
            total=quote.total,
            engine_version=engine_version,
        )
