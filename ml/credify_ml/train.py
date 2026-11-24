"""Train and compare the baseline, logistic regression and gradient boosting models.

    python -m credify_ml.train                       # writes reports/phase6/ and artefacts/
    python -m credify_ml.train --check               # re-train and compare with the committed
                                                     # summary; exit 1 if any metric differs

Tuning uses stratified 5-fold cross-validation on the **training** split only. Models are
compared on the **validation** split. The **test** split is fixed now (its ids are hashed in
the summary) but never scored here: it is used once, for the Phase 7 evaluation report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from app.modules.credit_risk.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from sklearn.base import clone
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline

from credify_ml.config import (
    ARTEFACTS_DIR,
    BORROWERS_FILE,
    CV_FOLDS,
    DATA_DIR,
    ID,
    LABEL,
    MANIFEST_FILE,
    REPORTS_DIR,
    SPLIT,
    TRAINER_VERSION,
)
from credify_ml.generate import content_hash
from credify_ml.models import Candidate, candidates

SUMMARY_DIR = REPORTS_DIR / "phase6"
SUMMARY_FILE = "training_summary.json"
METRIC_TOLERANCE = 1e-4  # reproducibility check: metrics must agree to four decimal places
MATERIAL_AUC_GAIN = 0.02  # approved decision D4


class DatasetIntegrityError(RuntimeError):
    """The dataset on disk is not the one its manifest describes."""


def load_dataset(data_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = json.loads((data_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
    frame = pd.read_parquet(data_dir / BORROWERS_FILE)
    actual = content_hash(frame)
    expected = manifest["content_sha256"]["borrowers"]
    if actual != expected:
        raise DatasetIntegrityError(f"dataset content hash {actual} != manifest {expected}")
    return frame, manifest


def _ids_hash(ids: pd.Series) -> str:
    return hashlib.sha256("\n".join(sorted(ids.astype(str))).encode("utf-8")).hexdigest()


def split(frame: pd.DataFrame, seed: int) -> dict[str, pd.DataFrame]:
    """Stratified train / validation / test split, fixed by the seed."""
    train_share, validation_share, test_share = SPLIT
    train, rest = train_test_split(
        frame, train_size=train_share, stratify=frame[LABEL], random_state=seed
    )
    validation, test = train_test_split(
        rest,
        test_size=test_share / (validation_share + test_share),
        stratify=rest[LABEL],
        random_state=seed,
    )
    return {"train": train, "validation": validation, "test": test}


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    clipped = np.clip(p, 1e-15, 1 - 1e-15)
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, clipped)),
        "mean_predicted": float(np.mean(p)),
        "observed_rate": float(np.mean(y)),
    }


def _rounded(values: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 6) for k, v in values.items()}


def _fit(
    candidate: Candidate, X: pd.DataFrame, y: np.ndarray, seed: int
) -> tuple[Pipeline, dict[str, Any]]:
    if not candidate.grid:
        model = clone(candidate.pipeline).fit(X, y)
        return model, {"best_params": {}, "cv": None}
    search = GridSearchCV(
        clone(candidate.pipeline),
        candidate.grid,
        scoring={"roc_auc": "roc_auc", "brier": "neg_brier_score", "log_loss": "neg_log_loss"},
        refit="roc_auc",
        cv=StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=seed),
        n_jobs=1,
    )
    search.fit(X, y)
    best = search.best_index_
    results = search.cv_results_
    cv = {
        "folds": CV_FOLDS,
        "selection_metric": "roc_auc",
        "roc_auc_mean": float(results["mean_test_roc_auc"][best]),
        "roc_auc_std": float(results["std_test_roc_auc"][best]),
        "brier_mean": float(-results["mean_test_brier"][best]),
        "log_loss_mean": float(-results["mean_test_log_loss"][best]),
        "grid": {k: [str(v) for v in values] for k, values in candidate.grid.items()},
        "combinations": len(results["params"]),
    }
    best_params = {k: v for k, v in search.best_params_.items()}
    return search.best_estimator_, {"best_params": best_params, "cv": _rounded_cv(cv)}


def _rounded_cv(cv: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in cv.items()}


def _comparison(models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lr = models["logistic_regression"]["validation"]
    gbm = models["hist_gradient_boosting"]["validation"]
    gain = gbm["roc_auc"] - lr["roc_auc"]
    material = gain >= MATERIAL_AUC_GAIN and gbm["brier"] < lr["brier"]
    return {
        "rule": "Serve logistic regression unless gradient boosting is materially better: "
        f"ROC-AUC +{MATERIAL_AUC_GAIN} and a lower Brier score (approved decision D4).",
        "gbm_minus_lr_roc_auc": round(gain, 6),
        "gbm_minus_lr_brier": round(gbm["brier"] - lr["brier"], 6),
        "gbm_materially_better": material,
        "provisional_choice": "hist_gradient_boosting" if material else "logistic_regression",
        "status": "provisional: confirmed in Phase 7 after calibration and the one-off "
        "test-set evaluation",
    }


def train(
    data_dir: Path = DATA_DIR,
    *,
    artefacts_dir: Path | None = ARTEFACTS_DIR,
    quick: bool = False,
) -> dict[str, Any]:
    frame, manifest = load_dataset(data_dir)
    seed = int(manifest["seed"])
    parts = split(frame, seed)
    X_train, y_train = parts["train"][list(FEATURE_NAMES)], parts["train"][LABEL].to_numpy()
    X_val, y_val = parts["validation"][list(FEATURE_NAMES)], parts["validation"][LABEL].to_numpy()

    models: dict[str, dict[str, Any]] = {}
    for candidate in candidates(seed, quick=quick):
        started = time.perf_counter()
        fitted, tuning = _fit(candidate, X_train, y_train, seed)
        probabilities = fitted.predict_proba(X_val)[:, 1]
        entry: dict[str, Any] = {
            "description": candidate.description,
            **tuning,
            "validation": _rounded(_metrics(y_val, probabilities)),
            "fit_seconds": round(time.perf_counter() - started, 1),
        }
        if artefacts_dir is not None:
            entry["artefact"] = _save(fitted, artefacts_dir, candidate.name, manifest)
        models[candidate.name] = entry

    oracle = parts["validation"]["dgp_probability"].to_numpy()
    return {
        "trainer_version": TRAINER_VERSION,
        # The generator's own probabilities on the validation split: the ceiling any model
        # trained on these features could approach. Not a model, never deployable.
        "dgp_oracle_validation": _rounded(_metrics(y_val, oracle)),
        "dataset": {
            "generator_version": manifest["generator_version"],
            "seed": seed,
            "rows": manifest["rows"],
            "content_sha256": manifest["content_sha256"]["borrowers"],
            "default_rate": manifest["default_rate"],
        },
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": list(FEATURE_NAMES),
        "split": {
            name: {
                "rows": len(part),
                "default_rate": round(float(part[LABEL].mean()), 6),
                "ids_sha256": _ids_hash(part[ID]),
            }
            for name, part in parts.items()
        },
        "test_set_used": False,
        "models": models,
        "comparison": _comparison(models),
        "libraries": {
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "quick": quick,
    }


def _save(model: Pipeline, root: Path, name: str, manifest: dict[str, Any]) -> dict[str, str]:
    """Write the fitted pipeline with joblib and record its SHA-256 (registry: Phase 7)."""
    folder = root / f"{name}-{manifest['content_sha256']['borrowers'][:12]}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "model.joblib"
    joblib.dump(model, path)
    return {
        "path": path.relative_to(root.parent).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "note": "git-ignored; reproducible from the dataset. The SHA-256 depends on library "
        "versions, so the reproducibility check compares metrics, not artefact bytes.",
    }


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Differences that matter for reproducibility (timings and artefact bytes excluded)."""
    problems = []
    for key in ("dataset", "split", "features", "feature_schema_version", "dgp_oracle_validation"):
        if expected.get(key) != actual.get(key):
            problems.append(f"{key} differs")
    for name, model in expected["models"].items():
        got = actual["models"].get(name)
        if got is None:
            problems.append(f"model {name} is missing")
            continue
        if json.dumps(model["best_params"], sort_keys=True, default=str) != json.dumps(
            got["best_params"], sort_keys=True, default=str
        ):
            problems.append(f"{name}: best parameters differ")
        for metric, value in model["validation"].items():
            if abs(value - got["validation"][metric]) > METRIC_TOLERANCE:
                problems.append(
                    f"{name}: validation {metric} {got['validation'][metric]} != {value}"
                )
    if expected["comparison"]["provisional_choice"] != actual["comparison"]["provisional_choice"]:
        problems.append("the provisional model choice differs")
    return problems


