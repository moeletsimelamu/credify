from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import build_session_factory
from app.modules.applications.models import ApplicationStatusHistory, LoanApplication
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role
from tests.conftest import (
    APPLICANTS,
    APPLICATIONS,
    PRODUCTS,
    Borrower,
    Party,
    application_payload,
    create_application,
)

pytestmark = pytest.mark.integration


def _url(application: dict[str, Any], suffix: str = "") -> str:
    return f"{APPLICATIONS}/{application['id']}{suffix}"


def _history(client: TestClient, application: dict[str, Any], party: Party) -> list[str]:
    rows = client.get(_url(application, "/status-history"), headers=party.headers).json()
    return [row["to_status"] for row in rows]


@pytest.fixture
def draft(client: TestClient, borrower: Borrower, product: dict[str, Any]) -> dict[str, Any]:
    return create_application(client, borrower.party, product["id"])


@pytest.fixture
def in_review_queue(
    client: TestClient, borrower: Borrower, draft: dict[str, Any]
) -> dict[str, Any]:
    """A submitted application: assessed and queued for review automatically."""
    response = client.post(_url(draft, "/submit"), headers=borrower.party.headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING_REVIEW"
    return draft


# --- Drafts ----------------------------------------------------------------------------------


def test_create_draft(client: TestClient, borrower: Borrower, draft: dict[str, Any]) -> None:
    assert draft["status"] == "DRAFT"
    assert draft["reference"].startswith("CRD-2026-")
    assert draft["currency"] == "ZAR"  # from the product, not the client
    assert draft["requested_amount"] == "30000.00"
    assert draft["version"] == 1
    assert draft["available_actions"] == ["SUBMIT", "WITHDRAW"]
    assert _history(client, draft, borrower.party) == ["DRAFT"]


def test_create_requires_a_profile(
    client: TestClient, make_party: Callable[..., Party], product: dict[str, Any]
) -> None:
    party = make_party(Role.APPLICANT)
    response = client.post(
        APPLICATIONS, json=application_payload(product["id"]), headers=party.headers
    )
    assert response.status_code == 404
    assert "applicant profile" in response.json()["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_amount": "4999.99"},
        {"requested_amount": "150000.01"},
        {"requested_term_months": 61},
    ],
)
def test_create_enforces_product_limits(
    client: TestClient, borrower: Borrower, product: dict[str, Any], overrides: dict[str, Any]
) -> None:
    response = client.post(
        APPLICATIONS,
        json=application_payload(product["id"], **overrides),
        headers=borrower.party.headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "BUSINESS_RULE_VIOLATION"


def test_client_cannot_choose_currency_or_status(
    client: TestClient, borrower: Borrower, product: dict[str, Any]
) -> None:
    for extra in ({"currency": "USD"}, {"status": "APPROVED"}):
        response = client.post(
            APPLICATIONS,
            json=application_payload(product["id"], **extra),
            headers=borrower.party.headers,
        )
        assert response.status_code == 422


def test_one_open_application_at_a_time(
    client: TestClient, borrower: Borrower, product: dict[str, Any], draft: dict[str, Any]
) -> None:
    second = client.post(
        APPLICATIONS, json=application_payload(product["id"]), headers=borrower.party.headers
    )
    assert second.status_code == 409
    assert second.json()["open_application"] == draft["reference"]
    # Once withdrawn, a new application can be started.
    client.post(_url(draft, "/withdraw"), headers=borrower.party.headers)
    assert (
        client.post(
            APPLICATIONS, json=application_payload(product["id"]), headers=borrower.party.headers
        ).status_code
        == 201
    )


def test_staff_cannot_create_applications(
    client: TestClient, officer: Party, product: dict[str, Any]
) -> None:
    response = client.post(
        APPLICATIONS, json=application_payload(product["id"]), headers=officer.headers
    )
    assert response.status_code == 403


def test_update_draft_with_optimistic_locking(
    client: TestClient, borrower: Borrower, draft: dict[str, Any], db_session: Session
) -> None:
    headers = borrower.party.headers
    updated = client.patch(
        _url(draft), json={"requested_amount": "45000.00"}, headers={**headers, "If-Match": '"1"'}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2

    stale = client.patch(
        _url(draft), json={"requested_term_months": 36}, headers={**headers, "If-Match": '"1"'}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"
    assert stale.json()["current_version"] == 2

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "application.updated")
    ).one()
    assert event.changes == {"requested_amount": {"from": "30000.00", "to": "45000.00"}}


def test_malformed_if_match_is_rejected(
    client: TestClient, borrower: Borrower, draft: dict[str, Any]
) -> None:
    response = client.post(
        _url(draft, "/submit"), headers={**borrower.party.headers, "If-Match": "latest"}
    )
    assert response.status_code == 400


def test_concurrent_writes_are_detected(draft: dict[str, Any], clean_db: Engine) -> None:
    """Two sessions read version 1; the second write must fail instead of overwriting."""
    factory = build_session_factory(clean_db)
    application_id = uuid.UUID(draft["id"])
    with factory() as first, factory() as second:
        a = first.get(LoanApplication, application_id)
        b = second.get(LoanApplication, application_id)
        assert a is not None
        assert b is not None
        a.purpose_description = "first writer"
        first.commit()
        b.purpose_description = "second writer"
        from sqlalchemy.orm.exc import StaleDataError

        with pytest.raises(StaleDataError):
            second.commit()


# --- Submission ------------------------------------------------------------------------------


def test_submit_requires_a_complete_profile(
    client: TestClient, make_borrower: Callable[..., Borrower], product: dict[str, Any]
) -> None:
    incomplete = make_borrower(complete=False)
    application = create_application(client, incomplete.party, product["id"])
    response = client.post(_url(application, "/submit"), headers=incomplete.party.headers)
    assert response.status_code == 422
    body = response.json()
    assert body["action"] == "SUBMIT"
    assert len(body["violations"]) == 3  # employment, income, expenses


def test_submit_rejects_mixed_currencies(
    client: TestClient, borrower: Borrower, draft: dict[str, Any]
) -> None:
    client.post(
        f"{APPLICANTS}/{borrower.applicant['id']}/expenses",
        json={"category": "EDUCATION", "monthly_amount": "100.00", "currency": "USD"},
        headers=borrower.party.headers,
    )
    response = client.post(_url(draft, "/submit"), headers=borrower.party.headers)
    assert response.status_code == 422
    assert "loan currency" in response.json()["detail"]


def test_submit_blocks_withdrawn_products(
    client: TestClient,
    borrower: Borrower,
    admin: Party,
    product: dict[str, Any],
    draft: dict[str, Any],
) -> None:
    client.patch(f"{PRODUCTS}/{product['id']}", json={"is_active": False}, headers=admin.headers)
    # The draft can still be edited under its product, but not submitted.
    edit = client.patch(_url(draft), json={"purpose": "EDUCATION"}, headers=borrower.party.headers)
    assert edit.status_code == 200
    response = client.post(_url(draft, "/submit"), headers=borrower.party.headers)
    assert response.status_code == 422
    assert "no longer available" in response.json()["detail"]


def test_submit_snapshots_terms_and_records_history(
    client: TestClient,
    borrower: Borrower,
    admin: Party,
    product: dict[str, Any],
    draft: dict[str, Any],
    db_session: Session,
) -> None:
    response = client.post(_url(draft, "/submit"), headers=borrower.party.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    # Submission assesses affordability and queues the application in one step.
    assert body["status"] == "PENDING_REVIEW"
    assert body["submitted_at"].startswith("2026-11-18T09:00:00")
    snapshot = body["product_terms_snapshot"]
    assert snapshot["annual_interest_rate"] == "0.215000"
    assert snapshot["max_dti"] == "0.4500"
    assert snapshot["code"] == "PERSONAL_UNSECURED"

    # Later product changes do not touch the frozen snapshot.
    client.patch(
        f"{PRODUCTS}/{product['id']}",
        json={"annual_interest_rate": "0.250000"},
        headers=admin.headers,
    )
    again = client.get(_url(draft), headers=borrower.party.headers).json()
    assert again["product_terms_snapshot"]["annual_interest_rate"] == "0.215000"

    assert _history(client, draft, borrower.party) == ["DRAFT", "SUBMITTED", "PENDING_REVIEW"]
    submitted, queued = db_session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == "application.status_changed")
        .order_by(AuditEvent.id)
    ).all()
    assert submitted.changes == {"status": {"from": "DRAFT", "to": "SUBMITTED"}}
    assert submitted.actor_user_id == borrower.party.user.id
    # Queueing is the system's step, not the applicant's.
    assert queued.changes == {"status": {"from": "SUBMITTED", "to": "PENDING_REVIEW"}}
    assert queued.actor_user_id is None

    # Submitted applications can no longer be edited or resubmitted.
    assert (
        client.patch(
            _url(draft), json={"purpose": "OTHER"}, headers=borrower.party.headers
        ).status_code
        == 409
    )
    resubmit = client.post(_url(draft, "/submit"), headers=borrower.party.headers)
    assert resubmit.status_code == 409
    assert resubmit.json()["code"] == "INVALID_TRANSITION"


