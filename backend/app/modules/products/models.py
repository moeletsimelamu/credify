from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, SmallInteger, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import (
    Base,
    Timestamps,
    UUIDPrimaryKey,
    enum_type,
    money_type,
    rate_type,
    ratio_type,
)
from app.core.money import Currency


class LoanProduct(UUIDPrimaryKey, Timestamps, Base):
    """Configurable loan product. Applications snapshot these terms when submitted."""

    __tablename__ = "loan_products"
    __table_args__ = (
        CheckConstraint("min_amount > 0 AND min_amount <= max_amount", name="amount_range"),
        CheckConstraint(
            "min_term_months >= 1 AND min_term_months <= max_term_months "
            "AND max_term_months <= 120",
            name="term_range",
        ),
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
        CheckConstraint("warn_dti > 0 AND warn_dti <= max_dti AND max_dti <= 1", name="dti_range"),
        CheckConstraint(
            "min_residual_amount >= 0 AND min_residual_rate >= 0 AND min_residual_rate < 1",
            name="residual_buffer_range",
        ),
    )

    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
    min_amount: Mapped[Decimal] = mapped_column(money_type())
    max_amount: Mapped[Decimal] = mapped_column(money_type())
    min_term_months: Mapped[int] = mapped_column(SmallInteger)
    max_term_months: Mapped[int] = mapped_column(SmallInteger)
    annual_interest_rate: Mapped[Decimal] = mapped_column(rate_type())
    initiation_fee: Mapped[Decimal] = mapped_column(money_type())
    monthly_service_fee: Mapped[Decimal] = mapped_column(money_type())
    late_fee: Mapped[Decimal] = mapped_column(money_type())
    grace_period_days: Mapped[int] = mapped_column(SmallInteger)
    early_settlement_fee_rate: Mapped[Decimal] = mapped_column(rate_type())
    max_dti: Mapped[Decimal] = mapped_column(ratio_type())
    warn_dti: Mapped[Decimal] = mapped_column(ratio_type())
    min_residual_amount: Mapped[Decimal] = mapped_column(money_type())
    min_residual_rate: Mapped[Decimal] = mapped_column(ratio_type())
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
