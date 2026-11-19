"""Shared fixtures.

Integration tests run against a real PostgreSQL database named by
``CREDIFY_TEST_DATABASE_URL`` (read from the environment or the repository's ``.env``).
The schema is built with the real Alembic migrations, so migrations are tested too.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.db import build_engine, build_session_factory
from app.core.ids import uuid7
from app.core.security import hash_password
from app.main import create_app
from app.modules.identity.models import Role, User

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_JWT_SECRET = secrets.token_urlsafe(48)
DEFAULT_PASSWORD = "correct-horse-battery-staple"
TABLES = ("audit_events", "refresh_tokens", "users")


def _read_dotenv_value(key: str) -> str | None:
    env_file = BACKEND_DIR.parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip()
    return None


TEST_DATABASE_URL = os.environ.get("CREDIFY_TEST_DATABASE_URL") or _read_dotenv_value(
    "CREDIFY_TEST_DATABASE_URL"
)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": TEST_DATABASE_URL or "postgresql+psycopg://user:pw@127.0.0.1:1/none",
        "jwt_secret": TEST_JWT_SECRET,
        "cookie_secure": False,
        "log_json": False,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="CREDIFY_TEST_DATABASE_URL is not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    assert TEST_DATABASE_URL
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    # Round-trip the migrations so both upgrade() and downgrade() are exercised.
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = build_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(migrated_engine: Engine) -> Iterator[Engine]:
    yield migrated_engine
    with migrated_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 11, 18, 9, 0, tzinfo=UTC))


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(clean_db: Engine, settings: Settings, clock: FixedClock) -> FastAPI:
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session(clean_db: Engine) -> Iterator[Session]:
    session = build_session_factory(clean_db)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def make_user(db_session: Session) -> Callable[..., User]:
    def factory(
        role: Role = Role.APPLICANT,
        *,
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
        is_active: bool = True,
    ) -> User:
        user = User(
            id=uuid7(),
            email=email or f"{role.value.lower()}-{secrets.token_hex(4)}@example.com",
            password_hash=hash_password(password),
            full_name=f"Test {role.value.title()}",
            role=role,
            is_active=is_active,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return factory


def login(client: TestClient, email: str, password: str = DEFAULT_PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
