"""Pin the role → permission matrix from ARCHITECTURE.md §4 so changes are deliberate."""

from __future__ import annotations

import pytest

from app.modules.identity.models import Role
from app.modules.identity.permissions import ROLE_PERMISSIONS, Permission, has_permission

P = Permission


def test_every_role_has_a_permission_set() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        (Role.ADMIN, P.USER_MANAGE, True),
        (Role.LOAN_OFFICER, P.USER_MANAGE, False),
        (Role.CREDIT_ANALYST, P.USER_MANAGE, False),
        (Role.APPLICANT, P.USER_MANAGE, False),
        (Role.ADMIN, P.AUDIT_READ, True),
        (Role.LOAN_OFFICER, P.AUDIT_READ, False),
        # Only loan officers recommend; only analysts and admins decide (maker–checker).
        (Role.LOAN_OFFICER, P.APPLICATION_RECOMMEND, True),
        (Role.CREDIT_ANALYST, P.APPLICATION_RECOMMEND, False),
        (Role.LOAN_OFFICER, P.APPLICATION_DECIDE, False),
        (Role.CREDIT_ANALYST, P.APPLICATION_DECIDE, True),
        (Role.ADMIN, P.APPLICATION_DECIDE, True),
        (Role.APPLICANT, P.APPLICATION_DECIDE, False),
        (Role.CREDIT_ANALYST, P.RISK_RUN, True),
        (Role.LOAN_OFFICER, P.RISK_RUN, False),
        (Role.APPLICANT, P.APPLICATION_READ_ANY, False),
        (Role.APPLICANT, P.APPLICATION_CREATE_OWN, True),
        (Role.APPLICANT, P.REPAYMENT_RECORD, False),
        (Role.APPLICANT, P.REPAYMENT_RECORD_OWN, True),
        (Role.APPLICANT, P.ANALYTICS_PORTFOLIO, False),
    ],
)
def test_permission_matrix(role: Role, permission: Permission, expected: bool) -> None:
    assert has_permission(role, permission) is expected


def test_no_role_can_both_recommend_and_decide() -> None:
    for role, permissions in ROLE_PERMISSIONS.items():
        assert not {P.APPLICATION_RECOMMEND, P.APPLICATION_DECIDE} <= permissions, role


def test_applicants_hold_no_cross_tenant_read_permissions() -> None:
    applicant = ROLE_PERMISSIONS[Role.APPLICANT]
    assert not any(p.value.endswith(":read_any") for p in applicant)
