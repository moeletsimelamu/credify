"""Shared fixtures.

Integration tests run against a real PostgreSQL database named by
``CREDIFY_TEST_DATABASE_URL`` (read from the environment or the repository's ``.env``).
The schema is built with the real Alembic migrations, so migrations are tested too.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
TABLES = (
    "audit_events",
    "refresh_tokens",
    "document_references",
    "information_requests",
    "review_notes",
    "application_status_history",
    "loan_applications",
    "employment_records",
    "income_records",
    "expense_records",
    "credit_commitments",
    "applicants",
    "loan_products",
    "users",
)


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


# --- Phase 3 helpers: products, applicant profiles, applications -------------------------

PRODUCTS = "/api/v1/products"
APPLICANTS = "/api/v1/applicants"
APPLICATIONS = "/api/v1/applications"


@dataclass(frozen=True)
class Party:
    """A user plus a live access token."""

    user: User
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.token)


@pytest.fixture
def make_party(client: TestClient, make_user: Callable[..., User]) -> Callable[..., Party]:
    def factory(role: Role = Role.APPLICANT) -> Party:
        user = make_user(role)
        return Party(user=user, token=login(client, user.email))

    return factory


@pytest.fixture
def admin(make_party: Callable[..., Party]) -> Party:
    return make_party(Role.ADMIN)


@pytest.fixture
def officer(make_party: Callable[..., Party]) -> Party:
    return make_party(Role.LOAN_OFFICER)


@pytest.fixture
def analyst(make_party: Callable[..., Party]) -> Party:
    return make_party(Role.CREDIT_ANALYST)


def product_payload(**overrides: object) -> dict[str, object]:
    """The Example B product from FINANCIAL_CALCULATIONS.md (21.5 % p.a., ZAR)."""
    payload: dict[str, object] = {
        "code": "PERSONAL_UNSECURED",
        "name": "Personal unsecured loan",
        "description": "Synthetic demo product",
        "currency": "ZAR",
        "min_amount": "5000.00",
        "max_amount": "150000.00",
        "min_term_months": 6,
        "max_term_months": 60,
        "annual_interest_rate": "0.215000",
        "initiation_fee": "0.00",
        "monthly_service_fee": "69.00",
        "late_fee": "100.00",
        "grace_period_days": 5,
        "early_settlement_fee_rate": "0.000000",
        "max_dti": "0.4500",
        "warn_dti": "0.3500",
        "min_residual_amount": "1500.00",
        "min_residual_rate": "0.1000",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def product(client: TestClient, admin: Party) -> dict[str, Any]:
    response = client.post(PRODUCTS, json=product_payload(), headers=admin.headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "first_name": "Thandi",
        "last_name": "Mokoena",
        "date_of_birth": "1990-04-12",
        "phone": "+27 82 555 0100",
        "address_line1": "12 Synthetic Street",
        "city": "Pretoria",
        "postal_code": "0002",
    }
    payload.update(overrides)
    return payload


def create_profile(client: TestClient, party: Party, **overrides: object) -> dict[str, Any]:
    response = client.post(APPLICANTS, json=profile_payload(**overrides), headers=party.headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def complete_profile(client: TestClient, party: Party, applicant_id: str) -> None:
    """Employment, income and expenses: the minimum a submission needs."""
    base = f"{APPLICANTS}/{applicant_id}"
    records = [
        (
            "employment",
            {
                "employment_type": "PERMANENT",
                "employer_name": "Example Holdings",
                "job_title": "Analyst",
                "start_date": "2019-02-01",
                "is_current": True,
            },
        ),
        (
            "income",
            {
                "source_type": "SALARY",
                "frequency": "MONTHLY",
                "gross_amount": "32000.00",
                "net_amount": "24500.00",
            },
        ),
        ("expenses", {"category": "HOUSING", "monthly_amount": "7500.00"}),
    ]
    for path, payload in records:
        response = client.post(f"{base}/{path}", json=payload, headers=party.headers)
        assert response.status_code == 201, response.text


def application_payload(product_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "loan_product_id": product_id,
        "requested_amount": "30000.00",
        "requested_term_months": 24,
        "purpose": "DEBT_CONSOLIDATION",
    }
    payload.update(overrides)
    return payload


def create_application(
    client: TestClient, party: Party, product_id: str, **overrides: object
) -> dict[str, Any]:
    response = client.post(
        APPLICATIONS, json=application_payload(product_id, **overrides), headers=party.headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


@dataclass(frozen=True)
class Borrower:
    party: Party
    applicant: dict[str, Any]


@pytest.fixture
def make_borrower(client: TestClient, make_party: Callable[..., Party]) -> Callable[..., Borrower]:
    def factory(*, complete: bool = True, **profile: object) -> Borrower:
        party = make_party(Role.APPLICANT)
        applicant = create_profile(client, party, **profile)
        if complete:
            complete_profile(client, party, applicant["id"])
        return Borrower(party=party, applicant=applicant)

    return factory


@pytest.fixture
def borrower(make_borrower: Callable[..., Borrower]) -> Borrower:
    return make_borrower()


def force_status(session: Session, application_id: str, status: str) -> None:
    """Move an application to a status the API cannot reach yet.

    SUBMITTED → PENDING_REVIEW requires a persisted affordability assessment (Phase 4), so
    tests of the review workflow use this stand-in. It writes the same history row the
    service would, through the ORM, never touching append-only rows.
    """
    from app.modules.applications.domain.state_machine import ApplicationStatus
    from app.modules.applications.models import ApplicationStatusHistory, LoanApplication

    application = session.get(LoanApplication, uuid.UUID(application_id))
    assert application is not None
    session.add(
        ApplicationStatusHistory(
            id=uuid7(),
            application_id=application.id,
            from_status=application.status,
            to_status=ApplicationStatus(status),
            reason="test fixture",
            # Keep history in clock order: the test clock, not the database clock, applies.
            created_at=application.submitted_at or datetime.now(UTC),
        )
    )
    application.status = ApplicationStatus(status)
    session.commit()