# --- Ownership -------------------------------------------------------------------------------


def test_applicants_cannot_reach_other_applications(
    client: TestClient,
    make_borrower: Callable[..., Borrower],
    draft: dict[str, Any],
) -> None:
    intruder = make_borrower(first_name="Mallory")
    headers = intruder.party.headers
    requests = [
        ("GET", _url(draft), None),
        ("PATCH", _url(draft), {"purpose": "OTHER"}),
        ("POST", _url(draft, "/submit"), None),
        ("POST", _url(draft, "/withdraw"), None),
        ("GET", _url(draft, "/status-history"), None),
        ("GET", _url(draft, "/information-requests"), None),
        ("GET", _url(draft, "/documents"), None),
        (
            "POST",
            _url(draft, "/documents"),
            {
                "document_type": "PAYSLIP",
                "file_name": "x.pdf",
                "content_type": "application/pdf",
                "size_bytes": 10,
            },
        ),
    ]
    for method, url, body in requests:
        response = client.request(method, url, json=body, headers=headers)
        assert response.status_code == 404, (method, url, response.status_code)
    assert client.get(APPLICATIONS, headers=headers).json()["total"] == 0
    # A random id looks exactly like someone else's.
    assert client.get(f"{APPLICATIONS}/{uuid.uuid4()}", headers=headers).status_code == 404


