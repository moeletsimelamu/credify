from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UUIDPrimaryKey, enum_type, money_type
from app.core.money import Currency
from app.modules.affordability.domain.calculator import Outcome


class RunReason(StrEnum):
    """Why an assessment version was produced."""

    SUBMISSION = "SUBMISSION"  # automatically, when the applicant submits
    RESUBMISSION = "RESUBMISSION"  # automatically, when review resumes after more information
    STAFF_REQUEST = "STAFF_REQUEST"  # a loan officer or credit analyst re-ran it


def _ratio_type() -> Numeric[Decimal]:
    # Wider than the NUMERIC(10,4) product thresholds: an assessment ratio can legitimately
    # be far above 1 (debts many times income), and it must still be stored exactly.
    return Numeric(24, 4)


class AffordabilityAssessment(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only (DATABASE.md §3.5). Re-running creates a new version, never an update."""

    __tablename__ = "affordability_assessments"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_affordability_assessments_version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    # Indexed through the (application_id, version) unique constraint.
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    calculator_version: Mapped[str] = mapped_column(String(40))
    run_reason: Mapped[RunReason] = mapped_column(enum_type(RunReason, "run_reason"))
    inputs_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
    gross_monthly_income: Mapped[Decimal] = mapped_column(money_type())
    net_monthly_income: Mapped[Decimal] = mapped_column(money_type())
    declared_living_expenses: Mapped[Decimal] = mapped_column(money_type())
    expense_norm: Mapped[Decimal] = mapped_column(money_type())
    applied_living_expenses: Mapped[Decimal] = mapped_column(money_type())
    existing_debt_instalments: Mapped[Decimal] = mapped_column(money_type())
    disposable_income: Mapped[Decimal] = mapped_column(money_type())
    proposed_instalment: Mapped[Decimal] = mapped_column(money_type())
    residual_income: Mapped[Decimal] = mapped_column(money_type())
    required_buffer: Mapped[Decimal] = mapped_column(money_type())
    # NULL when the denominator is zero (no counted income): the ratio does not exist.
    debt_to_income_ratio: Mapped[Decimal | None] = mapped_column(_ratio_type())
    repayment_burden_ratio: Mapped[Decimal | None] = mapped_column(_ratio_type())
    new_loan_burden_ratio: Mapped[Decimal | None] = mapped_column(_ratio_type())
    outcome: Mapped[Outcome] = mapped_column(enum_type(Outcome, "affordability_outcome"))
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # NULL for automatic runs (submission, resubmission); the staff member otherwise.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
