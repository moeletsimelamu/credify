from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, model_validator

from app.core.money import (
    DEFAULT_CURRENCY,
    Currency,
    MoneyAmount,
    NonNegativeMoney,
    PositiveMoney,
)
from app.modules.applicants.domain.enums import (
    NOT_EMPLOYED,
    CommitmentType,
    EmploymentType,
    ExpenseCategory,
    IncomeFrequency,
    IncomeSourceType,
    VerificationStatus,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 ()-]{7,20}$")]
AddressLine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PostalCode = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{2,9}$")
]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
OrgName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
JobTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


# --- Applicant profile -------------------------------------------------------------------


class ApplicantFields(BaseModel):
    first_name: Name
    last_name: Name
    date_of_birth: date
    email: EmailStr
    phone: Phone
    address_line1: AddressLine
    address_line2: AddressLine | None = None
    city: City
    postal_code: PostalCode
    country: CountryCode = "ZA"


class ApplicantCreate(BaseModel):
    """``email`` defaults to the applicant's login email."""

    model_config = ConfigDict(extra="forbid")
    first_name: Name
    last_name: Name
    date_of_birth: date
    email: EmailStr | None = None
    phone: Phone
    address_line1: AddressLine
    address_line2: AddressLine | None = None
    city: City
    postal_code: PostalCode
    country: CountryCode = "ZA"


class ApplicantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: Name | None = None
    last_name: Name | None = None
    date_of_birth: date | None = None
    email: EmailStr | None = None
    phone: Phone | None = None
    address_line1: AddressLine | None = None
    address_line2: AddressLine | None = None
    city: City | None = None
    postal_code: PostalCode | None = None
    country: CountryCode | None = None


class ApplicantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID | None
    synthetic_reference: str
    first_name: str
    last_name: str
    date_of_birth: date
    email: str
    phone: str
    address_line1: str
    address_line2: str | None
    city: str
    postal_code: str
    country: str
    created_at: datetime
    updated_at: datetime


# --- Employment --------------------------------------------------------------------------


class EmploymentFields(BaseModel):
    employment_type: EmploymentType
    employer_name: OrgName | None = None
    job_title: JobTitle | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        employed = self.employment_type not in NOT_EMPLOYED
        if employed and (self.employer_name is None or self.start_date is None):
            raise ValueError("employer_name and start_date are required for this employment type")
        if self.is_current and self.end_date is not None:
            raise ValueError("a current position cannot have an end_date")
        if employed and not self.is_current and self.end_date is None:
            raise ValueError("a past position needs an end_date")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class EmploymentCreate(EmploymentFields):
    model_config = ConfigDict(extra="forbid")


class EmploymentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employment_type: EmploymentType | None = None
    employer_name: OrgName | None = None
    job_title: JobTitle | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None


class EmploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    applicant_id: uuid.UUID
    employment_type: EmploymentType
    employer_name: str | None
    job_title: str | None
    start_date: date | None
    end_date: date | None
    is_current: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Income ------------------------------------------------------------------------------


class IncomeFields(BaseModel):
    source_type: IncomeSourceType
    frequency: IncomeFrequency
    gross_amount: PositiveMoney
    net_amount: PositiveMoney
    currency: Currency = DEFAULT_CURRENCY

    @model_validator(mode="after")
    def _net_not_above_gross(self) -> Self:
        if self.net_amount > self.gross_amount:
            raise ValueError("net_amount must not exceed gross_amount")
        return self


class IncomeCreate(IncomeFields):
    model_config = ConfigDict(extra="forbid")


class IncomeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: IncomeSourceType | None = None
    frequency: IncomeFrequency | None = None
    gross_amount: PositiveMoney | None = None
    net_amount: PositiveMoney | None = None
    currency: Currency | None = None


class IncomeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    applicant_id: uuid.UUID
    source_type: IncomeSourceType
    frequency: IncomeFrequency
    gross_amount: MoneyAmount
    net_amount: MoneyAmount
    currency: Currency
    verification_status: VerificationStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Expenses ----------------------------------------------------------------------------


class ExpenseFields(BaseModel):
    category: ExpenseCategory
    monthly_amount: NonNegativeMoney
    currency: Currency = DEFAULT_CURRENCY


class ExpenseCreate(ExpenseFields):
    model_config = ConfigDict(extra="forbid")


class ExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: ExpenseCategory | None = None
    monthly_amount: NonNegativeMoney | None = None
    currency: Currency | None = None


class ExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    applicant_id: uuid.UUID
    category: ExpenseCategory
    monthly_amount: MoneyAmount
    currency: Currency
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Existing credit commitments ---------------------------------------------------------


class CommitmentFields(BaseModel):
    commitment_type: CommitmentType
    lender_name: OrgName
    monthly_instalment: NonNegativeMoney
    outstanding_balance: NonNegativeMoney
    credit_limit: NonNegativeMoney | None = None
    currency: Currency = DEFAULT_CURRENCY


class CommitmentCreate(CommitmentFields):
    model_config = ConfigDict(extra="forbid")


class CommitmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commitment_type: CommitmentType | None = None
    lender_name: OrgName | None = None
    monthly_instalment: NonNegativeMoney | None = None
    outstanding_balance: NonNegativeMoney | None = None
    credit_limit: NonNegativeMoney | None = None
    currency: Currency | None = None


class CommitmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    applicant_id: uuid.UUID
    commitment_type: CommitmentType
    lender_name: str
    monthly_instalment: MoneyAmount
    outstanding_balance: MoneyAmount
    credit_limit: MoneyAmount | None
    currency: Currency
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FinancialProfileRead(BaseModel):
    """Everything an assessor needs in one response (active records only)."""

    applicant: ApplicantRead
    employment: list[EmploymentRead]
    income: list[IncomeRead]
    expenses: list[ExpenseRead]
    commitments: list[CommitmentRead]