def write_summary(summary: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SUMMARY_FILE
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", "utf-8")
    (out_dir / "training_summary.md").write_text(render_markdown(summary), "utf-8")
    return path


def render_markdown(summary: dict[str, Any]) -> str:
    dataset = summary["dataset"]
    lines = [
        "# Phase 6 training summary",
        "",
        "> Generated by `python -m credify_ml.train`. Synthetic data: these figures describe how "
        "well each model recovers the simulated process, not real-world performance. The "
        "test split has not been scored; the final evaluation is Phase 7.",
        "",
        f"- Dataset: {dataset['rows']:,} synthetic borrowers, `{dataset['generator_version']}`, "
        f"seed {dataset['seed']}, default rate {dataset['default_rate']:.2%}",
        f"- Content SHA-256: `{dataset['content_sha256']}`",
        f"- Features: {len(summary['features'])} (`{summary['feature_schema_version']}`)",
        "- Split: "
        + ", ".join(
            f"{name} {part['rows']:,} ({part['default_rate']:.2%} default)"
            for name, part in summary["split"].items()
        ),
        "",
        "| Model | CV ROC-AUC (train, 5-fold) | Val ROC-AUC | Val PR-AUC | Val Brier | "
        "Val log loss | Mean predicted | Best parameters |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, model in summary["models"].items():
        v = model["validation"]
        cv = model["cv"]
        cv_text = f"{cv['roc_auc_mean']:.4f} ± {cv['roc_auc_std']:.4f}" if cv else "—"
        params = ", ".join(
            f"{k.removeprefix('model__')}={p}" for k, p in model["best_params"].items()
        )
        lines.append(
            f"| {name} | {cv_text} | {v['roc_auc']:.4f} | {v['pr_auc']:.4f} | {v['brier']:.4f} | "
            f"{v['log_loss']:.4f} | {v['mean_predicted']:.4f} | {params or '—'} |"
        )
    oracle = summary["dgp_oracle_validation"]
    lines.append(
        f"| *DGP oracle (true probabilities; ceiling, not a model)* | — | {oracle['roc_auc']:.4f} "
        f"| {oracle['pr_auc']:.4f} | {oracle['brier']:.4f} | {oracle['log_loss']:.4f} | "
        f"{oracle['mean_predicted']:.4f} | — |"
    )
    comparison = summary["comparison"]
    lines += [
        "",
        f"**Comparison (provisional):** {comparison['rule']} Gradient boosting minus logistic "
        f"regression: ROC-AUC {comparison['gbm_minus_lr_roc_auc']:+.4f}, Brier "
        f"{comparison['gbm_minus_lr_brier']:+.4f}. Provisional choice: "
        f"`{comparison['provisional_choice']}` ({comparison['status']}).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and compare the Phase 6 models.")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--out", type=Path, default=SUMMARY_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Re-train and compare with the committed summary instead of overwriting it.",
    )
    args = parser.parse_args(argv)
    summary = train(args.data, artefacts_dir=None if args.check else ARTEFACTS_DIR)
    if args.check:
        expected = json.loads((args.out / SUMMARY_FILE).read_text(encoding="utf-8"))
        problems = compare(expected, summary)
        for problem in problems:
            print(f"MISMATCH {problem}")
        print("Reproducible." if not problems else f"{len(problems)} difference(s).")
        return 1 if problems else 0
    path = write_summary(summary, args.out)
    print(render_markdown(summary))
    print(f"Summary written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
