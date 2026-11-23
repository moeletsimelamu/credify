from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.core.money import Currency, MoneyAmount
from app.modules.loans.engine.amortisation import Schedule


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
