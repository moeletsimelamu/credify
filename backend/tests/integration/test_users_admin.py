from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.identity.models import RefreshToken, Role, User
from tests.conftest import DEFAULT_PASSWORD, bearer, login

pytestmark = pytest.mark.integration

USERS = "/api/v1/users"


@pytest.fixture
def admin_token(client: TestClient, make_user: Callable[..., User]) -> str:
    admin = make_user(Role.ADMIN)
    return login(client, admin.email)


@pytest.mark.parametrize("role", [Role.APPLICANT, Role.LOAN_OFFICER, Role.CREDIT_ANALYST])
def test_non_admins_cannot_manage_users(
    client: TestClient, make_user: Callable[..., User], role: Role
):
    token = login(client, make_user(role).email)
    response = client.get(USERS, headers=bearer(token))
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    create = client.post(
        USERS,
        headers=bearer(token),
        json={
            "email": "new@example.com",
            "full_name": "New Staff",
            "role": "ADMIN",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert create.status_code == 403


def test_admin_creates_staff_and_it_is_audited(
    client: TestClient, admin_token: str, db_session: Session
):
    response = client.post(
        USERS,
        headers=bearer(admin_token),
        json={
            "email": "officer@example.com",
            "full_name": "Lerato Officer",
            "role": "LOAN_OFFICER",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "LOAN_OFFICER"
    event = db_session.scalar(select(AuditEvent).where(AuditEvent.action == "user.created"))
    assert event is not None
    assert event.changes == {
        "full_name": {"from": None, "to": "Lerato Officer"},
        "is_active": {"from": None, "to": True},
        "role": {"from": None, "to": "LOAN_OFFICER"},
    }
    assert login(client, "officer@example.com")


def test_list_users_is_paginated_and_filterable(
    client: TestClient, admin_token: str, make_user: Callable[..., User]
):
    for _ in range(3):
        make_user(Role.APPLICANT)
    response = client.get(
        USERS, headers=bearer(admin_token), params={"role": "APPLICANT", "page_size": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert (
        client.get(USERS, headers=bearer(admin_token), params={"page_size": 500}).status_code == 422
    )


def test_deactivating_a_user_revokes_their_sessions(
    client: TestClient, admin_token: str, make_user: Callable[..., User], db_session: Session
):
    target = make_user(Role.LOAN_OFFICER)
    # Log the target in with a separate cookie jar so the admin's session is untouched.
    with TestClient(client.app) as other:
        login(other, target.email)
    response = client.patch(
        f"{USERS}/{target.id}", headers=bearer(admin_token), json={"is_active": False}
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    tokens = db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == target.id))
    assert all(t.revoked_at is not None for t in tokens)


def test_admin_cannot_demote_or_deactivate_themselves(client: TestClient, admin_token: str):
    me = client.get("/api/v1/auth/me", headers=bearer(admin_token)).json()
    demote = client.patch(
        f"{USERS}/{me['id']}", headers=bearer(admin_token), json={"role": "APPLICANT"}
    )
    deactivate = client.patch(
        f"{USERS}/{me['id']}", headers=bearer(admin_token), json={"is_active": False}
    )
    assert demote.status_code == deactivate.status_code == 422


def test_role_change_takes_effect_immediately(
    client: TestClient, admin_token: str, make_user: Callable[..., User]
):
    analyst = make_user(Role.CREDIT_ANALYST)
    with TestClient(client.app) as other:
        analyst_token = login(other, analyst.email)
        client.patch(f"{USERS}/{analyst.id}", headers=bearer(admin_token), json={"role": "ADMIN"})
        # The old token now carries a stale role claim, but the server reads the role from
        # the database, so the new permission applies at once.
        assert other.get(USERS, headers=bearer(analyst_token)).status_code == 200


def test_unknown_user_returns_problem_404(client: TestClient, admin_token: str):
    response = client.get(
        f"{USERS}/00000000-0000-7000-8000-000000000000", headers=bearer(admin_token)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
