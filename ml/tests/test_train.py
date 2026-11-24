"""Training: deterministic, leakage-free, and the reproducibility check catches drift."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from credify_ml import train as trainer
from credify_ml.config import LABEL, MANIFEST_FILE
from credify_ml.generate import generate, write


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("data")
    write(generate(3000), folder)
    return folder


@pytest.fixture(scope="module")
def summary(data_dir: Path) -> dict[str, Any]:
    return trainer.train(data_dir, artefacts_dir=None, quick=True)


def test_training_is_deterministic(data_dir: Path, summary: dict[str, Any]) -> None:
    again = trainer.train(data_dir, artefacts_dir=None, quick=True)
    assert trainer.compare(summary, again) == []


def test_models_beat_the_baseline(summary: dict[str, Any]) -> None:
    models = summary["models"]
    # No model can beat the generator's own probabilities by more than noise.
    oracle = summary["dgp_oracle_validation"]["roc_auc"]
    assert models["logistic_regression"]["validation"]["roc_auc"] < oracle + 0.05
    assert models["baseline"]["validation"]["roc_auc"] == 0.5
    assert models["logistic_regression"]["validation"]["roc_auc"] > 0.6
    assert models["hist_gradient_boosting"]["validation"]["roc_auc"] > 0.55
    assert summary["comparison"]["provisional_choice"] in {
        "logistic_regression",
        "hist_gradient_boosting",
    }


def test_test_split_is_fixed_but_unused(data_dir: Path, summary: dict[str, Any]) -> None:
    assert summary["test_set_used"] is False
    for model in summary["models"].values():
        assert set(model) >= {"validation"}
        assert "test" not in model
    frame, manifest = trainer.load_dataset(data_dir)
    parts = trainer.split(frame, manifest["seed"])
    ids = [set(p["borrower_id"]) for p in parts.values()]
    assert not ids[0] & ids[1]
    assert not ids[0] & ids[2]
    assert not ids[1] & ids[2]
    assert sum(len(i) for i in ids) == len(frame)
    # Stratified: each part's default rate is within a point of the whole.
    for part in parts.values():
        assert abs(part[LABEL].mean() - frame[LABEL].mean()) < 0.01


def test_compare_flags_metric_drift(summary: dict[str, Any]) -> None:
    drifted = copy.deepcopy(summary)
    drifted["models"]["logistic_regression"]["validation"]["roc_auc"] += 0.01
    problems = trainer.compare(summary, drifted)
    assert any("logistic_regression: validation roc_auc" in p for p in problems)
    tiny = copy.deepcopy(summary)
    tiny["models"]["logistic_regression"]["validation"]["roc_auc"] += 0.00005
    assert trainer.compare(summary, tiny) == []


def test_tampered_dataset_is_refused(tmp_path: Path) -> None:
    write(generate(100), tmp_path)
    manifest_path = tmp_path / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["content_sha256"]["borrowers"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(trainer.DatasetIntegrityError):
        trainer.load_dataset(tmp_path)


def test_artefacts_and_summary_files(data_dir: Path, tmp_path: Path) -> None:
    summary = trainer.train(data_dir, artefacts_dir=tmp_path / "artefacts", quick=True)
    artefact = summary["models"]["logistic_regression"]["artefact"]
    assert len(artefact["sha256"]) == 64
    assert (tmp_path / artefact["path"]).exists()
    path = trainer.write_summary(summary, tmp_path / "report")
    assert json.loads(path.read_text(encoding="utf-8"))["test_set_used"] is False
    assert "test split has not been scored" in (
        tmp_path / "report" / "training_summary.md"
    ).read_text(encoding="utf-8")
