from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPrimaryKey, enum_type, money_type
from app.core.money import Currency
from app.modules.applicants.domain.enums import (
    CommitmentType,
    EmploymentType,
    ExpenseCategory,
    IncomeFrequency,
    IncomeSourceType,
    VerificationStatus,
)


class Applicant(UUIDPrimaryKey, Timestamps, Base):
    """A borrower profile. Linked to the applicant's own login when they self-register."""

    __tablename__ = "applicants"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    date_of_birth: Mapped[date] = mapped_column(Date)
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str] = mapped_column(String(20))
    address_line1: Mapped[str] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(2))
    # A synthetic stand-in for a national ID number; Credify never stores real identifiers.
    synthetic_reference: Mapped[str] = mapped_column(String(20), unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class FinancialRecord(UUIDPrimaryKey, Timestamps):
    """Columns shared by the four financial-profile tables. Rows are soft-deleted."""

    applicant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applicants.id", ondelete="RESTRICT"), index=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class EmploymentRecord(FinancialRecord, Base):
    __tablename__ = "employment_records"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="end_after_start"),
        CheckConstraint("NOT (is_current AND end_date IS NOT NULL)", name="current_has_no_end"),
    )

    employer_name: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(120))
    employment_type: Mapped[EmploymentType] = mapped_column(
        enum_type(EmploymentType, "employment_type")
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool]


class IncomeRecord(FinancialRecord, Base):
    __tablename__ = "income_records"
    __table_args__ = (
        CheckConstraint(
            "gross_amount > 0 AND net_amount > 0 AND net_amount <= gross_amount",
            name="amounts_valid",
        ),
    )

    source_type: Mapped[IncomeSourceType] = mapped_column(
        enum_type(IncomeSourceType, "income_source_type")
    )
    frequency: Mapped[IncomeFrequency] = mapped_column(
        enum_type(IncomeFrequency, "income_frequency")
    )
    gross_amount: Mapped[Decimal] = mapped_column(money_type())
    net_amount: Mapped[Decimal] = mapped_column(money_type())
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_type(VerificationStatus, "verification_status"),
        default=VerificationStatus.DECLARED,
        server_default=VerificationStatus.DECLARED.value,
    )


class ExpenseRecord(FinancialRecord, Base):
    __tablename__ = "expense_records"
    __table_args__ = (CheckConstraint("monthly_amount >= 0", name="amount_non_negative"),)

    category: Mapped[ExpenseCategory] = mapped_column(
        enum_type(ExpenseCategory, "expense_category")
    )
    monthly_amount: Mapped[Decimal] = mapped_column(money_type())
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))


class CreditCommitment(FinancialRecord, Base):
    __tablename__ = "credit_commitments"
    __table_args__ = (
        CheckConstraint(
            "monthly_instalment >= 0 AND outstanding_balance >= 0 "
            "AND (credit_limit IS NULL OR credit_limit >= 0)",
            name="amounts_non_negative",
        ),
    )

    commitment_type: Mapped[CommitmentType] = mapped_column(
        enum_type(CommitmentType, "commitment_type")
    )
    lender_name: Mapped[str] = mapped_column(String(200))
    monthly_instalment: Mapped[Decimal] = mapped_column(money_type())
    outstanding_balance: Mapped[Decimal] = mapped_column(money_type())
    credit_limit: Mapped[Decimal | None] = mapped_column(money_type())
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
