from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import FixedClock
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import RefreshToken, Role, User
from tests.conftest import DEFAULT_PASSWORD, bearer, login

pytestmark = pytest.mark.integration

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def _actions(session: Session) -> list[str]:
    session.expire_all()
    return list(session.scalars(select(AuditEvent.action).order_by(AuditEvent.occurred_at)))


class TestRegistration:
    def test_registration_creates_an_applicant(self, client: TestClient, db_session: Session):
        response = client.post(
            REGISTER,
            json={
                "email": "Thandi@Example.com",
                "password": DEFAULT_PASSWORD,
                "full_name": "Thandi M",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["role"] == "APPLICANT"
        assert "password" not in response.text
        assert "password_hash" not in body
        assert "user.registered" in _actions(db_session)

    def test_registration_cannot_choose_a_role(self, client: TestClient):
        response = client.post(
            REGISTER,
            json={
                "email": "x@example.com",
                "password": DEFAULT_PASSWORD,
                "full_name": "Mallory",
                "role": "ADMIN",
            },
        )
        assert response.status_code == 422

    def test_duplicate_email_is_case_insensitive(self, client: TestClient):
        payload = {"email": "dup@example.com", "password": DEFAULT_PASSWORD, "full_name": "A B"}
        assert client.post(REGISTER, json=payload).status_code == 201
        response = client.post(REGISTER, json={**payload, "email": "DUP@example.com"})
        assert response.status_code == 409
        assert response.json()["code"] == "CONFLICT"

    def test_short_password_is_rejected_without_echoing_it(self, client: TestClient):
        response = client.post(
            REGISTER, json={"email": "a@example.com", "password": "short-pw", "full_name": "A B"}
        )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "short-pw" not in response.text


class TestLogin:
    def test_login_returns_access_token_and_httponly_refresh_cookie(
        self, client: TestClient, make_user: Callable[..., User]
    ):
        user = make_user(Role.LOAN_OFFICER)
        response = client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["role"] == "LOAN_OFFICER"
        assert "application:recommend" in body["user"]["permissions"]
        cookie = response.headers["set-cookie"]
        assert "credify_refresh=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert "Path=/api/v1/auth" in cookie

        me = client.get(ME, headers=bearer(body["access_token"]))
        assert me.status_code == 200
        assert me.json()["email"] == user.email

    def test_unknown_account_and_wrong_password_look_identical(
        self, client: TestClient, make_user: Callable[..., User]
    ):
        user = make_user()
        wrong_pw = client.post(LOGIN, json={"email": user.email, "password": "not-the-password"})
        unknown = client.post(LOGIN, json={"email": "nobody@example.com", "password": "x" * 12})
        assert wrong_pw.status_code == unknown.status_code == 401
        assert wrong_pw.json()["detail"] == unknown.json()["detail"] == "Invalid email or password"

    def test_failed_logins_are_audited(
        self, client: TestClient, db_session: Session, make_user: Callable[..., User]
    ):
        user = make_user()
        client.post(LOGIN, json={"email": user.email, "password": "not-the-password"})
        assert "auth.login_failed" in _actions(db_session)

    def test_account_locks_after_repeated_failures_and_unlocks_later(
        self,
        client: TestClient,
        clock: FixedClock,
        make_user: Callable[..., User],
        settings,
    ):
        user = make_user()
        for _ in range(settings.login_max_attempts):
            client.post(LOGIN, json={"email": user.email, "password": "not-the-password"})
        # Correct password is refused while locked, with the same generic message.
        locked = client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
        assert locked.status_code == 401
        assert locked.json()["detail"] == "Invalid email or password"

        clock.set(clock.now() + timedelta(minutes=settings.login_lockout_minutes, seconds=1))
        assert (
            client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD}).status_code
            == 200
        )

    def test_inactive_user_cannot_log_in(self, client: TestClient, make_user: Callable[..., User]):
        user = make_user(is_active=False)
        response = client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 401


class TestAccessToken:
    def test_missing_and_malformed_tokens_are_rejected(self, client: TestClient):
        assert client.get(ME).status_code == 401
        response = client.get(ME, headers=bearer("not-a-jwt"))
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_access_token_expires(
        self, client: TestClient, clock: FixedClock, make_user: Callable[..., User], settings
    ):
        user = make_user()
        token = login(client, user.email)
        clock.set(clock.now() + timedelta(minutes=settings.access_token_ttl_minutes))
        assert client.get(ME, headers=bearer(token)).status_code == 401

    def test_deactivation_takes_effect_before_token_expiry(
        self, client: TestClient, db_session: Session, make_user: Callable[..., User]
    ):
        user = make_user()
        token = login(client, user.email)
        user.is_active = False
        db_session.commit()
        assert client.get(ME, headers=bearer(token)).status_code == 401


class TestRefreshRotation:
    def test_refresh_rotates_the_token(
        self, client: TestClient, db_session: Session, make_user: Callable[..., User]
    ):
        user = make_user()
        login(client, user.email)
        first_cookie = client.cookies.get("credify_refresh")

        response = client.post(REFRESH)
        assert response.status_code == 200, response.text
        second_cookie = client.cookies.get("credify_refresh")
        assert second_cookie
        assert second_cookie != first_cookie

        tokens = db_session.scalars(select(RefreshToken)).all()
        assert len(tokens) == 2
        old = next(t for t in tokens if t.revoked_at is not None)
        new = next(t for t in tokens if t.revoked_at is None)
        assert old.replaced_by_id == new.id
        assert old.family_id == new.family_id
        assert first_cookie not in {t.token_hash for t in tokens}  # stored hashed only

    def test_reusing_a_rotated_token_revokes_the_whole_family(
        self, client: TestClient, db_session: Session, make_user: Callable[..., User]
    ):
        user = make_user()
        login(client, user.email)
        stolen = client.cookies.get("credify_refresh")
        assert client.post(REFRESH).status_code == 200  # legitimate rotation

        client.cookies.set("credify_refresh", stolen, path="/api/v1/auth")
        assert client.post(REFRESH).status_code == 401  # replay detected

        db_session.expire_all()
        assert all(t.revoked_at is not None for t in db_session.scalars(select(RefreshToken)))
        assert "auth.refresh_token_reuse_detected" in _actions(db_session)

    def test_expired_refresh_token_is_rejected(
        self, client: TestClient, clock: FixedClock, make_user: Callable[..., User], settings
    ):
        user = make_user()
        login(client, user.email)
        clock.set(clock.now() + timedelta(days=settings.refresh_token_ttl_days, seconds=1))
        assert client.post(REFRESH).status_code == 401

    def test_refresh_without_cookie_is_rejected(self, client: TestClient):
        assert client.post(REFRESH).status_code == 401

    def test_logout_revokes_the_refresh_token(
        self, client: TestClient, make_user: Callable[..., User]
    ):
        user = make_user()
        login(client, user.email)
        token = client.cookies.get("credify_refresh")
        assert client.post(LOGOUT).status_code == 204
        client.cookies.set("credify_refresh", token, path="/api/v1/auth")
        assert client.post(REFRESH).status_code == 401
