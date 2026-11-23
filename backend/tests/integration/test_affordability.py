"""Affordability assessments, resumed review and repayment schedules through the API."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.applicants.domain.enums import VerificationStatus
from app.modules.applicants.models import IncomeRecord
from app.modules.applications.models import ApplicationStatusHistory
from app.modules.audit.models import AuditEvent
from tests.conftest import (
    APPLICANTS,
    APPLICATIONS,
    PRODUCTS,
    Borrower,
    Party,
    create_application,
)

pytestmark = pytest.mark.integration


def _url(application: dict[str, Any], suffix: str = "") -> str:
    return f"{APPLICATIONS}/{application['id']}{suffix}"


def _assessments(client: TestClient, application: dict[str, Any], party: Party) -> list[Any]:
    response = client.get(_url(application, "/affordability-assessments"), headers=party.headers)
    assert response.status_code == 200, response.text
    return list(response.json())


def _submit(client: TestClient, borrower: Borrower, application: dict[str, Any]) -> Any:
    response = client.post(_url(application, "/submit"), headers=borrower.party.headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def draft(client: TestClient, borrower: Borrower, product: dict[str, Any]) -> dict[str, Any]:
    return create_application(client, borrower.party, product["id"])


@pytest.fixture
def submitted(client: TestClient, borrower: Borrower, draft: dict[str, Any]) -> dict[str, Any]:
    _submit(client, borrower, draft)
    return draft


def _to_information_requested(
    client: TestClient, officer: Party, application: dict[str, Any]
) -> str:
    assert (
        client.post(_url(application, "/start-review"), headers=officer.headers).status_code == 200
    )
    created = client.post(
        _url(application, "/request-information"),
        json={"message": "Please confirm your monthly housing costs."},
        headers=officer.headers,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


# --- Assessment on submission ------------------------------------------------------------


def test_submission_records_assessment_v1_and_queues(
    client: TestClient, borrower: Borrower, submitted: dict[str, Any], db_session: Session
) -> None:
    (assessment,) = _assessments(client, submitted, borrower.party)
    # The standard borrower: 32,000 gross, 24,500 net, 7,500 expenses, no other debt,
    # 30,000.00 over 24 months at 21.5 % with a 69.00 service fee.
    assert assessment["version"] == 1
    assert assessment["run_reason"] == "SUBMISSION"
    assert assessment["created_by"] is None
    assert assessment["calculator_version"] == "affordability-1.0.0"
    assert {
        key: assessment[key]
        for key in (
            "gross_monthly_income",
            "net_monthly_income",
            "declared_living_expenses",
            "expense_norm",
            "applied_living_expenses",
            "existing_debt_instalments",
            "disposable_income",
            "proposed_instalment",
            "residual_income",
            "required_buffer",
            "debt_to_income_ratio",
            "repayment_burden_ratio",
            "new_loan_burden_ratio",
            "outcome",
            "currency",
        )
    } == {
        "gross_monthly_income": "32000.00",
        "net_monthly_income": "24500.00",
        "declared_living_expenses": "7500.00",
        "expense_norm": "4220.00",
        "applied_living_expenses": "7500.00",
        "existing_debt_instalments": "0.00",
        "disposable_income": "17000.00",
        "proposed_instalment": "1617.95",
        "residual_income": "15382.05",
        "required_buffer": "2450.00",
        "debt_to_income_ratio": "0.0506",
        "repayment_burden_ratio": "0.0660",
        "new_loan_burden_ratio": "0.0660",
        "outcome": "AFFORDABLE",
        "currency": "ZAR",
    }
    assert [rule["status"] for rule in assessment["rule_results"]] == ["PASSED"] * 5
    snapshot = assessment["inputs_snapshot"]
    assert snapshot["product_terms"]["annual_interest_rate"] == "0.215000"
    assert snapshot["product_terms"]["max_dti"] == "0.4500"
    assert snapshot["income_records"][0]["gross_amount"] == "32000.00"
    assert snapshot["income_records"][0]["included"] is True
    assert snapshot["expense_norm"]["name"] == "illustrative-2026"
    assert snapshot["amortisation_version"] == "amortisation-1.0.0"

    queued = db_session.scalars(
        select(ApplicationStatusHistory).where(
            ApplicationStatusHistory.to_status == "PENDING_REVIEW"
        )
    ).one()
    assert queued.actor_user_id is None
    assert queued.reason == "Affordability assessment v1: AFFORDABLE"
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "affordability_assessment.created")
    ).one()
    assert event.actor_user_id is None
    assert event.context is not None
    assert event.context["run_reason"] == "SUBMISSION"


def test_unaffordable_applications_are_still_queued_for_a_human(
    client: TestClient, borrower: Borrower, draft: dict[str, Any]
) -> None:
    client.post(
        f"{APPLICANTS}/{borrower.applicant['id']}/expenses",
        json={"category": "OTHER", "monthly_amount": "20000.00"},
        headers=borrower.party.headers,
    )
    assert _submit(client, borrower, draft)["status"] == "PENDING_REVIEW"
    (assessment,) = _assessments(client, draft, borrower.party)
    assert assessment["outcome"] == "UNAFFORDABLE"
    assert assessment["residual_income"].startswith("-")
    failed = [rule["rule_id"] for rule in assessment["rule_results"] if rule["passed"] is False]
    assert failed == ["R1"]


def test_rejected_income_gives_insufficient_data(
    client: TestClient, borrower: Borrower, draft: dict[str, Any], db_session: Session
) -> None:
    # Staff verification has no endpoint yet; set the status the way a reviewer would.
    db_session.execute(update(IncomeRecord).values(verification_status=VerificationStatus.REJECTED))
    db_session.commit()
    _submit(client, borrower, draft)
    (assessment,) = _assessments(client, draft, borrower.party)
    assert assessment["outcome"] == "INSUFFICIENT_DATA"
    assert assessment["debt_to_income_ratio"] is None
    assert assessment["inputs_snapshot"]["income_records"][0]["included"] is False
    assert [rule["status"] for rule in assessment["rule_results"]][1:] == ["NOT_EVALUATED"] * 4


# --- Reading -------------------------------------------------------------------------------


def test_assessments_are_scoped_like_their_application(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    make_borrower: Callable[..., Borrower],
    product: dict[str, Any],
    submitted: dict[str, Any],
) -> None:
    (assessment,) = _assessments(client, submitted, borrower.party)
    detail_url = _url(submitted, f"/affordability-assessments/{assessment['id']}")
    assert client.get(detail_url, headers=borrower.party.headers).json() == assessment
    assert client.get(detail_url, headers=officer.headers).status_code == 200

    stranger = make_borrower()
    assert client.get(detail_url, headers=stranger.party.headers).status_code == 404
    listing = client.get(
        _url(submitted, "/affordability-assessments"), headers=stranger.party.headers
    )
    assert listing.status_code == 404

    # An assessment id only resolves under its own application.
    other = create_application(client, stranger.party, product["id"])
    _submit(client, stranger, other)
    wrong_parent = _url(other, f"/affordability-assessments/{assessment['id']}")
    assert client.get(wrong_parent, headers=officer.headers).status_code == 404
    missing = _url(submitted, f"/affordability-assessments/{uuid.uuid4()}")
    assert client.get(missing, headers=officer.headers).status_code == 404


def test_assessments_are_append_only(submitted: dict[str, Any], db_session: Session) -> None:
    for statement in (
        "UPDATE affordability_assessments SET outcome = 'AFFORDABLE'",
        "DELETE FROM affordability_assessments",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            db_session.execute(text(statement))
        db_session.rollback()


# --- Staff re-runs -------------------------------------------------------------------------


def test_staff_rerun_creates_new_versions(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    analyst: Party,
    admin: Party,
    product: dict[str, Any],
    submitted: dict[str, Any],
) -> None:
    url = _url(submitted, "/affordability-assessments")
    # A later product change does not affect assessments: they use the submitted terms.
    client.patch(
        f"{PRODUCTS}/{product['id']}",
        json={"annual_interest_rate": "0.290000"},
        headers=admin.headers,
    )
    by_officer = client.post(url, headers=officer.headers)
    assert by_officer.status_code == 201, by_officer.text
    assert by_officer.json()["version"] == 2
    assert by_officer.json()["run_reason"] == "STAFF_REQUEST"
    assert by_officer.json()["created_by"] == str(officer.user.id)
    assert by_officer.json()["proposed_instalment"] == "1617.95"
    assert client.post(url, headers=analyst.headers).json()["version"] == 3

    assert client.post(url, headers=admin.headers).status_code == 403
    assert client.post(url, headers=borrower.party.headers).status_code == 403
    assert [a["version"] for a in _assessments(client, submitted, officer)] == [3, 2, 1]


def test_staff_rerun_needs_an_undecided_application_with_the_lender(
    client: TestClient, borrower: Borrower, officer: Party, draft: dict[str, Any]
) -> None:
    url = _url(draft, "/affordability-assessments")
    not_submitted = client.post(url, headers=officer.headers)
    assert not_submitted.status_code == 409
    assert not_submitted.json()["code"] == "INVALID_TRANSITION"

    _submit(client, borrower, draft)
    client.post(_url(draft, "/withdraw"), headers=borrower.party.headers)
    assert client.post(url, headers=officer.headers).status_code == 409


# --- Resuming review after more information ------------------------------------------------


def test_resume_review_reassesses_the_updated_profile(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    submitted: dict[str, Any],
    db_session: Session,
) -> None:
    request_id = _to_information_requested(client, officer, submitted)
    resume_url = _url(submitted, "/resume-review")

    # Not before every request is answered, and nothing is assessed meanwhile.
    early = client.post(resume_url, headers=borrower.party.headers)
    assert early.status_code == 422
    assert early.json()["violations"] == ["all information requests must be answered first"]
    assert len(_assessments(client, submitted, borrower.party)) == 1

    client.post(
        _url(submitted, f"/information-requests/{request_id}/respond"),
        json={"response_text": "Housing went up; I have added the new amount."},
        headers=borrower.party.headers,
    )
    client.post(
        f"{APPLICANTS}/{borrower.applicant['id']}/expenses",
        json={"category": "TRANSPORT", "monthly_amount": "2500.00"},
        headers=borrower.party.headers,
    )
    # Staff cannot resume on the applicant's behalf.
    assert client.post(resume_url, headers=officer.headers).status_code == 403

    resumed = client.post(resume_url, headers=borrower.party.headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "PENDING_REVIEW"

    latest, first = _assessments(client, submitted, borrower.party)
    assert (latest["version"], latest["run_reason"]) == (2, "RESUBMISSION")
    assert latest["declared_living_expenses"] == "10000.00"
    assert first["declared_living_expenses"] == "7500.00"

    history = db_session.scalars(
        select(ApplicationStatusHistory)
        .where(ApplicationStatusHistory.application_id == uuid.UUID(submitted["id"]))
        .order_by(ApplicationStatusHistory.created_at, ApplicationStatusHistory.id)
    ).all()
    assert [row.to_status.value for row in history][-2:] == [
        "ADDITIONAL_INFO_REQUESTED",
        "PENDING_REVIEW",
    ]
    assert history[-1].actor_user_id == borrower.party.user.id
    assert history[-1].reason == "Affordability assessment v2: AFFORDABLE"

    # The profile is locked again, and resuming twice is not a valid move.
    locked = client.post(
        f"{APPLICANTS}/{borrower.applicant['id']}/expenses",
        json={"category": "FOOD", "monthly_amount": "1.00"},
        headers=borrower.party.headers,
    )
    assert locked.json()["code"] == "PROFILE_LOCKED"
    again = client.post(resume_url, headers=borrower.party.headers)
    assert again.status_code == 409
    assert again.json()["code"] == "INVALID_TRANSITION"


@pytest.mark.parametrize(
    ("path", "payload", "fragment"),
    [
        (
            "income",
            {
                "source_type": "RENTAL",
                "frequency": "MONTHLY",
                "gross_amount": "500.00",
                "net_amount": "500.00",
                "currency": "USD",
            },
            "loan currency",
        ),
        ("expenses", None, "expenses"),
    ],
)
def test_resume_review_rechecks_the_profile(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    submitted: dict[str, Any],
    path: str,
    payload: dict[str, str] | None,
    fragment: str,
) -> None:
    request_id = _to_information_requested(client, officer, submitted)
    client.post(
        _url(submitted, f"/information-requests/{request_id}/respond"),
        json={"response_text": "Updated."},
        headers=borrower.party.headers,
    )
    base = f"{APPLICANTS}/{borrower.applicant['id']}/{path}"
    if payload is None:  # remove every record of this kind
        for record in client.get(base, headers=borrower.party.headers).json():
            client.delete(f"{base}/{record['id']}", headers=borrower.party.headers)
    else:
        assert client.post(base, json=payload, headers=borrower.party.headers).status_code == 201

    response = client.post(_url(submitted, "/resume-review"), headers=borrower.party.headers)
    assert response.status_code == 422
    assert any(fragment in v for v in response.json()["violations"])
    assert len(_assessments(client, submitted, borrower.party)) == 1


# --- Repayment schedules -------------------------------------------------------------------


def test_product_schedule_preview(
    client: TestClient, borrower: Borrower, product: dict[str, Any]
) -> None:
    response = client.get(
        f"{PRODUCTS}/{product['id']}/repayment-schedule",
        params={"amount": "30000.00", "term_months": 24},
        headers=borrower.party.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {
        key: body[key]
        for key in (
            "engine_version",
            "currency",
            "principal",
            "level_instalment",
            "first_total_due",
            "total_interest",
            "total_service_fees",
            "total_repayable",
            "cost_of_credit",
            "disbursement_date",
        )
    } == {
        "engine_version": "amortisation-1.0.0",
        "currency": "ZAR",
        "principal": "30000.00",
        "level_instalment": "1548.95",
        "first_total_due": "1617.95",
        "total_interest": "7174.72",
        "total_service_fees": "1656.00",
        "total_repayable": "38830.72",
        "cost_of_credit": "8830.72",
        # 09:00 UTC on 18 November is still 18 November in South Africa.
        "disbursement_date": "2026-11-18",
    }
    rows = body["instalments"]
    assert len(rows) == 24
    assert rows[0]["due_date"] == "2026-12-18"
    assert rows[0]["interest"] == "537.50"
    assert rows[-1]["instalment"] == "1548.87"
    assert rows[-1]["closing_balance"] == "0.00"


def test_product_schedule_preview_validation(
    client: TestClient, borrower: Borrower, admin: Party, product: dict[str, Any]
) -> None:
    url = f"{PRODUCTS}/{product['id']}/repayment-schedule"
    headers = borrower.party.headers
    outside = client.get(url, params={"amount": "999.00", "term_months": 72}, headers=headers)
    assert outside.status_code == 422
    assert len(outside.json()["violations"]) == 2
    fractional = client.get(url, params={"amount": "1000.005", "term_months": 12}, headers=headers)
    assert fractional.status_code == 422
    assert client.get(url, params={"amount": "30000.00", "term_months": 24}).status_code == 401

    dated = client.get(
        url,
        params={"amount": "30000.00", "term_months": 6, "disbursement_date": "2027-01-31"},
        headers=headers,
    ).json()
    assert [row["due_date"] for row in dated["instalments"][:2]] == ["2027-02-28", "2027-03-31"]

    # Withdrawn products: hidden from applicants, still readable by staff.
    client.patch(f"{PRODUCTS}/{product['id']}", json={"is_active": False}, headers=admin.headers)
    params = {"amount": "30000.00", "term_months": 24}
    assert client.get(url, params=params, headers=headers).status_code == 404
    assert client.get(url, params=params, headers=admin.headers).status_code == 200


def test_application_schedule_uses_the_frozen_terms(
    client: TestClient,
    borrower: Borrower,
    officer: Party,
    admin: Party,
    make_borrower: Callable[..., Borrower],
    product: dict[str, Any],
    draft: dict[str, Any],
) -> None:
    url = _url(draft, "/repayment-schedule")
    client.patch(
        f"{PRODUCTS}/{product['id']}",
        json={"annual_interest_rate": "0.120000"},
        headers=admin.headers,
    )
    # A draft follows the product's current terms…
    assert client.get(url, headers=borrower.party.headers).json()["annual_interest_rate"] == (
        "0.120000"
    )
    _submit(client, borrower, draft)
    # …a submitted application keeps the terms frozen at submission.
    client.patch(
        f"{PRODUCTS}/{product['id']}",
        json={"annual_interest_rate": "0.290000"},
        headers=admin.headers,
    )
    frozen = client.get(url, headers=officer.headers).json()
    assert frozen["annual_interest_rate"] == "0.120000"
    assert frozen["term_months"] == 24
    assert frozen["amount"] == "30000.00"
    stranger = make_borrower()
    assert client.get(url, headers=stranger.party.headers).status_code == 404
