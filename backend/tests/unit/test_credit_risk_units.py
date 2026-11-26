"""Pure and file-level credit-risk pieces: bureau simulator, explanations, artefact checks."""

from __future__ import annotations

import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.modules.credit_risk import explain, registry
from app.modules.credit_risk import features as shared
from app.modules.credit_risk.domain import bureau
from app.modules.credit_risk.service import band_for, months_between
from tests import model_fixture


@pytest.fixture(scope="module")
def packaged() -> model_fixture.PackagedModel:
    return model_fixture.train()


# --- Simulated bureau ----------------------------------------------------------------------


def test_bureau_is_deterministic_and_labelled() -> None:
    first, again = bureau.simulate("SYN-ABC123"), bureau.simulate("SYN-ABC123")
    assert first == again
    assert first.provider == "SIMULATED"
    assert first.simulator_version == bureau.SIMULATOR_VERSION
    with pytest.raises(ValueError, match="synthetic reference"):
        bureau.simulate("")


def test_bureau_distribution_matches_the_training_data() -> None:
    reports = [bureau.simulate(f"SYN-{i:06X}") for i in range(10_000)]
    recent = sum(r.missed_payments_12m > 0 for r in reports) / len(reports)
    assert 0.16 < recent < 0.20  # 18 % have recent missed payments
    for report in reports:
        if report.missed_payments_12m:
            assert report.months_since_delinquency is not None
            assert 0 <= report.months_since_delinquency < 12
        elif report.months_since_delinquency is not None:
            assert 12 <= report.months_since_delinquency <= 96
    counts = Counter(r.missed_payments_12m for r in reports if r.missed_payments_12m)
    assert counts[1] > counts[2] > counts[3]


# --- Explanations ------------------------------------------------------------------------


def _row(packaged: model_fixture.PackagedModel, index: int = 0) -> dict[str, Any]:
    rows, _ = model_fixture.random_rows(5, seed=99)
    record = rows.iloc[index].to_dict()
    return {
        str(k): (None if isinstance(v, float) and np.isnan(v) else v) for k, v in record.items()
    }


def test_contributions_reproduce_the_model_exactly(packaged: model_fixture.PackagedModel) -> None:
    for index in range(5):
        features = _row(packaged, index)
        result = explain.explain(packaged.pipeline, packaged.metadata["explainer"], features)
        total = result["base_log_odds"] + sum(c["log_odds"] for c in result["contributions"])
        assert abs(total - result["log_odds"]) < 1e-4  # rounding to 6 places per contribution
        probability = packaged.pipeline.predict_proba(explain.feature_frame(features))[0, 1]
        assert abs(1 / (1 + np.exp(-result["log_odds"])) - probability) < 1e-6
        assert [c["feature"] for c in result["contributions"]] != []
        assert set(c["feature"] for c in result["contributions"]) == set(shared.FEATURE_NAMES)
        assert "model, not the causes" in result["caveat"]


def test_contribution_text(packaged: model_fixture.PackagedModel) -> None:
    result = explain.explain(packaged.pipeline, packaged.metadata["explainer"], _row(packaged))
    by_feature = {c["feature"]: c for c in result["contributions"]}
    dti = by_feature["debt_to_income"]
    assert dti["text"].startswith("Debt-to-income ")
    assert ("increases risk" in dti["text"]) == (dti["log_odds"] > 0)
    assert len(result["top_increasing"]) <= 3


def test_a_mismatched_explainer_is_refused(packaged: model_fixture.PackagedModel) -> None:
    wrong = {**packaged.metadata["explainer"], "base_log_odds": 5.0}
    with pytest.raises(explain.ExplanationError, match="contributions sum"):
        explain.explain(packaged.pipeline, wrong, _row(packaged))
    with pytest.raises(explain.ExplanationError, match="unsupported"):
        explain.explain(packaged.pipeline, {"kind": "shap"}, _row(packaged))


# --- Artefact verification -----------------------------------------------------------------


