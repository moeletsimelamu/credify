"""Health, audit-trail integrity, error format and baseline HTTP security."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.core.clock import FixedClock
from app.main import create_app
from app.modules.identity.models import Role, User
from tests.conftest import bearer, login, make_settings

pytestmark = pytest.mark.integration


def test_health_endpoints(client: TestClient):
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"


def test_ready_reports_503_when_database_is_unreachable(clean_db: Engine, clock: FixedClock):
    settings = make_settings(database_url="postgresql+psycopg://nobody:pw@127.0.0.1:1/none")
    with TestClient(create_app(settings, clock=clock)) as broken:
        response = broken.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


def test_audit_events_cannot_be_modified_or_deleted(
    client: TestClient, make_user: Callable[..., User], clean_db: Engine
):
    login(client, make_user().email)  # produces an auth.login_succeeded event
    for statement in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"), clean_db.begin() as conn:
            conn.execute(text(statement))


def test_audit_log_is_admin_only_and_filterable(client: TestClient, make_user: Callable[..., User]):
    officer_token = login(client, make_user(Role.LOAN_OFFICER).email)
    assert client.get("/api/v1/audit-events", headers=bearer(officer_token)).status_code == 403

    admin_token = login(client, make_user(Role.ADMIN).email)
    response = client.get(
        "/api/v1/audit-events",
        headers=bearer(admin_token),
        params={"action": "auth.login_succeeded"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["actor_role"] for item in body["items"]} == {"LOAN_OFFICER", "ADMIN"}
    assert all(item["request_id"] for item in body["items"])


def test_problem_details_and_request_id(client: TestClient):
    response = client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "trace-12345678"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["x-request-id"] == "trace-12345678"
    body = response.json()
    assert body["status"] == 404
    assert body["request_id"] == "trace-12345678"


def test_invalid_request_id_header_is_replaced(client: TestClient):
    response = client.get("/health/live", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["x-request-id"] != "bad id\nwith newline"


def test_security_headers_are_set(client: TestClient):
    headers = client.get("/health/live").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]


def test_cors_allows_only_the_configured_origin(client: TestClient):
    allowed = client.options(
        "/api/v1/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    blocked = client.options(
        "/api/v1/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in blocked.headers


def test_openapi_is_disabled_in_production(clean_db: Engine, clock: FixedClock):
    settings = make_settings(environment="production", cookie_secure=True)
    with TestClient(create_app(settings, clock=clock)) as prod:
        assert prod.get("/openapi.json").status_code == 404
        assert prod.get("/docs").status_code == 404
