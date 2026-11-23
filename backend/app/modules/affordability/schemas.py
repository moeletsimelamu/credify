from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.money import Currency, MoneyAmount
from app.modules.affordability.domain.calculator import Outcome
from app.modules.affordability.models import RunReason


class AffordabilityAssessmentRead(BaseModel):
    """An immutable assessment version. Money and ratios are strings in JSON."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    version: int
    calculator_version: str
    run_reason: RunReason
    outcome: Outcome
    currency: Currency
    gross_monthly_income: MoneyAmount
    net_monthly_income: MoneyAmount
    declared_living_expenses: MoneyAmount
    expense_norm: MoneyAmount
    applied_living_expenses: MoneyAmount
    existing_debt_instalments: MoneyAmount
    disposable_income: MoneyAmount
    proposed_instalment: MoneyAmount
    residual_income: MoneyAmount
    required_buffer: MoneyAmount
    debt_to_income_ratio: Decimal | None
    repayment_burden_ratio: Decimal | None
    new_loan_burden_ratio: Decimal | None
    rule_results: list[dict[str, Any]]
    inputs_snapshot: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime
