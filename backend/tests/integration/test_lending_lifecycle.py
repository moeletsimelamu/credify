"""Phase 5 exit criterion: applicant → submission → review → decision → loan → payments →
arrears → settlement, through the public API with a controllable clock."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import FixedClock
from app.modules.audit.models import AuditEvent
from app.modules.loans import service as loans
from app.modules.loans.models import LoanAccount
from tests.conftest import (
    APPLICATIONS,
    Borrower,
    Party,
    bearer,
    create_application,
    login,
)

pytestmark = pytest.mark.integration

LOANS = "/api/v1/loan-accounts"
RATIONALE = "Affordable with a comfortable buffer; stable permanent employment."


def _url(application: dict[str, Any], suffix: str = "") -> str:
    return f"{APPLICATIONS}/{application['id']}{suffix}"


def _move_to(clock: FixedClock, day: date) -> None:
    clock.set(datetime.combine(day, time(9, 0), tzinfo=UTC))


def _fresh(client: TestClient, party: Party) -> dict[str, str]:
    """Access tokens expire after 15 minutes of clock time, so log in again after a jump."""
    return bearer(login(client, party.user.email))


def _latest_assessment(client: TestClient, application: dict[str, Any], party: Party) -> Any:
    response = client.get(_url(application, "/affordability-assessments"), headers=party.headers)
    assert response.status_code == 200, response.text
    return response.json()[0]


def _under_review(
    client: TestClient, borrower: Borrower, product: dict[str, Any], officer: Party, **request: Any
) -> dict[str, Any]:
    application = create_application(client, borrower.party, product["id"], **request)
    assert client.post(_url(application, "/submit"), headers=borrower.party.headers).is_success
    assert client.post(_url(application, "/start-review"), headers=officer.headers).is_success
    return application


def _recommend(
    client: TestClient,
    application: dict[str, Any],
    officer: Party,
    recommendation: str = "APPROVE",
    *,
    assessment_id: str | None = None,
) -> Any:
    assessment_id = assessment_id or _latest_assessment(client, application, officer)["id"]
    return client.post(
        _url(application, "/recommendation"),
        json={
            "recommendation": recommendation,
            "rationale": RATIONALE,
            "affordability_assessment_id": assessment_id,
        },
        headers=officer.headers,
    )


def _decide(
    client: TestClient, application: dict[str, Any], decider: Party, **overrides: Any
) -> Any:
    payload: dict[str, Any] = {
        "decision": "APPROVED",
        "reason_codes": ["MEETS_CRITERIA", "AFFORDABLE_WITH_BUFFER"],
        "explanation": "Residual income well above the buffer; no adverse indicators.",
        "affordability_assessment_id": _latest_assessment(client, application, decider)["id"],
        "model_output_acknowledged": True,
    }
    payload.update(overrides)
    return client.post(_url(application, "/decision"), json=payload, headers=decider.headers)


@pytest.fixture
def recommended(
    client: TestClient, borrower: Borrower, product: dict[str, Any], officer: Party
) -> dict[str, Any]:
    application = _under_review(client, borrower, product, officer)
    response = _recommend(client, application, officer)
    assert response.status_code == 201, response.text
    return application


@pytest.fixture
def approved(client: TestClient, recommended: dict[str, Any], analyst: Party) -> dict[str, Any]:
    response = _decide(client, recommended, analyst)
    assert response.status_code == 201, response.text
    return recommended


@pytest.fixture
def loan(client: TestClient, approved: dict[str, Any], officer: Party) -> dict[str, Any]:
    response = client.post(_url(approved, "/disburse"), headers=officer.headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _pay(
    client: TestClient,
    loan: dict[str, Any],
    headers: dict[str, str],
    amount: str,
    *,
    key: str | None = None,
    value_date: str | None = None,
) -> Any:
    body: dict[str, Any] = {"amount": amount}
    if value_date:
        body["value_date"] = value_date
    return client.post(
        f"{LOANS}/{loan['id']}/repayments",
        json=body,
        headers={**headers, "Idempotency-Key": key or f"test-{uuid.uuid4()}"},
    )


def _schedule(client: TestClient, loan: dict[str, Any], headers: dict[str, str]) -> Any:
    response = client.get(f"{LOANS}/{loan['id']}/schedule", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- The full lifecycle ---------------------------------------------------------------------


def test_end_to_end_lifecycle(
    client: TestClient,
    clock: FixedClock,
    borrower: Borrower,
    product: dict[str, Any],
    officer: Party,
    analyst: Party,
    admin: Party,
    db_session: Session,
) -> None:
    # Applicant → submission → review → recommendation → decision.
    application = _under_review(client, borrower, product, officer)
    assert _recommend(client, application, officer).status_code == 201
    assert client.get(_url(application), headers=officer.headers).json()["status"] == (
        "RECOMMENDED_APPROVAL"
    )
    decision = _decide(client, application, analyst)
    assert decision.status_code == 201, decision.text
    body = decision.json()
    assert body["decision"] == "APPROVED"
    assert body["approved_amount"] == "30000.00"
    assert body["approved_term_months"] == 24
    assert body["departs_from_recommendation"] is False
    assert body["decided_by"] == str(analyst.user.id)

    # The applicant sees the outcome and plain-language reasons, not the internal note.
    mine = client.get(_url(application, "/decision"), headers=borrower.party.headers).json()
    assert mine["decision"] == "APPROVED"
    assert [r["code"] for r in mine["reasons"]] == ["MEETS_CRITERIA", "AFFORDABLE_WITH_BUFFER"]
    assert "explanation" not in mine
    assert "decided_by" not in mine

    # Simulated disbursement on 2026-11-18 (Example B: 30,000.00 at 21.5 % over 24 months).
    opened = client.post(_url(application, "/disburse"), headers=officer.headers)
    assert opened.status_code == 201, opened.text
    loan = opened.json()
    assert loan["account_number"].startswith("LA-")
    assert loan["status"] == "ACTIVE"
    assert loan["principal"] == "30000.00"
    assert loan["instalment_amount"] == "1548.95"
    assert loan["first_due_date"] == "2026-12-18"
    assert loan["maturity_date"] == "2028-11-18"
    again = client.post(_url(application, "/disburse"), headers=officer.headers)
    assert again.status_code == 200
    assert again.json()["id"] == loan["id"]

    schedule = _schedule(client, loan, borrower.party.headers)
    rows = schedule["instalments"]
    assert len(rows) == 24
    assert rows[0]["interest_due"] == "537.50"
    assert rows[0]["total_due"] == "1617.95"
    assert rows[-1]["principal_due"] == rows[-1]["opening_balance"]

    # Instalment 1 paid on time by the applicant.
    _move_to(clock, date(2026, 12, 18))
    paid = _pay(client, loan, _fresh(client, borrower.party), "1617.95")
    assert paid.status_code == 201, paid.text
    assert paid.json()["account"]["status"] == "ACTIVE"
    allocations = {a["component"]: a["amount"] for a in paid.json()["transaction"]["allocations"]}
    assert allocations == {"SERVICE_FEE": "69.00", "INTEREST": "537.50", "PRINCIPAL": "1011.45"}

    # Instalment 2 is missed: after the 5-day grace period the job charges a late fee.
    run = loans.run_arrears(
        db_session, as_of=date(2027, 1, 25), now=datetime(2027, 1, 25, 1, 0, tzinfo=UTC)
    )
    assert run.evaluated == 1
    assert run.late_fees_charged == 1
    assert run.discrepancies == {}
    _move_to(clock, date(2027, 1, 25))
    staff = _fresh(client, officer)
    account = client.get(f"{LOANS}/{loan['id']}", headers=staff).json()
    assert account["status"] == "IN_ARREARS"
    assert account["days_past_due"] == 7
    assert account["arrears_bucket"] == "1-30"
    assert account["arrears_amount"] == "1717.95"
    charges = client.get(f"{LOANS}/{loan['id']}/charges", headers=staff).json()
    assert [(c["charge_type"], c["amount"], c["charged_on"]) for c in charges] == [
        ("LATE_FEE", "100.00", "2027-01-24")
    ]
    # Running the job again for the same date changes nothing.
    rerun = loans.run_arrears(
        db_session, as_of=date(2027, 1, 25), now=datetime(2027, 1, 25, 2, 0, tzinfo=UTC)
    )
    assert (rerun.late_fees_charged, rerun.status_changes) == (0, 0)

    # The officer records the catch-up payment: late fee first, then instalment 2.
    caught_up = _pay(client, loan, staff, "1717.95")
    assert caught_up.status_code == 201, caught_up.text
    assert caught_up.json()["account"]["status"] == "ACTIVE"
    assert caught_up.json()["account"]["days_past_due"] == 0
    assert caught_up.json()["transaction"]["allocations"][0]["component"] == "CHARGE"

    # Instalment 3 on time, then settle 17 days later (FINANCIAL_CALCULATIONS.md §7).
    _move_to(clock, date(2027, 2, 18))
    assert _pay(client, loan, _fresh(client, borrower.party), "1617.95").status_code == 201
    _move_to(clock, date(2027, 3, 7))
    owner = _fresh(client, borrower.party)
    quote = client.get(f"{LOANS}/{loan['id']}/settlement-quote", headers=owner).json()
    assert quote["outstanding_principal"] == "26910.96"
    assert quote["accrual_start"] == "2027-02-18"
    assert quote["accrued_days"] == 17
    assert quote["accrued_interest"] == "269.48"
    assert quote["settlement_fee"] == "0.00"
    assert quote["total"] == "27180.44"

    wrong = client.post(
        f"{LOANS}/{loan['id']}/settlement",
        json={"amount": "27000.00"},
        headers={**owner, "Idempotency-Key": "settle-wrong-amount"},
    )
    assert wrong.status_code == 422
    assert wrong.json()["settlement_amount"] == "27180.44"
    settled = client.post(
        f"{LOANS}/{loan['id']}/settlement",
        json={"amount": "27180.44"},
        headers={**owner, "Idempotency-Key": "settle-loan-0001"},
    )
    assert settled.status_code == 201, settled.text
    final = settled.json()["account"]
    assert final["status"] == "SETTLED"
    assert final["settled_on"] == "2027-03-07"
    assert final["outstanding_principal"] == "0.00"

    statuses = [row["status"] for row in _schedule(client, loan, owner)["instalments"]]
    assert statuses == ["PAID"] * 3 + ["CANCELLED"] * 21
    statement = client.get(f"{LOANS}/{loan['id']}/transactions", headers=owner).json()
    assert [t["transaction_type"] for t in statement] == [
        "DISBURSEMENT",
        "REPAYMENT",
        "CHARGE",
        "REPAYMENT",
        "REPAYMENT",
        "SETTLEMENT",
    ]
    assert statement[-1]["balance_after"] == "0.00"
    # Once settled, nothing more can be paid.
    assert _pay(client, loan, owner, "100.00").status_code == 409

    account_row = db_session.get(LoanAccount, uuid.UUID(loan["id"]))
    assert account_row is not None
    db_session.refresh(account_row)
    assert loans.reconcile(db_session, account_row) == []

    # Portfolio figures for the period.
    portfolio = client.get(
        "/api/v1/analytics/portfolio?date_from=2026-11-01&date_to=2027-03-07",
        headers=_fresh(client, admin),
    )
    assert portfolio.status_code == 200, portfolio.text
    (zar,) = portfolio.json()["currencies"]
    assert zar["decisions_approved"] == 1
    assert zar["approval_rate"] == "1.0000"
    assert zar["loans_disbursed"] == 1
    assert zar["principal_disbursed"] == "30000.00"
    assert zar["loans_by_status"] == {"SETTLED": 1}
    assert zar["outstanding_principal"] == "0.00"
    # Instalments due Dec–Feb (3 × 1,617.95) were collected in full.
    assert zar["amount_due"] == "4853.85"
    assert zar["collection_rate"] == "1.0000"

    actions = set(db_session.scalars(select(AuditEvent.action)).all())
    assert {
        "recommendation.recorded",
        "decision.recorded",
        "loan_account.opened",
        "loan_transaction.recorded",
        "loan_charge.created",
        "loan_account.status_changed",
        "arrears_job.completed",
    } <= actions


# --- Recommendation and decision rules -------------------------------------------------


def test_recommendation_must_cite_the_latest_assessment(
    client: TestClient, borrower: Borrower, product: dict[str, Any], officer: Party
) -> None:
    application = _under_review(client, borrower, product, officer)
    v1 = _latest_assessment(client, application, officer)["id"]
    rerun = client.post(_url(application, "/affordability-assessments"), headers=officer.headers)
    assert rerun.status_code == 201
    stale = _recommend(client, application, officer, assessment_id=v1)
    assert stale.status_code == 422
    assert "latest affordability assessment (version 2)" in stale.json()["detail"]
    unknown = _recommend(client, application, officer, assessment_id=str(uuid.uuid4()))
    assert unknown.status_code == 422
    short = client.post(
        _url(application, "/recommendation"),
        json={
            "recommendation": "REJECT",
            "rationale": "Too short",
            "affordability_assessment_id": rerun.json()["id"],
        },
        headers=officer.headers,
    )
    assert short.status_code == 422
    assert "at least 20 characters" in short.json()["detail"]


def test_only_loan_officers_recommend_and_only_from_review(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    officer: Party,
    analyst: Party,
) -> None:
    application = create_application(client, borrower.party, product["id"])
    assert client.post(_url(application, "/submit"), headers=borrower.party.headers).is_success
    assert _recommend(client, application, analyst).status_code == 403
    assert _recommend(client, application, officer).status_code == 409  # PENDING_REVIEW


def test_reruns_are_blocked_while_a_recommendation_waits(
    client: TestClient, recommended: dict[str, Any], officer: Party, analyst: Party
) -> None:
    blocked = client.post(_url(recommended, "/affordability-assessments"), headers=officer.headers)
    assert blocked.status_code == 409
    # Sending it back reopens review: a re-run is allowed and the next recommendation
    # must cite it.
    sent = client.post(
        _url(recommended, "/send-back"),
        json={"reason": "Please re-check the housing costs."},
        headers=analyst.headers,
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "UNDER_REVIEW"
    rerun = client.post(_url(recommended, "/affordability-assessments"), headers=officer.headers)
    assert rerun.status_code == 201
    assert _recommend(client, recommended, officer, "REJECT").status_code == 201
    history = client.get(_url(recommended, "/recommendations"), headers=analyst.headers).json()
    assert [r["recommendation"] for r in history] == ["APPROVE", "REJECT"]


def test_send_back_needs_a_decider_and_a_reason(
    client: TestClient, recommended: dict[str, Any], officer: Party, admin: Party
) -> None:
    url = _url(recommended, "/send-back")
    assert client.post(url, json={"reason": "x"}, headers=officer.headers).status_code == 403
    assert client.post(url, json={"reason": " "}, headers=admin.headers).status_code == 422


def test_decision_guards(
    client: TestClient, recommended: dict[str, Any], analyst: Party, officer: Party
) -> None:
    assert _decide(client, recommended, officer).status_code == 403
    wrong_code = _decide(client, recommended, analyst, reason_codes=["HIGH_DTI"])
    assert wrong_code.status_code == 422
    assert "HIGH_DTI cannot support a decision to approve" in wrong_code.json()["detail"]
    unknown = _decide(client, recommended, analyst, reason_codes=["NOT_A_CODE"])
    assert "NOT_A_CODE is not a recognised reason code" in unknown.json()["detail"]
    none = _decide(client, recommended, analyst, reason_codes=[])
    assert "at least one reason code" in none.json()["detail"]
    unacknowledged = _decide(client, recommended, analyst, model_output_acknowledged=False)
    assert "advisory" in unacknowledged.json()["detail"]
    blank = _decide(client, recommended, analyst, explanation=" ")
    assert blank.status_code == 422
    stale = _decide(client, recommended, analyst, affordability_assessment_id=str(uuid.uuid4()))
    assert "latest affordability assessment" in stale.json()["detail"]
    terms_on_decline = _decide(
        client,
        recommended,
        analyst,
        decision="DECLINED",
        reason_codes=["HIGH_DTI"],
        approved_amount="20000.00",
    )
    assert "only an approval" in terms_on_decline.json()["detail"]
    # Nothing was recorded by the failed attempts.
    assert client.get(_url(recommended, "/decision"), headers=analyst.headers).status_code == 404


def test_maker_checker_survives_a_role_change(
    client: TestClient,
    recommended: dict[str, Any],
    officer: Party,
    admin: Party,
) -> None:
    # The recommending officer is promoted to credit analyst and tries to decide their own
    # recommendation: the state machine refuses (approved decision D2).
    promoted = client.patch(
        f"/api/v1/users/{officer.user.id}",
        json={"role": "CREDIT_ANALYST"},
        headers=admin.headers,
    )
    assert promoted.status_code == 200, promoted.text
    response = _decide(client, recommended, officer)
    assert response.status_code == 422
    assert "someone other than the recommender" in response.json()["detail"]


def test_decline_against_the_recommendation(
    client: TestClient, recommended: dict[str, Any], admin: Party, borrower: Borrower
) -> None:
    response = _decide(
        client,
        recommended,
        admin,
        decision="DECLINED",
        reason_codes=["INSUFFICIENT_INCOME_EVIDENCE"],
        explanation="Payslips do not match the declared income.",
    )
    assert response.status_code == 201, response.text
    assert response.json()["departs_from_recommendation"] is True
    assert response.json()["approved_amount"] is None
    detail = client.get(_url(recommended), headers=borrower.party.headers).json()
    assert detail["status"] == "DECLINED"
    assert detail["decided_at"] is not None
    # Declined applications cannot be disbursed, and the applicant may apply again.
    disburse = client.post(_url(recommended, "/disburse"), headers=admin.headers)
    assert disburse.status_code == 409
    again = client.post(
        APPLICATIONS,
        json={
            "loan_product_id": detail["loan_product_id"],
            "requested_amount": "10000.00",
            "requested_term_months": 12,
            "purpose": "OTHER",
        },
        headers=borrower.party.headers,
    )
    assert again.status_code == 201


def test_counter_offer(
    client: TestClient, recommended: dict[str, Any], analyst: Party, officer: Party
) -> None:
    larger = _decide(
        client, recommended, analyst, approved_amount="40000.00", reason_codes=["COUNTER_OFFER"]
    )
    assert "cannot exceed the requested amount" in larger.json()["detail"]
    uncoded = _decide(client, recommended, analyst, approved_amount="20000.00")
    assert "COUNTER_OFFER reason code" in uncoded.json()["detail"]
    # 30,000 over 12 months would cost far more per month than the assessed 1,617.95.
    dearer = _decide(
        client, recommended, analyst, approved_term_months=12, reason_codes=["COUNTER_OFFER"]
    )
    assert "may not cost more per month" in dearer.json()["detail"]
    offer = _decide(
        client, recommended, analyst, approved_amount="20000.00", reason_codes=["COUNTER_OFFER"]
    )
    assert offer.status_code == 201, offer.text
    assert offer.json()["approved_amount"] == "20000.00"
    loan = client.post(_url(recommended, "/disburse"), headers=officer.headers).json()
    assert loan["approved_amount"] == "20000.00"
    assert loan["principal"] == "20000.00"
    assert loan["term_months"] == 24


def test_disbursement_permissions(
    client: TestClient, approved: dict[str, Any], analyst: Party, borrower: Borrower, admin: Party
) -> None:
    assert client.post(_url(approved, "/disburse"), headers=analyst.headers).status_code == 403
    assert (
        client.post(_url(approved, "/disburse"), headers=borrower.party.headers).status_code == 403
    )
    assert client.get(_url(approved, "/loan-account"), headers=admin.headers).status_code == 404
    assert client.post(_url(approved, "/disburse"), headers=admin.headers).status_code == 201
    assert client.get(_url(approved, "/loan-account"), headers=admin.headers).status_code == 200


def test_reason_code_catalogue(client: TestClient, borrower: Borrower) -> None:
    response = client.get("/api/v1/decision-reason-codes", headers=borrower.party.headers)
    assert response.status_code == 200
    codes = {item["code"]: item["outcome"] for item in response.json()}
    assert codes["MEETS_CRITERIA"] == "APPROVED"
    assert codes["HIGH_DTI"] == "DECLINED"


# --- Repayments ------------------------------------------------------------------------


def test_idempotent_repayments(
    client: TestClient, clock: FixedClock, loan: dict[str, Any], officer: Party
) -> None:
    _move_to(clock, date(2026, 12, 18))
    headers = _fresh(client, officer)
    first = _pay(client, loan, headers, "1617.95", key="pay-dec-0001")
    assert first.status_code == 201
    replay = _pay(client, loan, headers, "1617.95", key="pay-dec-0001")
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["replayed"] is True
    assert replay.json()["transaction"]["id"] == first.json()["transaction"]["id"]
    reused = _pay(client, loan, headers, "100.00", key="pay-dec-0001")
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    statement = client.get(f"{LOANS}/{loan['id']}/transactions", headers=headers).json()
    assert [t["transaction_type"] for t in statement] == ["DISBURSEMENT", "REPAYMENT"]
    missing = client.post(
        f"{LOANS}/{loan['id']}/repayments", json={"amount": "1.00"}, headers=headers
    )
    assert missing.status_code == 422


def test_repayment_value_dates_and_amounts(
    client: TestClient, clock: FixedClock, loan: dict[str, Any], officer: Party
) -> None:
    _move_to(clock, date(2026, 12, 18))
    headers = _fresh(client, officer)
    future = _pay(client, loan, headers, "100.00", value_date="2026-12-19")
    assert "future" in future.json()["detail"]
    early = _pay(client, loan, headers, "100.00", value_date="2026-11-01")
    assert "cannot be before 2026-11-18" in early.json()["detail"]
    floats = client.post(
        f"{LOANS}/{loan['id']}/repayments",
        json={"amount": 100.5},
        headers={**headers, "Idempotency-Key": "float-amount-01"},
    )
    assert floats.status_code == 422
    whole = _pay(client, loan, headers, "40000.00")
    assert whole.status_code == 422
    assert "settlement quote" in whole.json()["detail"]
    # A partial payment of instalment 1 on its due date.
    partial = _pay(client, loan, headers, "500.00")
    assert partial.status_code == 201
    rows = _schedule(client, loan, headers)["instalments"]
    assert rows[0]["status"] == "PARTIALLY_PAID"
    assert (rows[0]["service_fee_paid"], rows[0]["interest_paid"]) == ("69.00", "431.00")


def test_prepayment_recasts_with_the_same_instalment(
    client: TestClient, clock: FixedClock, loan: dict[str, Any], officer: Party, db_session: Session
) -> None:
    _move_to(clock, date(2026, 12, 18))
    headers = _fresh(client, officer)
    paid = _pay(client, loan, headers, "6617.95")  # instalment 1 + 5,000.00 extra
    assert paid.status_code == 201, paid.text
    allocations = paid.json()["transaction"]["allocations"]
    assert allocations[-1] == {
        "component": "PREPAYMENT",
        "amount": "5000.00",
        "schedule_instalment_id": None,
        "loan_charge_id": None,
    }
    account = paid.json()["account"]
    assert account["outstanding_principal"] == "23988.55"  # 28,988.55 − 5,000.00
    schedule = _schedule(client, loan, headers)
    assert schedule["current_version"] == 2
    rows = schedule["instalments"]
    assert rows[0]["schedule_version"] == 1
    assert rows[0]["status"] == "PAID"
    assert rows[1]["schedule_version"] == 2
    assert rows[1]["opening_balance"] == "23988.55"
    assert all(row["principal_due"] for row in rows)
    # Same instalment, fewer of them.
    levels = {Decimal(r["interest_due"]) + Decimal(r["principal_due"]) for r in rows[1:-1]}
    assert levels == {Decimal("1548.95")}
    assert len(rows) < 24
    assert account["maturity_date"] == rows[-1]["due_date"]
    versions = client.get(f"{LOANS}/{loan['id']}/schedules", headers=headers).json()
    assert [(v["version"], v["reason"], v["is_current"]) for v in versions] == [
        (1, "ORIGINATION", False),
        (2, "PREPAYMENT_RECAST", True),
    ]
    v1 = client.get(f"{LOANS}/{loan['id']}/schedules/1", headers=headers).json()
    assert len(v1["instalments"]) == 24
    account_row = db_session.get(LoanAccount, uuid.UUID(loan["id"]))
    assert account_row is not None
    assert loans.reconcile(db_session, account_row) == []

    # Long after maturity, the portfolio counts only the effective schedule: the version-1
    # rows the recast replaced (including those beyond the new, shorter end) are excluded.
    _move_to(clock, date(2029, 1, 1))
    portfolio = client.get(
        "/api/v1/analytics/portfolio?date_from=2026-11-01&date_to=2028-12-31",
        headers=_fresh(client, officer),
    ).json()
    (zar,) = portfolio["currencies"]
    assert zar["amount_due"] == str(sum(Decimal(row["total_due"]) for row in rows))
    assert zar["amount_collected"] == "1617.95"  # the prepayment is principal, not an instalment


def test_arrears_ageing_and_bucket(
    client: TestClient, loan: dict[str, Any], officer: Party, db_session: Session
) -> None:
    # Nothing paid: by 2027-02-20 instalments 1–3 are overdue; 1 and 2 are past their grace
    # period and carry a late fee each (3's fee becomes chargeable on 2027-02-24).
    run = loans.run_arrears(
        db_session, as_of=date(2027, 2, 20), now=datetime(2027, 2, 20, tzinfo=UTC)
    )
    assert run.late_fees_charged == 2
    account = client.get(f"{LOANS}/{loan['id']}", headers=officer.headers)
    # The officer's token is still valid: the app clock did not move.
    body = account.json()
    assert body["days_past_due"] == 64  # since 2026-12-18
    assert body["arrears_bucket"] == "61-90"
    assert body["arrears_amount"] == str(Decimal("1617.95") * 3 + Decimal("200.00"))
    in_arrears = client.get(f"{LOANS}?status=IN_ARREARS", headers=officer.headers).json()
    assert in_arrears["total"] == 1
    # A repayment cannot be back-dated before the account was last brought up to date.
    early = _pay(client, loan, officer.headers, "100.00", value_date="2027-02-01")
    assert early.status_code == 422


def test_loan_scoping_and_permissions(
    client: TestClient,
    clock: FixedClock,
    loan: dict[str, Any],
    borrower: Borrower,
    analyst: Party,
    make_borrower: Callable[..., Borrower],
) -> None:
    stranger = make_borrower()
    for path in ("", "/schedule", "/transactions", "/charges", "/settlement-quote"):
        response = client.get(f"{LOANS}/{loan['id']}{path}", headers=stranger.party.headers)
        assert response.status_code == 404, path
    assert client.get(LOANS, headers=stranger.party.headers).json()["total"] == 0
    assert client.get(LOANS, headers=borrower.party.headers).json()["total"] == 1
    assert _pay(client, loan, stranger.party.headers, "100.00").status_code == 404
    # Credit analysts can read loans but not record payments.
    assert client.get(f"{LOANS}/{loan['id']}", headers=analyst.headers).status_code == 200
    assert _pay(client, loan, analyst.headers, "100.00").status_code == 403
    assert (
        client.get("/api/v1/analytics/portfolio", headers=borrower.party.headers).status_code == 403
    )


def test_settlement_quote_dates(client: TestClient, loan: dict[str, Any], officer: Party) -> None:
    url = f"{LOANS}/{loan['id']}/settlement-quote"
    today = client.get(url, headers=officer.headers).json()
    # On the disbursement date nothing has accrued and no fee applies on this product.
    assert (today["accrued_days"], today["total"]) == (0, "30000.00")
    assert client.get(f"{url}?as_of=2026-11-01", headers=officer.headers).status_code == 422
    assert client.get(f"{url}?as_of=2028-12-31", headers=officer.headers).status_code == 422
    # Projected: instalment 1 unpaid past its grace period by 2026-12-30.
    later = client.get(f"{url}?as_of=2026-12-30", headers=officer.headers).json()
    assert later["arrears_principal"] == "1011.45"
    assert later["projected_late_fees"] == "100.00"


def test_settlement_fee_and_replay(
    client: TestClient,
    clock: FixedClock,
    borrower: Borrower,
    admin: Party,
    officer: Party,
    analyst: Party,
) -> None:
    product = client.post(
        "/api/v1/products",
        json={
            "code": "FEE_PRODUCT",
            "name": "Product with fees",
            "currency": "ZAR",
            "min_amount": "1000.00",
            "max_amount": "50000.00",
            "min_term_months": 3,
            "max_term_months": 36,
            "annual_interest_rate": "0.180000",
            "initiation_fee": "500.00",
            "monthly_service_fee": "50.00",
            "late_fee": "0.00",
            "grace_period_days": 0,
            "early_settlement_fee_rate": "0.010000",
            "max_dti": "0.5000",
            "warn_dti": "0.4000",
            "min_residual_amount": "1000.00",
            "min_residual_rate": "0.1000",
        },
        headers=admin.headers,
    ).json()
    application = _under_review(
        client, borrower, product, officer, requested_amount="10000.00", requested_term_months=12
    )
    assert _recommend(client, application, officer).status_code == 201
    assert _decide(client, application, analyst).status_code == 201
    loan = client.post(_url(application, "/disburse"), headers=officer.headers).json()
    assert loan["principal"] == "10500.00"  # initiation fee capitalised
    _move_to(clock, date(2026, 12, 3))
    headers = _fresh(client, officer)
    quote = client.get(f"{LOANS}/{loan['id']}/settlement-quote", headers=headers).json()
    assert quote["settlement_fee"] == "105.00"  # 1 % of 10,500.00
    assert quote["accrued_days"] == 15
    assert quote["accrued_interest"] == "77.67"  # q(10,500 × 0.18 × 15 / 365)
    body = {"amount": quote["total"]}
    first = client.post(
        f"{LOANS}/{loan['id']}/settlement",
        json=body,
        headers={**headers, "Idempotency-Key": "settle-fee-0001"},
    )
    assert first.status_code == 201, first.text
    replay = client.post(
        f"{LOANS}/{loan['id']}/settlement",
        json=body,
        headers={**headers, "Idempotency-Key": "settle-fee-0001"},
    )
    assert replay.status_code == 200
    statement = client.get(f"{LOANS}/{loan['id']}/transactions", headers=headers).json()
    assert [t["transaction_type"] for t in statement] == ["DISBURSEMENT", "CHARGE", "SETTLEMENT"]
    charges = client.get(f"{LOANS}/{loan['id']}/charges", headers=headers).json()
    assert [(c["charge_type"], c["amount"], c["amount_paid"]) for c in charges] == [
        ("EARLY_SETTLEMENT_FEE", "105.00", "105.00")
    ]


def test_financial_history_is_append_only(
    loan: dict[str, Any], approved: dict[str, Any], db_session: Session
) -> None:
    for table in ("loan_transactions", "lending_decisions", "recommendations"):
        with pytest.raises(DBAPIError):
            db_session.execute(text(f"UPDATE {table} SET created_at = now()"))  # noqa: S608
        db_session.rollback()
