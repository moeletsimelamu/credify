"""Financial-profile vocabularies (DATABASE.md §3.3). Pure: no SQLAlchemy, no I/O."""

from __future__ import annotations

from enum import StrEnum


class EmploymentType(StrEnum):
    PERMANENT = "PERMANENT"
    CONTRACT = "CONTRACT"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    PART_TIME = "PART_TIME"
    UNEMPLOYED = "UNEMPLOYED"
    RETIRED = "RETIRED"


# Employment types that have no employer, job title or start date to capture.
NOT_EMPLOYED = frozenset({EmploymentType.UNEMPLOYED, EmploymentType.RETIRED})


class IncomeSourceType(StrEnum):
    SALARY = "SALARY"
    SELF_EMPLOYMENT = "SELF_EMPLOYMENT"
    RENTAL = "RENTAL"
    PENSION = "PENSION"
    GRANT = "GRANT"
    OTHER = "OTHER"


class IncomeFrequency(StrEnum):
    WEEKLY = "WEEKLY"
    FORTNIGHTLY = "FORTNIGHTLY"
    MONTHLY = "MONTHLY"
    ANNUALLY = "ANNUALLY"


class VerificationStatus(StrEnum):
    """Simulated verification. Applicant-declared income always starts as DECLARED."""

    DECLARED = "DECLARED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ExpenseCategory(StrEnum):
    HOUSING = "HOUSING"
    TRANSPORT = "TRANSPORT"
    FOOD = "FOOD"
    UTILITIES = "UTILITIES"
    EDUCATION = "EDUCATION"
    INSURANCE = "INSURANCE"
    MEDICAL = "MEDICAL"
    DEPENDANTS = "DEPENDANTS"
    OTHER = "OTHER"


class CommitmentType(StrEnum):
    HOME_LOAN = "HOME_LOAN"
    VEHICLE_FINANCE = "VEHICLE_FINANCE"
    CREDIT_CARD = "CREDIT_CARD"
    PERSONAL_LOAN = "PERSONAL_LOAN"
    STORE_ACCOUNT = "STORE_ACCOUNT"
    STUDENT_LOAN = "STUDENT_LOAN"
    OTHER = "OTHER"