def test_staff_list_and_filter(
    client: TestClient,
    officer: Party,
    make_borrower: Callable[..., Borrower],
    product: dict[str, Any],
) -> None:
    first, second = make_borrower(), make_borrower(first_name="Sipho")
    a = create_application(client, first.party, product["id"])
    create_application(client, second.party, product["id"])
    client.post(_url(a, "/submit"), headers=first.party.headers)

    assert client.get(APPLICATIONS, headers=officer.headers).json()["total"] == 2
    queued = client.get(f"{APPLICATIONS}?status=PENDING_REVIEW", headers=officer.headers).json()
    assert [item["id"] for item in queued["items"]] == [a["id"]]
    both = client.get(f"{APPLICATIONS}?status=PENDING_REVIEW&status=DRAFT", headers=officer.headers)
    assert both.json()["total"] == 2
    # Each applicant only ever sees their own.
    own = client.get(APPLICATIONS, headers=first.party.headers).json()
    assert [item["id"] for item in own["items"]] == [a["id"]]


# --- Review workflow -------------------------------------------------------------------------


def test_officer_claims_and_starts_review(
    client: TestClient, officer: Party, in_review_queue: dict[str, Any], db_session: Session
) -> None:
    detail = client.get(_url(in_review_queue), headers=officer.headers).json()
    assert detail["available_actions"] == ["START_REVIEW"]

    response = client.post(_url(in_review_queue, "/start-review"), headers=officer.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "UNDER_REVIEW"
    assert body["assigned_officer_id"] == str(officer.user.id)
    actions = db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_id == in_review_queue["id"])
    ).all()
    assert "application.assigned" in actions


