from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.money import (
    DEFAULT_CURRENCY,
    Currency,
    MoneyAmount,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    Ratio,
)

ProductCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,49}$")]
ProductName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
TermMonths = Annotated[int, Field(ge=1, le=120)]
GraceDays = Annotated[int, Field(ge=0, le=90)]


class ProductTerms(BaseModel):
    """Every editable product field. Cross-field rules live in ``validate_terms``."""

    name: ProductName
    description: Description | None = None
    min_amount: PositiveMoney
    max_amount: PositiveMoney
    min_term_months: TermMonths
    max_term_months: TermMonths
    annual_interest_rate: Rate
    initiation_fee: NonNegativeMoney
    monthly_service_fee: NonNegativeMoney
    late_fee: NonNegativeMoney
    grace_period_days: GraceDays
    early_settlement_fee_rate: Rate
    max_dti: Ratio
    warn_dti: Ratio
    min_residual_amount: NonNegativeMoney
    min_residual_rate: Ratio


def validate_terms(terms: ProductTerms) -> list[str]:
    """Cross-field rules; mirrored by CHECK constraints on ``loan_products``."""
    problems: list[str] = []
    if terms.min_amount > terms.max_amount:
        problems.append("min_amount must not exceed max_amount")
    if terms.min_term_months > terms.max_term_months:
        problems.append("min_term_months must not exceed max_term_months")
    if terms.warn_dti <= 0:
        problems.append("warn_dti must be greater than zero")
    if terms.warn_dti > terms.max_dti:
        problems.append("warn_dti must not exceed max_dti")
    if terms.min_residual_rate >= 1:
        problems.append("min_residual_rate must be below 1")
    return problems


class ProductCreate(ProductTerms):
    model_config = ConfigDict(extra="forbid")
    code: ProductCode
    currency: Currency = DEFAULT_CURRENCY

    @model_validator(mode="after")
    def _check_terms(self) -> Self:
        problems = validate_terms(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


class ProductUpdate(BaseModel):
    """Partial update. ``code`` and ``currency`` are immutable once a product exists."""

    model_config = ConfigDict(extra="forbid")
    name: ProductName | None = None
    description: Description | None = None
    min_amount: PositiveMoney | None = None
    max_amount: PositiveMoney | None = None
    min_term_months: TermMonths | None = None
    max_term_months: TermMonths | None = None
    annual_interest_rate: Rate | None = None
    initiation_fee: NonNegativeMoney | None = None
    monthly_service_fee: NonNegativeMoney | None = None
    late_fee: NonNegativeMoney | None = None
    grace_period_days: GraceDays | None = None
    early_settlement_fee_rate: Rate | None = None
    max_dti: Ratio | None = None
    warn_dti: Ratio | None = None
    min_residual_amount: NonNegativeMoney | None = None
    min_residual_rate: Ratio | None = None
    is_active: bool | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    currency: Currency
    min_amount: MoneyAmount
    max_amount: MoneyAmount
    min_term_months: int
    max_term_months: int
    annual_interest_rate: Rate
    initiation_fee: MoneyAmount
    monthly_service_fee: MoneyAmount
    late_fee: MoneyAmount
    grace_period_days: int
    early_settlement_fee_rate: Rate
    max_dti: Ratio
    warn_dti: Ratio
    min_residual_amount: MoneyAmount
    min_residual_rate: Ratio
    is_active: bool
    created_at: datetime
    updated_at: datetime
