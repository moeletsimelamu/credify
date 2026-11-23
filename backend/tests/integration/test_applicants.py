from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role
from tests.conftest import (
    APPLICANTS,
    APPLICATIONS,
    Borrower,
    Party,
    create_application,
    create_profile,
    profile_payload,
)

pytestmark = pytest.mark.integration


def test_applicant_creates_own_profile(
    client: TestClient, make_party: Callable[..., Party], db_session: Session
) -> None:
    party = make_party(Role.APPLICANT)
    profile = create_profile(client, party)
    assert profile["user_id"] == str(party.user.id)
    assert profile["email"] == party.user.email  # defaults to the login email
    assert profile["synthetic_reference"].startswith("SYN-")
    assert client.get(f"{APPLICANTS}/me", headers=party.headers).json()["id"] == profile["id"]
    actions = db_session.scalars(select(AuditEvent.action)).all()
    assert "applicant.created" in actions


def test_one_profile_per_applicant(client: TestClient, borrower: Borrower) -> None:
    response = client.post(APPLICANTS, json=profile_payload(), headers=borrower.party.headers)
    assert response.status_code == 409


def test_minors_cannot_register(client: TestClient, make_party: Callable[..., Party]) -> None:
    party = make_party(Role.APPLICANT)
    # The test clock is 2026-11-18; this applicant turns 18 the next day.
    response = client.post(
        APPLICANTS, json=profile_payload(date_of_birth="2008-11-19"), headers=party.headers
    )
    assert response.status_code == 422
    assert "18" in response.json()["detail"]


def test_staff_cannot_create_or_edit_profiles(
    client: TestClient, officer: Party, borrower: Borrower
) -> None:
    assert (
        client.post(APPLICANTS, json=profile_payload(), headers=officer.headers).status_code == 403
    )
    patch = client.patch(
        f"{APPLICANTS}/{borrower.applicant['id']}", json={"city": "Durban"}, headers=officer.headers
    )
    assert patch.status_code == 403


def test_staff_can_read_any_profile(
    client: TestClient, officer: Party, analyst: Party, admin: Party, borrower: Borrower
) -> None:
    url = f"{APPLICANTS}/{borrower.applicant['id']}/financial-profile"
    for staff in (officer, analyst, admin):
        body = client.get(url, headers=staff.headers).json()
        assert body["applicant"]["id"] == borrower.applicant["id"]
        assert len(body["income"]) == 1
    listing = client.get(f"{APPLICANTS}?search=mokoena", headers=officer.headers).json()
    assert listing["total"] == 1


def test_applicants_cannot_see_each_other(
    client: TestClient, make_borrower: Callable[..., Borrower]
) -> None:
    alice, bob = make_borrower(), make_borrower(first_name="Bob")
    alice_id = alice.applicant["id"]
    bob_headers = bob.party.headers
    income_id = client.get(f"{APPLICANTS}/{alice_id}/income", headers=alice.party.headers).json()[
        0
    ]["id"]

    # Every read and write on someone else's profile is a 404, never a 403.
    requests = [
        ("GET", f"{APPLICANTS}/{alice_id}", None),
        ("GET", f"{APPLICANTS}/{alice_id}/financial-profile", None),
        ("GET", f"{APPLICANTS}/{alice_id}/income", None),
        ("PATCH", f"{APPLICANTS}/{alice_id}", {"city": "Durban"}),
        (
            "POST",
            f"{APPLICANTS}/{alice_id}/expenses",
            {"category": "FOOD", "monthly_amount": "1.00"},
        ),
        ("PATCH", f"{APPLICANTS}/{alice_id}/income/{income_id}", {"net_amount": "1.00"}),
        ("DELETE", f"{APPLICANTS}/{alice_id}/income/{income_id}", None),
    ]
    for method, url, body in requests:
        response = client.request(method, url, json=body, headers=bob_headers)
        assert response.status_code == 404, (method, url, response.status_code)
    # And staff-only listing is forbidden outright.
    assert client.get(APPLICANTS, headers=bob_headers).status_code == 403


def test_financial_record_lifecycle_is_audited(
    client: TestClient, borrower: Borrower, db_session: Session
) -> None:
    base = f"{APPLICANTS}/{borrower.applicant['id']}/commitments"
    headers = borrower.party.headers
    created = client.post(
        base,
        json={
            "commitment_type": "CREDIT_CARD",
            "lender_name": "Example Bank",
            "monthly_instalment": "850.00",
            "outstanding_balance": "12000.00",
            "credit_limit": "20000.00",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    updated = client.patch(
        f"{base}/{record_id}", json={"monthly_instalment": "900.00"}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["monthly_instalment"] == "900.00"

    assert client.delete(f"{base}/{record_id}", headers=headers).status_code == 204
    assert client.get(base, headers=headers).json() == []
    # Soft delete: the row is kept, and a second delete finds nothing active.
    assert client.delete(f"{base}/{record_id}", headers=headers).status_code == 404

    events = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "credit_commitment")
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
    ).all()
    assert [e.action for e in events] == [
        "credit_commitment.created",
        "credit_commitment.updated",
        "credit_commitment.removed",
    ]
    assert events[1].changes == {"monthly_instalment": {"from": "850.00", "to": "900.00"}}


def test_partial_update_revalidates_the_whole_record(
    client: TestClient, borrower: Borrower
) -> None:
    base = f"{APPLICANTS}/{borrower.applicant['id']}/income"
    headers = borrower.party.headers
    record = client.get(base, headers=headers).json()[0]
    # net 24 500 is valid on its own, but not once gross drops below it.
    response = client.patch(
        f"{base}/{record['id']}", json={"gross_amount": "20000.00"}, headers=headers
    )
    assert response.status_code == 422
    assert "net_amount must not exceed gross_amount" in response.json()["detail"]


def test_employment_dates_cannot_be_in_the_future(client: TestClient, borrower: Borrower) -> None:
    response = client.post(
        f"{APPLICANTS}/{borrower.applicant['id']}/employment",
        json={
            "employment_type": "CONTRACT",
            "employer_name": "Future Corp",
            "start_date": "2027-01-01",
            "is_current": True,
        },
        headers=borrower.party.headers,
    )
    assert response.status_code == 422
    assert "future" in response.json()["detail"]


def test_profile_is_locked_while_under_review(
    client: TestClient, borrower: Borrower, product: dict[str, Any]
) -> None:
    headers = borrower.party.headers
    applicant_id = borrower.applicant["id"]
    application = create_application(client, borrower.party, product["id"])
    # Drafts leave the profile editable.
    assert (
        client.patch(
            f"{APPLICANTS}/{applicant_id}", json={"city": "Durban"}, headers=headers
        ).status_code
        == 200
    )

    assert (
        client.post(f"{APPLICATIONS}/{application['id']}/submit", headers=headers).status_code
        == 200
    )
    for method, url, body in [
        ("PATCH", f"{APPLICANTS}/{applicant_id}", {"city": "Cape Town"}),
        (
            "POST",
            f"{APPLICANTS}/{applicant_id}/expenses",
            {"category": "FOOD", "monthly_amount": "3000.00"},
        ),
    ]:
        response = client.request(method, url, json=body, headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "PROFILE_LOCKED"

    # Withdrawing releases the lock.
    client.post(f"{APPLICATIONS}/{application['id']}/withdraw", headers=headers)
    assert (
        client.patch(
            f"{APPLICANTS}/{applicant_id}", json={"city": "Cape Town"}, headers=headers
        ).status_code
        == 200
    )