def test_review_respects_assignment(
    client: TestClient,
    admin: Party,
    officer: Party,
    make_party: Callable[..., Party],
    analyst: Party,
    in_review_queue: dict[str, Any],
) -> None:
    other = make_party(Role.LOAN_OFFICER)
    assigned = client.post(
        _url(in_review_queue, "/assign"),
        json={"officer_id": str(officer.user.id)},
        headers=admin.headers,
    )
    assert assigned.status_code == 200
    assert assigned.json()["assigned_officer_id"] == str(officer.user.id)

    blocked = client.post(_url(in_review_queue, "/start-review"), headers=other.headers)
    assert blocked.status_code == 422
    assert "another loan officer" in blocked.json()["detail"]
    # Admins can assign but do not review; analysts hold no assign permission.
    assert (
        client.post(_url(in_review_queue, "/start-review"), headers=admin.headers).status_code
        == 403
    )
    assert (
        client.post(_url(in_review_queue, "/start-review"), headers=analyst.headers).status_code
        == 403
    )
    assert (
        client.post(_url(in_review_queue, "/start-review"), headers=officer.headers).status_code
        == 200
    )


def test_assign_only_to_active_loan_officers(
    client: TestClient, admin: Party, analyst: Party, in_review_queue: dict[str, Any]
) -> None:
    for target in (analyst.user.id, uuid.uuid4()):
        response = client.post(
            _url(in_review_queue, "/assign"),
            json={"officer_id": str(target)},
            headers=admin.headers,
        )
        assert response.status_code == 422


def test_assign_requires_a_submitted_application(
    client: TestClient, admin: Party, officer: Party, draft: dict[str, Any]
) -> None:
    response = client.post(
        _url(draft, "/assign"), json={"officer_id": str(officer.user.id)}, headers=admin.headers
    )
    assert response.status_code == 409