def _version(packaged: model_fixture.PackagedModel, uri: str, **overrides: Any) -> Any:
    values = {
        "id": uuid.uuid4(),
        "artefact_uri": uri,
        "artefact_sha256": packaged.sha256,
        "libraries": packaged.metadata["libraries"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_verified_load(packaged: model_fixture.PackagedModel, tmp_path: Path) -> None:
    uri = model_fixture.install(packaged, tmp_path)
    loaded = registry.load(tmp_path, _version(packaged, uri))
    assert list(loaded.pipeline.feature_names_in_) == list(shared.FEATURE_NAMES)


def test_altered_artefact_is_refused(packaged: model_fixture.PackagedModel, tmp_path: Path) -> None:
    uri = model_fixture.install(packaged, tmp_path)
    (tmp_path / uri).write_bytes(packaged.data + b"tampered")
    with pytest.raises(registry.ModelIntegrityError, match="refusing to load"):
        registry.load(tmp_path, _version(packaged, uri))


@pytest.mark.parametrize("uri", ["../outside.joblib", "/etc/model.joblib", "a/../../b.joblib", ""])
def test_paths_outside_the_model_directory_are_refused(uri: str, tmp_path: Path) -> None:
    with pytest.raises(registry.ModelIntegrityError):
        registry.artefact_path(tmp_path, uri)


def test_library_mismatch_and_missing_directory(
    packaged: model_fixture.PackagedModel, tmp_path: Path
) -> None:
    uri = model_fixture.install(packaged, tmp_path)
    old = _version(packaged, uri, libraries={"scikit-learn": "0.24.0"})
    with pytest.raises(registry.ModelIntegrityError, match=r"trained with scikit-learn 0\.24\.0"):
        registry.verified_bytes(tmp_path, old)
    with pytest.raises(registry.ModelIntegrityError, match="no model directory"):
        registry.load(None, _version(packaged, uri, id=uuid.uuid4()))
    with pytest.raises(registry.ModelIntegrityError, match="cannot be read"):
        registry.verified_bytes(tmp_path, _version(packaged, "credify-pd/missing/model.joblib"))


def test_schema_with_protected_attributes_is_rejected() -> None:
    schema = shared.schema()
    features = list(schema["features"])  # type: ignore[call-overload]
    leaked = {**schema, "features": [*features, {"name": "age"}]}
    problems = registry._schema_problems(leaked)
    assert any("protected attributes" in p for p in problems)
    assert registry._schema_problems(schema) == []


# --- Small helpers -------------------------------------------------------------------------


def test_bands_and_tenure() -> None:
    cutoffs = {"cuts": [0.04, 0.07, 0.13, 0.25], "bands": list("ABCDE")}
    assert band_for(Decimal("0.039999"), cutoffs).value == "A"
    assert band_for(Decimal("0.04"), cutoffs).value == "B"
    assert band_for(Decimal("0.13"), cutoffs).value == "D"
    assert band_for(Decimal("0.9"), cutoffs).value == "E"
    from datetime import date

    assert months_between(date(2024, 1, 31), date(2024, 2, 29)) == 0
    assert months_between(date(2024, 1, 15), date(2025, 1, 15)) == 12
    assert months_between(date(2030, 1, 1), date(2025, 1, 1)) == 0


def test_applicant_visible_reason_drops_only_the_credit_risk_summary() -> None:
    from app.modules.applications.service import applicant_visible_reason

    staff = "Affordability assessment v2: MARGINAL; credit-risk v3: band D (advisory)"
    assert applicant_visible_reason(staff) == "Affordability assessment v2: MARGINAL"
    failed = "Affordability assessment v1: AFFORDABLE; credit-risk v1: not scored (no model)"
    assert applicant_visible_reason(failed) == "Affordability assessment v1: AFFORDABLE"
    assert applicant_visible_reason("Applicant withdrew") == "Applicant withdrew"
    assert applicant_visible_reason(None) is None
