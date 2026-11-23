"""Roles, kept free of SQLAlchemy so pure domain code can import them."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    LOAN_OFFICER = "LOAN_OFFICER"
    CREDIT_ANALYST = "CREDIT_ANALYST"
    APPLICANT = "APPLICANT"


STAFF_ROLES = frozenset({Role.ADMIN, Role.LOAN_OFFICER, Role.CREDIT_ANALYST})
