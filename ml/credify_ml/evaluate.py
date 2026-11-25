"""Final evaluation of the chosen model (docs/ML_STRATEGY.md §4–§7).

    python -m credify_ml.evaluate

Every *choice* is made on the validation split: whether to calibrate, the operating
threshold and the risk-band cut-offs. The test split is then scored **once**, to report how
those choices perform on data nothing was tuned on. The result is the model to register:

* ``artefacts/<name>-<version>/model.joblib`` and ``metadata.json`` (git-ignored), and
* ``reports/<name>-<version>/evaluation.{json,md}`` (committed).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from app.modules.credit_risk import features as shared
from app.modules.credit_risk.explain import build_linear_explainer
from sklearn.base import clone
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from sklearn.pipeline import Pipeline

from credify_ml import dgp
from credify_ml.config import (
    ARTEFACTS_DIR,
    DATA_DIR,
    LABEL,
    MAX_FLAGGED_RATE,
    MIN_BAND_SHARE,
    MODEL_NAME,
    MODEL_VERSION,
    REPORTS_DIR,
)
from credify_ml.models import candidates
from credify_ml.train import SUMMARY_DIR, SUMMARY_FILE, _metrics, load_dataset, split

THRESHOLDS = [round(float(t), 2) for t in np.arange(0.05, 0.601, 0.01)]
REPORT_THRESHOLDS = [round(float(t), 2) for t in np.arange(0.05, 0.601, 0.05)]
CALIBRATION_BINS = 10
CHECK_TOLERANCE = 1e-4  # reports must agree to four decimal places
MAX_ECE_WITHOUT_CALIBRATION = 0.015
# Provisional bands (ML_STRATEGY.md §7): A < 3 %, B < 7 %, C < 15 %, D < 30 %, E ≥ 30 %.
PROVISIONAL_CUTS = (0.03, 0.07, 0.15, 0.30)
A_CUT_CANDIDATES = (0.03, 0.04, 0.05, 0.06)
E_CUT_CANDIDATES = (0.30, 0.25, 0.20)
BANDS = ("A", "B", "C", "D", "E")
GUIDANCE = {
    "A": "Low modelled risk",
    "B": "Below-average modelled risk",
    "C": "Average modelled risk",
    "D": "Elevated modelled risk — review carefully",
    "E": "High modelled risk — review carefully",
}

# Expected sign of each feature's main coefficient under the documented process.
EXPECTED_SIGNS = {
    "debt_to_income": +1,
    "missed_payments_12m": +1,
    "months_since_delinquency": -1,  # more months since → less recent → lower risk
    "revolving_utilisation": +1,
    "residual_income": -1,
    "employment_tenure_months": -1,
    "loan_to_income": +1,
    "term_months": +1,
    "num_credit_accounts": +1,
}


def _r(value: float, places: int = 6) -> float:
    return round(float(value), places)


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = CALIBRATION_BINS) -> dict[str, Any]:
    """Equal-frequency reliability bins and the expected calibration error."""
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    index = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    rows, ece = [], 0.0
    for b in range(bins):
        mask = index == b
        if not mask.any():
            continue
        predicted, observed, share = (
            float(p[mask].mean()),
            float(y[mask].mean()),
            float(mask.mean()),
        )
        ece += abs(predicted - observed) * share
        rows.append(
            {"bin": b + 1, "share": _r(share, 4), "mean_predicted": _r(predicted, 4),
             "observed_rate": _r(observed, 4)}
        )  # fmt: skip
    return {"bins": rows, "ece": _r(ece)}


def threshold_row(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, float]:
    flagged = p >= threshold
    tp = int((flagged & (y == 1)).sum())
    fp = int((flagged & (y == 0)).sum())
    fn = int((~flagged & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "flagged_rate": _r(flagged.mean(), 4),
        "precision": _r(precision, 4),
        "recall": _r(recall, 4),
        "f1": _r(f1, 4),
    }


def fold_platt_scaling(
    model: Pipeline, X_val: pd.DataFrame, y_val: np.ndarray
) -> tuple[Pipeline, dict[str, float]]:
    """Platt scaling on the validation split, folded into the logistic regression.

    Platt fits p = σ(a·logit + b). Because the model's logit is linear in the transformed
    features, multiplying its coefficients by ``a`` and setting its intercept to
    ``a·intercept + b`` gives exactly the calibrated probabilities from a plain logistic
    regression, so loading, scoring and the exact explanations are unchanged.
    """
    logit = np.asarray(model.decision_function(X_val), dtype=float).reshape(-1, 1)
    platt = LogisticRegression(C=1e6, max_iter=1000).fit(logit, y_val)
    slope, offset = float(platt.coef_[0, 0]), float(platt.intercept_[0])
    folded = copy.deepcopy(model)
    estimator = folded[-1]
    estimator.coef_ = estimator.coef_ * slope
    estimator.intercept_ = estimator.intercept_ * slope + offset
    return folded, {"slope": round(slope, 6), "intercept": round(offset, 6)}


def choose_threshold(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    """The highest-recall threshold whose review workload stays within MAX_FLAGGED_RATE."""
    rows = [threshold_row(y, p, t) for t in THRESHOLDS]
    eligible = [r for r in rows if r["flagged_rate"] <= MAX_FLAGGED_RATE]
    chosen = max(eligible, key=lambda r: (r["recall"], -r["threshold"]))
    chosen = {**chosen, "threshold": float(chosen["threshold"])}
    return {
        "rule": f"highest recall on defaulters with at most {MAX_FLAGGED_RATE:.0%} of "
        "applications flagged for careful review (chosen on the validation split)",
        "threshold": chosen["threshold"],
        "validation": chosen,
    }


def band_of(p: np.ndarray, cuts: tuple[float, ...]) -> np.ndarray:
    return np.array(BANDS)[np.searchsorted(np.array(cuts), p, side="right")]


def band_table(y: np.ndarray, p: np.ndarray, cuts: tuple[float, ...]) -> list[dict[str, Any]]:
    bands = band_of(p, cuts)
    bounds = (0.0, *cuts, 1.0)
    rows = []
    for i, band in enumerate(BANDS):
        mask = bands == band
        rows.append(
            {
                "band": band,
                "pd_from": bounds[i],
                "pd_to": bounds[i + 1],
                "share": _r(mask.mean(), 4),
                "default_rate": _r(y[mask].mean(), 4) if mask.any() else None,
                "mean_pd": _r(p[mask].mean(), 4) if mask.any() else None,
                "guidance": GUIDANCE[band],
            }
        )
    return rows


def choose_bands(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, Any]:
    """Finalise the provisional cut-offs on validation (smallest change first).

    B keeps its provisional 7 % edge and D starts at the operating threshold, so "flagged" and
    "review carefully" mean the same thing. A widens from 3 %, and only if that is not enough
    the E edge comes down from 30 % in 5-point steps, until every band holds MIN_BAND_SHARE
    of validation applications and default rates rise from A to E.
    """
    _, b_cut, _, _ = PROVISIONAL_CUTS
    tried = []
    for e_cut in E_CUT_CANDIDATES:
        for a_cut in A_CUT_CANDIDATES:
            cuts = (a_cut, b_cut, threshold, e_cut)
            table = band_table(y, p, cuts)
            rates = [row["default_rate"] for row in table]
            sizes_ok = all(row["share"] >= MIN_BAND_SHARE for row in table)
            rising = all(r is not None for r in rates) and all(
                a < b for a, b in itertools.pairwise(rates)
            )
            tried.append({"cuts": list(cuts), "sizes_ok": sizes_ok, "rising": rising})
            if sizes_ok and rising:
                return {"cuts": list(cuts), "validation": table, "tried": tried}
    raise RuntimeError(f"no band cut-offs satisfy the rules: {tried}")


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def learned_vs_true(explainer: dict[str, Any]) -> list[dict[str, Any]]:
    """Directional agreement between the fitted coefficients and the documented process."""
    by_name = dict(zip(explainer["transformed_features"], explainer["coefficients"], strict=True))
    rows = []
    for feature, expected in EXPECTED_SIGNS.items():
        column = next(n for n in by_name if n.split("__", 1)[-1] == feature)
        coefficient = by_name[column]
        rows.append(
            {"feature": feature, "expected": "+" if expected > 0 else "−",
             "coefficient": _r(coefficient, 4),
             "agrees": bool(np.sign(coefficient) == expected)}
        )  # fmt: skip

    def category(feature: str, value: str) -> float:
        return float(by_name[f"categorical__{feature}_{value}"])

    for value, effect in dgp.EMPLOYMENT_EFFECTS.items():
        if value == "PERMANENT":
            continue
        diff = category("employment_type", value) - category("employment_type", "PERMANENT")
        rows.append(
            {"feature": f"employment_type={value} vs PERMANENT", "expected": "+" if effect > 0
             else "−", "coefficient": _r(diff, 4), "agrees": bool(np.sign(diff) == np.sign(effect))}
        )  # fmt: skip
    neutral = np.mean(
        [category("loan_purpose", v) for v in shared.FEATURES[12].categories
         if v not in dgp.PURPOSE_EFFECTS]
    )  # fmt: skip
    for value, effect in dgp.PURPOSE_EFFECTS.items():
        diff = category("loan_purpose", value) - float(neutral)
        rows.append(
            {"feature": f"loan_purpose={value} vs neutral purposes", "expected": "+" if effect > 0
             else "−", "coefficient": _r(diff, 4), "agrees": bool(np.sign(diff) == np.sign(effect))}
        )  # fmt: skip
    return rows


def score_distribution(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    quantiles = [0.05, 0.25, 0.5, 0.75, 0.95]
    return {
        outcome: {f"p{int(q * 100)}": _r(np.quantile(p[y == value], q), 4) for q in quantiles}
        for outcome, value in (("defaulted", 1), ("repaid", 0))
    }


def evaluate(data_dir: Path = DATA_DIR, summary_dir: Path = SUMMARY_DIR) -> dict[str, Any]:
    frame, manifest = load_dataset(data_dir)
    seed = int(manifest["seed"])
    training = json.loads((summary_dir / SUMMARY_FILE).read_text(encoding="utf-8"))
    choice = training["comparison"]["provisional_choice"]
    parts = split(frame, seed)
    cols = list(shared.FEATURE_NAMES)
    X_train, y_train = parts["train"][cols], parts["train"][LABEL].to_numpy()
    X_val, y_val = parts["validation"][cols], parts["validation"][LABEL].to_numpy()
    X_test, y_test = parts["test"][cols], parts["test"][LABEL].to_numpy()

    fitted: dict[str, Pipeline] = {}
    for candidate in candidates(seed):
        if candidate.name == "baseline":
            continue
        params = training["models"][candidate.name]["best_params"]
        fitted[candidate.name] = (
            clone(candidate.pipeline).set_params(**params).fit(X_train, y_train)
        )
    model = fitted[choice]
    if choice != "logistic_regression":  # pragma: no cover - not the case for v1
        raise NotImplementedError("exact explanations are implemented for logistic regression")

    # --- Choices, on validation -------------------------------------------------------
    p_val = model.predict_proba(X_val)[:, 1]
    calibration_val = calibration_table(y_val, p_val)
    uncalibrated_ece = calibration_val["ece"]
    platt: dict[str, float] | None = None
    if uncalibrated_ece > MAX_ECE_WITHOUT_CALIBRATION:
        model, platt = fold_platt_scaling(model, X_val, y_val)
        p_val = model.predict_proba(X_val)[:, 1]
        calibration_val = calibration_table(y_val, p_val)
    threshold = choose_threshold(y_val, p_val)
    bands = choose_bands(y_val, p_val, threshold["threshold"])
    cuts = tuple(bands["cuts"])

    # --- The one-off test evaluation ----------------------------------------------------
    p_test = model.predict_proba(X_test)[:, 1]
    flagged = p_test >= threshold["threshold"]
    tn, fp, fn, tp = confusion_matrix(y_test, flagged.astype(int), labels=[0, 1]).ravel()
    explainer = build_linear_explainer(model, X_train)
    report: dict[str, Any] = {
        "model": {"name": MODEL_NAME, "version": MODEL_VERSION, "algorithm": choice},
        "dataset": training["dataset"],
        "split": training["split"],
        "feature_schema_version": shared.FEATURE_SCHEMA_VERSION,
        "trained_on": "training split only (35,000 rows); choices on validation; test scored once",
        "calibration": {
            "applied": platt is not None,
            "method": "Platt scaling folded into the coefficients" if platt else None,
            "platt": platt,
            "uncalibrated_validation_ece": uncalibrated_ece,
            "rule": f"calibrate only if the validation ECE exceeds {MAX_ECE_WITHOUT_CALIBRATION}; "
            "Platt scaling is folded into the logistic regression, so the served model stays a "
            "plain logistic regression and its explanations stay exact",
            "validation": calibration_val,
            "test": calibration_table(y_test, p_test),
        },
        "operating_threshold": {
            **threshold,
            "test": threshold_row(y_test, p_test, threshold["threshold"]),
        },
        "threshold_table": {
            "validation": [threshold_row(y_val, p_val, t) for t in REPORT_THRESHOLDS],
            "test": [threshold_row(y_test, p_test, t) for t in REPORT_THRESHOLDS],
        },
        "bands": {
            "cuts": list(cuts),
            "provisional_cuts": list(PROVISIONAL_CUTS),
            "rule": "B keeps its 7 % edge and D starts at the operating threshold; A widens "
            "from 3 %, then the E edge comes down from 30 % in 5-point steps, until every band "
            f"holds at least {MIN_BAND_SHARE:.0%} of validation applications with default "
            "rates rising from A to E",
            "validation": bands["validation"],
            "test": band_table(y_test, p_test, cuts),
        },
        "test_metrics": {
            name: {k: _r(v) for k, v in _metrics(y_test, m.predict_proba(X_test)[:, 1]).items()}
            for name, m in fitted.items()
        },
        "test_prior_baseline": {
            k: _r(v) for k, v in _metrics(y_test, np.full(len(y_test), y_train.mean())).items()
        },
        "test_dgp_oracle": {
            k: _r(v)
            for k, v in _metrics(y_test, parts["test"]["dgp_probability"].to_numpy()).items()
        },
        "confusion_matrix_test": {
            "threshold": threshold["threshold"],
            "true_negative": int(tn),
            "false_positive": int(fp),
            "false_negative": int(fn),
            "true_positive": int(tp),
        },
        "score_distribution_test": score_distribution(y_test, p_test),
        "learned_vs_true": learned_vs_true(explainer),
        "libraries": {
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    report["_model"] = model
    report["_explainer"] = explainer
    return report


def write(report: dict[str, Any], artefacts_dir: Path, reports_dir: Path) -> dict[str, Path]:
    model: Pipeline = report.pop("_model")
    explainer: dict[str, Any] = report.pop("_explainer")
    slug = f"{MODEL_NAME}-{MODEL_VERSION}"
    out = artefacts_dir / slug
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "model.joblib"
    joblib.dump(model, model_path)
    metadata = {
        "name": MODEL_NAME,
        "version": MODEL_VERSION,
        "algorithm": report["model"]["algorithm"],
        "artefact_file": model_path.name,
        "artefact_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "feature_schema": shared.schema(),
        "dataset": report["dataset"],
        "decision_threshold": report["operating_threshold"]["threshold"],
        "band_cutoffs": {
            "cuts": report["bands"]["cuts"],
            "bands": list(BANDS),
            "guidance": GUIDANCE,
        },
        "metrics": {
            "validation_at_threshold": report["operating_threshold"]["validation"],
            "test": report["test_metrics"][report["model"]["algorithm"]],
            "test_at_threshold": report["operating_threshold"]["test"],
            "calibration_ece": {
                "validation": report["calibration"]["validation"]["ece"],
                "test": report["calibration"]["test"]["ece"],
            },
        },
        "explainer": explainer,
        "libraries": report["libraries"],
        "git_commit": _git_commit(),
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    metadata_path = out / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", "utf-8")

    report_dir = reports_dir / slug
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "evaluation.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    (report_dir / "evaluation.md").write_text(render_markdown(report), "utf-8")
    return {"model": model_path, "metadata": metadata_path, "report": json_path}


def differences(expected: Any, actual: Any, *, tolerance: float, path: str = "") -> list[str]:
    """Paths where two JSON reports differ (numbers within ``tolerance`` count as equal)."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        problems = []
        for key in sorted(set(expected) | set(actual)):
            if key in {"libraries", "trained_at", "git_commit"}:
                continue
            if key not in expected or key not in actual:
                problems.append(f"{path}/{key}: present in only one report")
                continue
            problems += differences(
                expected[key], actual[key], tolerance=tolerance, path=f"{path}/{key}"
            )
        return problems
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: {len(actual)} items, expected {len(expected)}"]
        return [
            p
            for i, (e, a) in enumerate(zip(expected, actual, strict=True))
            for p in differences(e, a, tolerance=tolerance, path=f"{path}[{i}]")
        ]
    if isinstance(expected, bool) or isinstance(actual, bool):
        return [] if expected == actual else [f"{path}: {actual} != {expected}"]
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return [] if abs(expected - actual) <= tolerance else [f"{path}: {actual} != {expected}"]
    return [] if expected == actual else [f"{path}: {actual!r} != {expected!r}"]


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    header = "| " + " | ".join(label for _, label in columns) + " |"
    text = {"band", "feature", "guidance", "expected", "agrees", "group", "attribute", "proxy_for"}
    rule = "|" + "|".join("---" if key in text else "---:" for key, _ in columns) + "|"
    lines = [header, rule]
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            if isinstance(value, float):
                cells.append(f"{value:.4f}")
            elif isinstance(value, bool):
                cells.append("yes" if value else "**no**")
            else:
                cells.append("—" if value is None else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    m = report["model"]
    threshold = report["operating_threshold"]
    cm = report["confusion_matrix_test"]
    lines = [
        f"# Evaluation — {m['name']} {m['version']} ({m['algorithm']})",
        "",
        "> Synthetic data: these figures measure how well the model recovers the simulated "
        "process, not real-world performance. The model is experimental and advisory; it never "
        "decides an application. Generated by `python -m credify_ml.evaluate`.",
        "",
        f"Dataset `{report['dataset']['content_sha256'][:16]}…` "
        f"({report['dataset']['rows']:,} rows, seed {report['dataset']['seed']}). "
        f"{report['trained_on']}.",
        "",
        "## Test-set metrics (scored once)",
        "",
        "| Model | ROC-AUC | PR-AUC | Brier | Log loss | Mean predicted | Observed rate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    rows = [(name, values) for name, values in report["test_metrics"].items()]
    rows += [("prior baseline", report["test_prior_baseline"]),
             ("*DGP oracle (ceiling, not a model)*", report["test_dgp_oracle"])]  # fmt: skip
    for name, v in rows:
        lines.append(
            f"| {name} | {v['roc_auc']:.4f} | {v['pr_auc']:.4f} | {v['brier']:.4f} | "
            f"{v['log_loss']:.4f} | {v['mean_predicted']:.4f} | {v['observed_rate']:.4f} |"
        )
    lines += [
        "",
        f"Served model: **{m['algorithm']}**. Gradient boosting was not materially better on "
        "validation (approved decision D4), and the test set confirms the ordering.",
        "",
        "## Calibration",
        "",
        f"Expected calibration error: validation {report['calibration']['validation']['ece']:.4f}, "
        f"test {report['calibration']['test']['ece']:.4f}. {report['calibration']['rule']}. "
        + (
            f"Applied: Platt slope {report['calibration']['platt']['slope']}, intercept "
            f"{report['calibration']['platt']['intercept']} (uncalibrated validation ECE "
            f"{report['calibration']['uncalibrated_validation_ece']:.4f})."
            if report["calibration"]["applied"]
            else "Not needed: the model was already well calibrated."
        ),
        "",
        *_table(
            report["calibration"]["test"]["bins"],
            [
                ("bin", "Bin (test)"),
                ("share", "Share"),
                ("mean_predicted", "Mean predicted"),
                ("observed_rate", "Observed"),
            ],
        ),
        "",
        "## Operating threshold",
        "",
        f"**{threshold['threshold']:.2f}** — {threshold['rule']}. On test it flags "
        f"{threshold['test']['flagged_rate']:.1%} of applications and finds "
        f"{threshold['test']['recall']:.1%} of defaulters (precision "
        f"{threshold['test']['precision']:.1%}). Flagged applications are routed for careful "
        "human review; the model never declines anyone.",
        "",
        f"Confusion matrix (test, threshold {cm['threshold']:.2f}): {cm['true_positive']} true "
        f"positives, {cm['false_positive']} false positives, {cm['false_negative']} false "
        f"negatives, {cm['true_negative']} true negatives.",
        "",
        *_table(
            report["threshold_table"]["test"],
            [
                ("threshold", "Threshold (test)"),
                ("flagged_rate", "Flagged"),
                ("precision", "Precision"),
                ("recall", "Recall"),
                ("f1", "F1"),
            ],
        ),
        "",
        "## Risk bands",
        "",
        f"Cut-offs {report['bands']['cuts']} (provisional {report['bands']['provisional_cuts']}). "
        f"{report['bands']['rule']}.",
        "",
        *_table(
            report["bands"]["test"],
            [
                ("band", "Band (test)"),
                ("pd_from", "PD from"),
                ("pd_to", "PD to"),
                ("share", "Share"),
                ("mean_pd", "Mean PD"),
                ("default_rate", "Default rate"),
                ("guidance", "Guidance"),
            ],
        ),
        "",
        "## Score distribution (test)",
        "",
        "| Outcome | p5 | p25 | p50 | p75 | p95 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for outcome, q in report["score_distribution_test"].items():
        lines.append(
            f"| {outcome} | {q['p5']:.4f} | {q['p25']:.4f} | {q['p50']:.4f} | {q['p75']:.4f} | "
            f"{q['p95']:.4f} |"
        )
    agreement = report["learned_vs_true"]
    agreed = sum(row["agrees"] for row in agreement)
    lines += [
        "",
        "## Learned versus true effects",
        "",
        f"{agreed} of {len(agreement)} effects have the direction the generating process "
        "intended. Correlated features (for example gross income, net income and residual "
        "income) can share or swap credit for the same signal, so a disagreement is not "
        "necessarily an error; it is reported rather than hidden.",
        "",
        *_table(agreement, [("feature", "Effect"), ("expected", "Expected"),
                            ("coefficient", "Learned (log-odds)"), ("agrees", "Agrees")]),
        "",
    ]  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the chosen model and package it.")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--artefacts", type=Path, default=ARTEFACTS_DIR)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Re-evaluate and compare with the committed report instead of overwriting it.",
    )
    args = parser.parse_args(argv)
    report = evaluate(args.data)
    if args.check:
        report.pop("_model")
        report.pop("_explainer")
        committed = args.reports / f"{MODEL_NAME}-{MODEL_VERSION}" / "evaluation.json"
        expected = json.loads(committed.read_text(encoding="utf-8"))
        problems = differences(expected, json.loads(json.dumps(report)), tolerance=CHECK_TOLERANCE)
        for problem in problems:
            print(f"MISMATCH {problem}")
        print("Reproducible." if not problems else f"{len(problems)} difference(s).")
        return 1 if problems else 0
    paths = write(report, args.artefacts, args.reports)
    print((paths["report"].with_suffix(".md")).read_text(encoding="utf-8"))
    print(f"Model artefact: {paths['model']}")
    print(f"Metadata:       {paths['metadata']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