def test_information_request_round_trip(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    analyst: Party,
    in_review_queue: dict[str, Any],
) -> None:
    client.post(_url(in_review_queue, "/start-review"), headers=officer.headers)
    created = client.post(
        _url(in_review_queue, "/request-information"),
        json={"message": "Please upload your three most recent payslips."},
        headers=analyst.headers,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    detail = client.get(_url(in_review_queue), headers=borrower.party.headers).json()
    assert detail["status"] == "ADDITIONAL_INFO_REQUESTED"

    # The profile is editable again while information is outstanding.
    edit = client.patch(
        f"{APPLICANTS}/{borrower.applicant['id']}",
        json={"city": "Johannesburg"},
        headers=borrower.party.headers,
    )
    assert edit.status_code == 200

    # Only the owning applicant answers; staff cannot answer on their behalf.
    respond_url = _url(in_review_queue, f"/information-requests/{request_id}/respond")
    assert (
        client.post(
            respond_url, json={"response_text": "Done"}, headers=officer.headers
        ).status_code
        == 403
    )
    answered = client.post(
        respond_url,
        json={"response_text": "Uploaded October to December."},
        headers=borrower.party.headers,
    )
    assert answered.status_code == 200
    assert answered.json()["status"] == "RESPONDED"
    again = client.post(
        respond_url, json={"response_text": "Twice"}, headers=borrower.party.headers
    )
    assert again.status_code == 409

    requests = client.get(
        _url(in_review_queue, "/information-requests"), headers=officer.headers
    ).json()
    assert [r["status"] for r in requests] == ["RESPONDED"]
    assert _history(client, in_review_queue, borrower.party) == [
        "DRAFT",
        "SUBMITTED",
        "PENDING_REVIEW",
        "UNDER_REVIEW",
        "ADDITIONAL_INFO_REQUESTED",
    ]


def test_request_information_needs_review_in_progress(
    client: TestClient, analyst: Party, in_review_queue: dict[str, Any]
) -> None:
    response = client.post(
        _url(in_review_queue, "/request-information"),
        json={"message": "Payslips please"},
        headers=analyst.headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_withdraw_cancels_open_requests(
    client: TestClient, borrower: Borrower, officer: Party, in_review_queue: dict[str, Any]
) -> None:
    client.post(_url(in_review_queue, "/start-review"), headers=officer.headers)
    client.post(
        _url(in_review_queue, "/request-information"),
        json={"message": "Bank statement"},
        headers=officer.headers,
    )
    response = client.post(
        _url(in_review_queue, "/withdraw"),
        json={"reason": "Found a cheaper loan"},
        headers=borrower.party.headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "WITHDRAWN"
    assert response.json()["available_actions"] == []
    requests = client.get(
        _url(in_review_queue, "/information-requests"), headers=borrower.party.headers
    ).json()
    assert [r["status"] for r in requests] == ["CANCELLED"]
    history = client.get(
        _url(in_review_queue, "/status-history"), headers=borrower.party.headers
    ).json()
    assert history[-1]["reason"] == "Found a cheaper loan"
    assert (
        client.post(_url(in_review_queue, "/withdraw"), headers=borrower.party.headers).status_code
        == 409
    )


# --- Notes and documents ---------------------------------------------------------------------


def test_review_notes_are_staff_only(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    analyst: Party,
    in_review_queue: dict[str, Any],
) -> None:
    url = _url(in_review_queue, "/notes")
    officer_note = client.post(
        url, json={"body": "Payslips match declared income."}, headers=officer.headers
    )
    analyst_note = client.post(url, json={"body": "DTI within appetite."}, headers=analyst.headers)
    assert officer_note.json()["note_type"] == "OFFICER_NOTE"
    assert analyst_note.json()["note_type"] == "ANALYST_ASSESSMENT"
    assert len(client.get(url, headers=officer.headers).json()) == 2
    # Applicants can neither read nor write internal notes.
    assert client.get(url, headers=borrower.party.headers).status_code == 403
    assert client.post(url, json={"body": "hi"}, headers=borrower.party.headers).status_code == 403


def test_review_notes_are_append_only(
    client: TestClient, officer: Party, in_review_queue: dict[str, Any], db_session: Session
) -> None:
    client.post(
        _url(in_review_queue, "/notes"), json={"body": "Original note"}, headers=officer.headers
    )
    for statement in ("UPDATE review_notes SET body = 'edited'", "DELETE FROM review_notes"):
        with pytest.raises(DBAPIError, match="append-only"):
            db_session.execute(text(statement))
        db_session.rollback()


def test_status_history_is_append_only(draft: dict[str, Any], db_session: Session) -> None:
    assert db_session.scalars(select(ApplicationStatusHistory)).all()
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text("UPDATE application_status_history SET reason = 'rewritten'"))
    db_session.rollback()


def test_document_references(
    client: TestClient, borrower: Borrower, officer: Party, in_review_queue: dict[str, Any]
) -> None:
    payload = {
        "document_type": "PAYSLIP",
        "file_name": "payslip-oct.pdf",
        "content_type": "application/pdf",
        "size_bytes": 81_234,
    }
    created = client.post(
        _url(in_review_queue, "/documents"), json=payload, headers=borrower.party.headers
    )
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["status"] == "RECEIVED"
    assert document["storage_key"] == f"simulated/{in_review_queue['id']}/{document['id']}"

    # Staff cannot add documents as the applicant; applicants cannot review them.
    assert (
        client.post(
            _url(in_review_queue, "/documents"), json=payload, headers=officer.headers
        ).status_code
        == 403
    )
    review_url = _url(in_review_queue, f"/documents/{document['id']}")
    assert (
        client.patch(
            review_url, json={"status": "ACCEPTED"}, headers=borrower.party.headers
        ).status_code
        == 403
    )

    reviewed = client.patch(review_url, json={"status": "ACCEPTED"}, headers=officer.headers)
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "ACCEPTED"
    assert (
        client.patch(review_url, json={"status": "REJECTED"}, headers=officer.headers).status_code
        == 409
    )
    assert len(client.get(_url(in_review_queue, "/documents"), headers=officer.headers).json()) == 1
