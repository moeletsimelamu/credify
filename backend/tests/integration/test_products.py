from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role
from tests.conftest import PRODUCTS, Party, product_payload

pytestmark = pytest.mark.integration


def test_admin_creates_product_with_string_money(client: TestClient, admin: Party) -> None:
    response = client.post(PRODUCTS, json=product_payload(), headers=admin.headers)
    assert response.status_code == 201, response.text
    body = response.json()
    # Money and rates travel as strings, never JSON numbers.
    assert body["min_amount"] == "5000.00"
    assert body["annual_interest_rate"] == "0.215000"
    assert body["max_dti"] == "0.4500"
    assert body["currency"] == "ZAR"


@pytest.mark.parametrize("role", [Role.APPLICANT, Role.LOAN_OFFICER, Role.CREDIT_ANALYST])
def test_only_admins_manage_products(
    client: TestClient, make_party: Callable[..., Party], product: dict[str, Any], role: Role
) -> None:
    party = make_party(role)
    assert (
        client.post(PRODUCTS, json=product_payload(code="OTHER"), headers=party.headers).status_code
        == 403
    )
    patch = client.patch(
        f"{PRODUCTS}/{product['id']}", json={"name": "Hacked"}, headers=party.headers
    )
    assert patch.status_code == 403


def test_duplicate_code_conflicts(
    client: TestClient, admin: Party, product: dict[str, Any]
) -> None:
    response = client.post(PRODUCTS, json=product_payload(), headers=admin.headers)
    assert response.status_code == 409


def test_float_money_is_rejected(client: TestClient, admin: Party) -> None:
    response = client.post(PRODUCTS, json=product_payload(min_amount=5000.5), headers=admin.headers)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_update_revalidates_merged_terms_and_is_audited(
    client: TestClient, admin: Party, product: dict[str, Any], db_session: Session
) -> None:
    url = f"{PRODUCTS}/{product['id']}"
    # warn_dti alone is valid, but not above the stored max_dti of 0.45.
    rejected = client.patch(url, json={"warn_dti": "0.5000"}, headers=admin.headers)
    assert rejected.status_code == 422
    assert rejected.json()["violations"] == ["warn_dti must not exceed max_dti"]

    ok = client.patch(url, json={"annual_interest_rate": "0.199000"}, headers=admin.headers)
    assert ok.status_code == 200
    assert ok.json()["annual_interest_rate"] == "0.199000"
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "product.updated")
    ).one()
    assert event.changes == {"annual_interest_rate": {"from": "0.215000", "to": "0.199000"}}


def test_code_and_currency_are_immutable(
    client: TestClient, admin: Party, product: dict[str, Any]
) -> None:
    response = client.patch(
        f"{PRODUCTS}/{product['id']}", json={"currency": "USD"}, headers=admin.headers
    )
    assert response.status_code == 422


def test_inactive_products_are_hidden_from_applicants(
    client: TestClient,
    admin: Party,
    officer: Party,
    make_party: Callable[..., Party],
    product: dict[str, Any],
) -> None:
    applicant = make_party(Role.APPLICANT)
    url = f"{PRODUCTS}/{product['id']}"
    assert client.get(url, headers=applicant.headers).status_code == 200
    client.patch(url, json={"is_active": False}, headers=admin.headers)

    assert client.get(url, headers=applicant.headers).status_code == 404
    listing = client.get(f"{PRODUCTS}?include_inactive=true", headers=applicant.headers).json()
    assert listing["total"] == 0
    # Staff still see it (older applications reference it).
    assert client.get(url, headers=officer.headers).status_code == 200
    staff_listing = client.get(f"{PRODUCTS}?include_inactive=true", headers=officer.headers)
    assert staff_listing.json()["total"] == 1


def test_products_require_authentication(client: TestClient) -> None:
    assert client.get(PRODUCTS).status_code == 401
