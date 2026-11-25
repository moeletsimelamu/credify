"""Credit-risk scoring through the API: registry, verified loading, scoring at submission,
explanations, failure handling, citation in recommendations and decisions (Phase 7)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.applicants.models import Applicant
from app.modules.credit_risk import explain, registry
from app.modules.credit_risk import features as shared
from app.modules.credit_risk.domain import bureau
from app.modules.credit_risk.models import ModelVersion
from tests import model_fixture
from tests.conftest import APPLICATIONS, Borrower, Party, create_application, make_settings

pytestmark = pytest.mark.integration

MODELS = "/api/v1/models"


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "models"
    folder.mkdir()
    return folder


@pytest.fixture
def settings(model_dir: Path) -> Settings:  # overrides the conftest fixture
    return make_settings(model_dir=model_dir)


@pytest.fixture(autouse=True)
def _fresh_model_cache() -> Iterator[None]:
    registry.clear_cache()
    yield
    registry.clear_cache()


@pytest.fixture(scope="module")
def packaged() -> model_fixture.PackagedModel:
    return model_fixture.train()


@pytest.fixture
def register(
    db_session: Session, model_dir: Path, packaged: model_fixture.PackagedModel
) -> Callable[..., ModelVersion]:
    def factory(model: model_fixture.PackagedModel | None = None) -> ModelVersion:
        model = model or packaged
        uri = model_fixture.install(model, model_dir)
        version = registry.register(
            db_session, model.metadata, artefact_uri=uri, now=datetime.now(UTC)
        )
        db_session.commit()
        return version

    return factory


@pytest.fixture
def active_model(
    client: TestClient, admin: Party, register: Callable[..., ModelVersion]
) -> ModelVersion:
    version = register()
    response = client.post(f"{MODELS}/{version.id}/activate", headers=admin.headers)
    assert response.status_code == 200, response.text
    return version


def _url(application: dict[str, Any], suffix: str = "") -> str:
    return f"{APPLICATIONS}/{application['id']}{suffix}"


def _submit(client: TestClient, borrower: Borrower, product: dict[str, Any]) -> dict[str, Any]:
    application = create_application(client, borrower.party, product["id"])
    response = client.post(_url(application, "/submit"), headers=borrower.party.headers)
    assert response.status_code == 200, response.text
    return application


def _risk(client: TestClient, application: dict[str, Any], party: Party) -> list[Any]:
    response = client.get(_url(application, "/risk-assessments"), headers=party.headers)
    assert response.status_code == 200, response.text
    return list(response.json())


# --- Without an active model ---------------------------------------------------------------


def test_submission_without_a_model_records_a_failure_and_still_queues(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    analyst: Party,
    db_session: Session,
) -> None:
    application = _submit(client, borrower, product)
    detail = client.get(_url(application), headers=analyst.headers).json()
    assert detail["status"] == "PENDING_REVIEW"
    (risk,) = _risk(client, application, analyst)
    assert risk["status"] == "FAILED"
    assert risk["failure_reason"] == "no credit-risk model is active"
    assert risk["probability_of_default"] is None
    assert "advisory only" in risk["advisory_notice"]
    report = client.get(_url(application, "/credit-bureau-report"), headers=analyst.headers)
    assert report.status_code == 200
    body = report.json()
    assert body["provider"] == "SIMULATED"
    applicant = db_session.get(Applicant, uuid.UUID(borrower.applicant["id"]))
    assert applicant is not None
    expected = bureau.simulate(applicant.synthetic_reference)
    assert body["missed_payments_12m"] == expected.missed_payments_12m
    assert body["months_since_delinquency"] == expected.months_since_delinquency
    health = client.get("/health/ready").json()
    assert health["checks"]["model"] == "none active"


# --- Registry and activation ---------------------------------------------------------------


def test_activation_rules(
    client: TestClient,
    admin: Party,
    analyst: Party,
    officer: Party,
    register: Callable[..., ModelVersion],
    packaged: model_fixture.PackagedModel,
    db_session: Session,
) -> None:
    first = register()
    url = f"{MODELS}/{first.id}/activate"
    assert client.post(url, headers=analyst.headers).status_code == 403
    assert client.post(url, headers=officer.headers).status_code == 403
    activated = client.post(url, headers=admin.headers)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert client.post(url, headers=admin.headers).status_code == 409  # no longer a candidate

    second = register(model_fixture.train(version="0.0.2-test"))
    assert client.post(f"{MODELS}/{second.id}/activate", headers=admin.headers).status_code == 200
    listed = {m["version"]: m["status"] for m in client.get(MODELS, headers=analyst.headers).json()}
    assert listed == {"0.0.1-test": "RETIRED", "0.0.2-test": "ACTIVE"}
    assert client.get(MODELS, headers=officer.headers).status_code == 403
    detail = client.get(f"{MODELS}/{second.id}", headers=analyst.headers).json()
    assert [f["name"] for f in detail["feature_schema"]["features"]] == list(shared.FEATURE_NAMES)
    assert client.get("/health/ready").json()["checks"]["model"] == "ok (credify-pd 0.0.2-test)"

    with pytest.raises(Exception, match="already registered"):
        registry.register(
            db_session, packaged.metadata, artefact_uri="credify-pd/x/model.joblib",
            now=datetime.now(UTC),
        )  # fmt: skip
    db_session.rollback()


def test_an_altered_artefact_cannot_be_activated(
    client: TestClient, admin: Party, register: Callable[..., ModelVersion], model_dir: Path
) -> None:
    version = register()
    (model_dir / version.artefact_uri).write_bytes(b"not the registered model")
    response = client.post(f"{MODELS}/{version.id}/activate", headers=admin.headers)
    assert response.status_code == 422
    assert "failed verification" in response.json()["detail"]


# --- Scoring -------------------------------------------------------------------------------


def test_submission_is_scored_with_an_exact_explanation(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    analyst: Party,
    active_model: ModelVersion,
    packaged: model_fixture.PackagedModel,
) -> None:
    application = _submit(client, borrower, product)
    (risk,) = _risk(client, application, analyst)
    assert risk["status"] == "SCORED", risk["failure_reason"]
    assert risk["run_reason"] == "SUBMISSION"
    assert risk["model_version_id"] == str(active_model.id)
    probability = float(risk["probability_of_default"])
    assert 0 < probability < 1
    threshold = float(risk["decision_threshold"])
    assert risk["above_threshold"] == (probability >= threshold)
    cuts = [0.04, 0.07, threshold, 0.25]
    assert risk["risk_band"] == "ABCDE"[sum(probability >= c for c in cuts)]

    snapshot = risk["feature_snapshot"]
    assert snapshot["feature_order"] == list(shared.FEATURE_NAMES)
    assert set(snapshot["features"]) == set(shared.FEATURE_NAMES)
    assert not set(snapshot["features"]) & shared.PROTECTED_ATTRIBUTES
    # The standard borrower: 32,000 gross, permanent since 2019, no revolving credit.
    features = snapshot["features"]
    assert features["gross_monthly_income"] == 32000.0
    assert features["employment_type"] == "PERMANENT"
    assert features["revolving_utilisation"] is None
    assert features["num_credit_accounts"] == 0.0
    assert snapshot["sources"]["affordability_assessment_version"] == 1

    explanation = risk["explanation"]
    total = explanation["base_log_odds"] + sum(c["log_odds"] for c in explanation["contributions"])
    assert abs(total - explanation["log_odds"]) < 1e-4
    expected = packaged.pipeline.predict_proba(explain.feature_frame(features))[0, 1]
    assert abs(expected - probability) < 1e-6
    history = client.get(_url(application, "/status-history"), headers=analyst.headers).json()
    assert f"band {risk['risk_band']} (advisory)" in history[-1]["reason"]


def test_staff_rerun_and_who_may_see_it(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    analyst: Party,
    officer: Party,
    active_model: ModelVersion,
) -> None:
    application = _submit(client, borrower, product)
    url = _url(application, "/risk-assessments")
    assert client.post(url, headers=officer.headers).status_code == 403  # risk:run is analysts'
    rerun = client.post(url, headers=analyst.headers)
    assert rerun.status_code == 201, rerun.text
    assert (rerun.json()["version"], rerun.json()["run_reason"]) == (2, "STAFF_REQUEST")
    assert rerun.json()["requested_by"] == str(analyst.user.id)
    # Applicants never see the experimental score; other applicants get nothing at all.
    assert client.get(url, headers=borrower.party.headers).status_code == 403
    one = client.get(f"{url}/{rerun.json()['id']}", headers=officer.headers)
    assert one.status_code == 200
    assert client.get(f"{url}/{uuid.uuid4()}", headers=officer.headers).status_code == 404


def test_a_file_altered_after_activation_fails_scoring_and_readiness(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    analyst: Party,
    active_model: ModelVersion,
    model_dir: Path,
) -> None:
    (model_dir / active_model.artefact_uri).write_bytes(b"swapped")
    registry.clear_cache()
    application = _submit(client, borrower, product)
    (risk,) = _risk(client, application, analyst)
    assert risk["status"] == "FAILED"
    assert "refusing to load" in risk["failure_reason"]
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["model"] == "artefact failed verification"


# --- Citation in recommendations and decisions -------------------------------------------


def test_recommendation_and_decision_cite_the_latest_score(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    officer: Party,
    analyst: Party,
    active_model: ModelVersion,
) -> None:
    application = _submit(client, borrower, product)
    assert client.post(_url(application, "/start-review"), headers=officer.headers).is_success
    affordability = client.get(
        _url(application, "/affordability-assessments"), headers=officer.headers
    ).json()[0]
    (risk,) = _risk(client, application, officer)
    body = {
        "recommendation": "APPROVE",
        "rationale": "Affordable with a healthy buffer and stable employment.",
        "affordability_assessment_id": affordability["id"],
    }
    missing = client.post(_url(application, "/recommendation"), json=body, headers=officer.headers)
    assert missing.status_code == 422
    assert "latest credit-risk assessment (version 1)" in missing.json()["detail"]
    cited = client.post(
        _url(application, "/recommendation"),
        json={**body, "credit_risk_assessment_id": risk["id"]},
        headers=officer.headers,
    )
    assert cited.status_code == 201, cited.text
    assert cited.json()["credit_risk_assessment_id"] == risk["id"]
    # Re-scoring is blocked while the recommendation waits for a decision.
    blocked = client.post(_url(application, "/risk-assessments"), headers=analyst.headers)
    assert blocked.status_code == 409

    decision = client.post(
        _url(application, "/decision"),
        json={
            "decision": "APPROVED",
            "reason_codes": ["MEETS_CRITERIA"],
            "explanation": "Affordability supports the loan.",
            "affordability_assessment_id": affordability["id"],
            "credit_risk_assessment_id": risk["id"],
            "model_output_acknowledged": True,
        },
        headers=analyst.headers,
    )
    assert decision.status_code == 201, decision.text
    assert decision.json()["credit_risk_assessment_id"] == risk["id"]
    # Approving against "review carefully" guidance departs from the model.
    assert decision.json()["departs_from_model"] is risk["above_threshold"]


def test_risk_history_is_append_only(
    client: TestClient,
    borrower: Borrower,
    product: dict[str, Any],
    db_session: Session,
) -> None:
    _submit(client, borrower, product)
    for table in ("credit_risk_assessments", "credit_bureau_reports"):
        with pytest.raises(DBAPIError):
            db_session.execute(text(f"UPDATE {table} SET created_at = now()"))  # noqa: S608
        db_session.rollback()
    assert db_session.scalars(select(ModelVersion)).all() == []
