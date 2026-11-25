"""Evaluation, fairness and registration staging on a small generated dataset."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from app.modules.credit_risk import explain
from app.modules.credit_risk import features as shared

from credify_ml import evaluate as ev
from credify_ml import fairness as fair
from credify_ml import register as reg
from credify_ml import train as trainer
from credify_ml.config import MAX_FLAGGED_RATE, MIN_BAND_SHARE, MODEL_NAME, MODEL_VERSION
from credify_ml.generate import generate, write


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("ml")
    data, summary = root / "data", root / "summary"
    write(generate(12_000), data)
    trainer.write_summary(trainer.train(data, artefacts_dir=None, quick=True), summary)
    return {
        "data": data,
        "summary": summary,
        "artefacts": root / "artefacts",
        "reports": root / "reports",
    }


@pytest.fixture(scope="module")
def report(workspace: dict[str, Path]) -> dict[str, Any]:
    result = ev.evaluate(workspace["data"], workspace["summary"])
    model, explainer = result["_model"], result["_explainer"]
    paths = ev.write(result, workspace["artefacts"], workspace["reports"])
    return {"report": result, "model": model, "explainer": explainer, "paths": paths}


def test_choices_follow_their_rules(report: dict[str, Any]) -> None:
    r = report["report"]
    validation = r["operating_threshold"]["validation"]
    assert validation["flagged_rate"] <= MAX_FLAGGED_RATE
    calibration = r["calibration"]
    needed = calibration["uncalibrated_validation_ece"] > ev.MAX_ECE_WITHOUT_CALIBRATION
    assert calibration["applied"] is needed
    if needed:  # folded Platt scaling must actually improve calibration
        assert calibration["validation"]["ece"] < calibration["uncalibrated_validation_ece"]
        assert calibration["platt"]["slope"] > 0
    bands = r["bands"]["validation"]
    assert all(row["share"] >= MIN_BAND_SHARE for row in bands)
    rates = [row["default_rate"] for row in bands]
    assert all(a < b for a, b in itertools.pairwise(rates))
    assert r["bands"]["cuts"][2] == r["operating_threshold"]["threshold"]  # D = "flagged"


def test_test_set_scored_once_with_every_metric(report: dict[str, Any]) -> None:
    r = report["report"]
    assert set(r["test_metrics"]) == {"logistic_regression", "hist_gradient_boosting"}
    cm = r["confusion_matrix_test"]
    assert (
        sum(cm[k] for k in ("true_negative", "false_positive", "false_negative", "true_positive"))
        == r["split"]["test"]["rows"]
    )
    assert len(r["learned_vs_true"]) == 18


def test_packaged_explainer_is_exact(report: dict[str, Any], workspace: dict[str, Path]) -> None:
    frame, manifest = trainer.load_dataset(workspace["data"])
    row = trainer.split(frame, manifest["seed"])["test"].iloc[0]
    features = {
        name: (None if isinstance(row[name], float) and np.isnan(row[name]) else row[name])
        for name in shared.FEATURE_NAMES
    }
    result = explain.explain(report["model"], report["explainer"], features)
    total = result["base_log_odds"] + sum(c["log_odds"] for c in result["contributions"])
    assert abs(total - result["log_odds"]) < 1e-4


def test_metadata_and_check_mode(report: dict[str, Any], workspace: dict[str, Path]) -> None:
    metadata = json.loads(report["paths"]["metadata"].read_text(encoding="utf-8"))
    assert metadata["name"] == MODEL_NAME
    assert metadata["feature_schema"] == shared.schema()
    assert (
        not {f["name"] for f in metadata["feature_schema"]["features"]}
        & shared.PROTECTED_ATTRIBUTES
    )
    assert metadata["explainer"]["kind"] == "linear_exact"
    committed = json.loads(report["paths"]["report"].read_text(encoding="utf-8"))
    drifted = json.loads(json.dumps(committed))
    drifted["test_metrics"]["logistic_regression"]["roc_auc"] += 0.01
    assert ev.differences(committed, drifted, tolerance=ev.CHECK_TOLERANCE)
    assert ev.differences(committed, committed, tolerance=ev.CHECK_TOLERANCE) == []


def test_fairness_report(report: dict[str, Any], workspace: dict[str, Path]) -> None:
    result = fair.fairness(workspace["data"], workspace["artefacts"])
    assert result["attributes_are_features"] is False
    assert set(result["disparities"]) == {"sex", "age_band"}
    for gaps in result["disparities"].values():
        assert 0 < gaps["disparate_impact_ratio"] <= 1
        assert gaps["equal_opportunity_difference"] >= 0
    groups = {row["group"] for row in result["groups"]["sex"]}
    assert groups == {"F", "M"}
    proxies = {row["feature"]: row["proxy_for"] for row in result["proxy_check"]["features"]}
    assert "employment_type" in proxies
    assert "Credify never uses it" in result["mitigation"]["not_adopted"]
    assert "four-fifths" in fair.render_markdown(result)


def test_registration_staging(
    report: dict[str, Any], workspace: dict[str, Path], tmp_path: Path
) -> None:
    metadata, uri = reg.stage(workspace["artefacts"], tmp_path / "store")
    assert uri == f"{MODEL_NAME}/{MODEL_VERSION}/model.joblib"
    assert (tmp_path / "store" / uri).read_bytes() == report["paths"]["model"].read_bytes()
    # Staging again with identical bytes is fine; different bytes are refused.
    reg.stage(workspace["artefacts"], tmp_path / "store")
    (tmp_path / "store" / uri).write_bytes(b"something else")
    with pytest.raises(reg.RegistrationError, match="different contents"):
        reg.stage(workspace["artefacts"], tmp_path / "store")
    tampered = workspace["artefacts"] / f"{MODEL_NAME}-{MODEL_VERSION}" / "metadata.json"
    original = tampered.read_text(encoding="utf-8")
    try:
        tampered.write_text(original.replace(metadata["artefact_sha256"], "0" * 64), "utf-8")
        with pytest.raises(reg.RegistrationError, match="does not match"):
            reg.stage(workspace["artefacts"], tmp_path / "other")
    finally:
        tampered.write_text(original, "utf-8")
