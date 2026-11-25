"""Role → permission mapping. The single source of truth for server-side authorisation.

See ARCHITECTURE.md §4. Object-level rules (e.g. "applicants only see their own data") are
enforced in each module's service layer on top of these coarse permissions.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.identity.roles import Role


class Permission(StrEnum):
    USER_MANAGE = "user:manage"
    PRODUCT_MANAGE = "product:manage"
    AUDIT_READ = "audit:read"
    APPLICANT_READ_ANY = "applicant:read_any"
    APPLICANT_WRITE_OWN = "applicant:write_own"
    APPLICATION_CREATE_OWN = "application:create_own"
    APPLICATION_READ_ANY = "application:read_any"
    APPLICATION_ASSIGN = "application:assign"
    APPLICATION_REQUEST_INFO = "application:request_info"
    APPLICATION_NOTE = "application:note"
    APPLICATION_RECOMMEND = "application:recommend"
    APPLICATION_DECIDE = "application:decide"
    AFFORDABILITY_RUN = "affordability:run"
    RISK_RUN = "risk:run"
    RISK_ANNOTATE = "risk:annotate"
    LOAN_READ_ANY = "loan:read_any"
    LOAN_DISBURSE = "loan:disburse"
    REPAYMENT_RECORD = "repayment:record"
    REPAYMENT_RECORD_OWN = "repayment:record_own"
    ANALYTICS_PORTFOLIO = "analytics:portfolio"
    ANALYTICS_MODEL = "analytics:model"
    MODEL_MANAGE = "model:manage"  # activate a registered model version (audited)


P = Permission

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(
        {
            P.USER_MANAGE,
            P.PRODUCT_MANAGE,
            P.AUDIT_READ,
            P.APPLICANT_READ_ANY,
            P.APPLICATION_READ_ANY,
            P.APPLICATION_NOTE,
            P.APPLICATION_ASSIGN,
            P.APPLICATION_DECIDE,
            P.LOAN_READ_ANY,
            P.LOAN_DISBURSE,
            P.REPAYMENT_RECORD,
            P.ANALYTICS_PORTFOLIO,
            P.ANALYTICS_MODEL,
            P.MODEL_MANAGE,
        }
    ),
    Role.LOAN_OFFICER: frozenset(
        {
            P.APPLICANT_READ_ANY,
            P.APPLICATION_READ_ANY,
            P.APPLICATION_NOTE,
            P.APPLICATION_ASSIGN,
            P.APPLICATION_REQUEST_INFO,
            P.APPLICATION_RECOMMEND,
            P.AFFORDABILITY_RUN,
            P.LOAN_READ_ANY,
            P.LOAN_DISBURSE,
            P.REPAYMENT_RECORD,
            P.ANALYTICS_PORTFOLIO,
        }
    ),
    Role.CREDIT_ANALYST: frozenset(
        {
            P.APPLICANT_READ_ANY,
            P.APPLICATION_READ_ANY,
            P.APPLICATION_NOTE,
            P.APPLICATION_REQUEST_INFO,
            P.APPLICATION_DECIDE,
            P.AFFORDABILITY_RUN,
            P.RISK_RUN,
            P.RISK_ANNOTATE,
            P.LOAN_READ_ANY,
            P.ANALYTICS_PORTFOLIO,
            P.ANALYTICS_MODEL,
        }
    ),
    Role.APPLICANT: frozenset(
        {
            P.APPLICANT_WRITE_OWN,
            P.APPLICATION_CREATE_OWN,
            P.REPAYMENT_RECORD_OWN,
        }
    ),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def permissions_for(role: Role) -> list[str]:
    return sorted(p.value for p in ROLE_PERMISSIONS.get(role, frozenset()))
