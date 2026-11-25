"""Fairness and bias evaluation (docs/ML_STRATEGY.md §8).

    python -m credify_ml.fairness

Uses the audit-only synthetic attributes (``sex``, ``age_band``), which are never model
inputs, joined to the **test** split and the packaged model. Reports per-group outcomes,
disparate-impact ratios (the four-fifths rule as a heuristic, not a legal test), equal-
opportunity and false-positive-rate gaps, calibration within groups, a proxy check on the
features, and two mitigation analyses. Nothing here "solves" bias; it measures it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from app.modules.credit_risk import features as shared
from sklearn.base import clone
from sklearn.metrics import roc_auc_score

from credify_ml.config import (
    ARTEFACTS_DIR,
    AUDIT_FILE,
    DATA_DIR,
    ID,
    LABEL,
    MODEL_NAME,
    MODEL_VERSION,
    REPORTS_DIR,
)
from credify_ml.evaluate import CHECK_TOLERANCE, _table, band_of, differences
from credify_ml.train import load_dataset, split

ATTRIBUTES = ("sex", "age_band")
FOUR_FIFTHS = 0.8
# Proxy flags: a standardised mean difference of 0.3 (binary attribute) or a correlation
# ratio η² of 0.06 (multi-level attribute), conventional "medium" effect sizes.
PROXY_SMD = 0.3
PROXY_ETA2 = 0.06
REVIEW_THRESHOLDS = (0.10, 0.13, 0.15, 0.20)
MIN_GROUP_SIZE = 100


def _r(value: float, places: int = 4) -> float | None:
    return None if value is None or np.isnan(value) else round(float(value), places)


def group_metrics(
    frame: pd.DataFrame, attribute: str, threshold: float, cuts: list[float]
) -> list[dict[str, Any]]:
    rows = []
    for group, part in frame.groupby(attribute, observed=True):
        y, p = part[LABEL].to_numpy(), part["pd"].to_numpy()
        flagged = p >= threshold
        elevated = np.isin(band_of(p, tuple(cuts)), ["D", "E"])
        positives, negatives = y == 1, y == 0
        rows.append(
            {
                "attribute": attribute,
                "group": str(group),
                "borrowers": len(part),
                "observed_default_rate": _r(y.mean()),
                "mean_pd": _r(p.mean()),
                "calibration_gap": _r(p.mean() - y.mean()),
                "flagged_rate": _r(flagged.mean()),
                "elevated_band_rate": _r(elevated.mean()),
                "true_positive_rate": _r(flagged[positives].mean()) if positives.any() else None,
                "false_positive_rate": _r(flagged[negatives].mean()) if negatives.any() else None,
                "precision": _r(y[flagged].mean()) if flagged.any() else None,
                "roc_auc": _r(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
            }
        )
    return rows


def disparities(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ratios and gaps across the groups of one attribute (groups under 100 rows skipped)."""
    usable = [r for r in rows if r["borrowers"] >= MIN_GROUP_SIZE]
    not_flagged = [1 - r["flagged_rate"] for r in usable]
    not_elevated = [1 - r["elevated_band_rate"] for r in usable]
    tpr = [r["true_positive_rate"] for r in usable if r["true_positive_rate"] is not None]
    fpr = [r["false_positive_rate"] for r in usable if r["false_positive_rate"] is not None]
    di_flag = min(not_flagged) / max(not_flagged)
    di_band = min(not_elevated) / max(not_elevated)
    return {
        "favourable_outcome": "not flagged for careful review (PD below the threshold)",
        "disparate_impact_ratio": _r(di_flag),
        "disparate_impact_ratio_bands": _r(di_band),
        "passes_four_fifths_heuristic": bool(di_flag >= FOUR_FIFTHS and di_band >= FOUR_FIFTHS),
        "equal_opportunity_difference": _r(max(tpr) - min(tpr)),
        "false_positive_rate_difference": _r(max(fpr) - min(fpr)),
        "max_calibration_gap": _r(max(abs(r["calibration_gap"]) for r in usable)),
    }


def proxy_check(train: pd.DataFrame) -> list[dict[str, Any]]:
    """How strongly each numeric feature tracks each audit attribute, on the training split."""
    rows = []
    numeric = [s.name for s in shared.FEATURES if s.kind is not shared.FeatureKind.CATEGORY]
    for feature in numeric:
        values = train[feature]
        mask = values.notna()
        x = values[mask].to_numpy(dtype=float)
        sex = train.loc[mask, "sex"].to_numpy()
        pooled = x.std() or 1.0
        smd = (x[sex == "F"].mean() - x[sex == "M"].mean()) / pooled
        age = train.loc[mask, "age_band"].to_numpy()
        means = {g: x[age == g].mean() for g in np.unique(age)}
        between = sum((age == g).sum() * (m - x.mean()) ** 2 for g, m in means.items())
        eta2 = between / (((x - x.mean()) ** 2).sum() or 1.0)
        rows.append(
            {
                "feature": feature,
                "sex_standardised_mean_difference": _r(smd),
                "age_band_eta_squared": _r(eta2),
                "proxy_for": ", ".join(
                    name
                    for name, strong in (
                        ("sex", abs(smd) >= PROXY_SMD),
                        ("age_band", eta2 >= PROXY_ETA2),
                    )
                    if strong
                )
                or None,
            }
        )
    for feature in shared.CATEGORICAL:
        table = pd.crosstab(train[feature], train["age_band"], normalize="columns")
        spread = float((table.max(axis=1) - table.min(axis=1)).max())
        sex_table = pd.crosstab(train[feature], train["sex"], normalize="columns")
        sex_spread = float((sex_table.max(axis=1) - sex_table.min(axis=1)).max())
        rows.append(
            {
                "feature": feature,
                "sex_max_share_difference": _r(sex_spread),
                "age_band_max_share_difference": _r(spread),
                "proxy_for": ", ".join(
                    name
                    for name, strong in (("sex", sex_spread >= 0.10), ("age_band", spread >= 0.10))
                    if strong
                )
                or None,
            }
        )
    return rows


def threshold_review(frame: pd.DataFrame, attribute: str) -> list[dict[str, Any]]:
    """Mitigation option 1: how the single global threshold moves each group's outcomes."""
    rows = []
    for threshold in REVIEW_THRESHOLDS:
        groups = group_metrics(frame, attribute, threshold, [0.04, 0.07, threshold, 0.25])
        gaps = disparities(groups)
        rows.append(
            {
                "threshold": threshold,
                "disparate_impact_ratio": gaps["disparate_impact_ratio"],
                "equal_opportunity_difference": gaps["equal_opportunity_difference"],
                "flagged_rate_overall": _r((frame["pd"] >= threshold).mean()),
            }
        )
    return rows


def without_proxies(
    model: Any, parts: dict[str, pd.DataFrame], audit: pd.DataFrame, proxies: list[str],
    threshold: float,
) -> dict[str, Any]:  # fmt: skip
    """Mitigation option 2: retrain with the flagged proxy features neutralised (set to the
    training median / most common value) and compare accuracy and disparities."""
    train, test = parts["train"].copy(), parts["test"].copy()
    for feature in proxies:
        fill = (
            train[feature].mode().iloc[0]
            if feature in shared.CATEGORICAL
            else train[feature].median()
        )
        train[feature] = fill
        test[feature] = fill
    cols = list(shared.FEATURE_NAMES)
    refit = clone(model).fit(train[cols], train[LABEL])
    scored = test.merge(audit, on=ID)
    scored["pd"] = refit.predict_proba(scored[cols])[:, 1]
    result: dict[str, Any] = {
        "neutralised_features": proxies,
        "test_roc_auc": _r(roc_auc_score(scored[LABEL], scored["pd"])),
    }
    for attribute in ATTRIBUTES:
        gaps = disparities(
            group_metrics(scored, attribute, threshold, [0.04, 0.07, threshold, 0.25])
        )
        result[attribute] = {
            k: gaps[k] for k in ("disparate_impact_ratio", "equal_opportunity_difference")
        }
    return result


def fairness(data_dir: Path = DATA_DIR, artefacts_dir: Path = ARTEFACTS_DIR) -> dict[str, Any]:
    slug = f"{MODEL_NAME}-{MODEL_VERSION}"
    metadata = json.loads((artefacts_dir / slug / "metadata.json").read_text(encoding="utf-8"))
    model = joblib.load(artefacts_dir / slug / "model.joblib")  # our own artefact, just written
    frame, manifest = load_dataset(data_dir)
    audit = pd.read_parquet(data_dir / AUDIT_FILE)
    parts = split(frame, int(manifest["seed"]))
    threshold = float(metadata["decision_threshold"])
    cuts = list(metadata["band_cutoffs"]["cuts"])
    cols = list(shared.FEATURE_NAMES)

    test = parts["test"].merge(audit, on=ID)
    test["pd"] = model.predict_proba(test[cols])[:, 1]
    report: dict[str, Any] = {
        "model": {"name": MODEL_NAME, "version": MODEL_VERSION},
        "split": "test",
        "threshold": threshold,
        "band_cutoffs": cuts,
        "attributes_are_features": False,
        "groups": {},
        "disparities": {},
    }
    for attribute in ATTRIBUTES:
        rows = group_metrics(test, attribute, threshold, cuts)
        report["groups"][attribute] = rows
        report["disparities"][attribute] = disparities(rows)
    train = parts["train"].merge(audit, on=ID)
    proxies = proxy_check(train)
    report["proxy_check"] = {
        "split": "train",
        "rules": f"|standardised mean difference| ≥ {PROXY_SMD} for sex; η² ≥ {PROXY_ETA2} for "
        "age band; for categorical features, a share difference of 10 points or more",
        "features": proxies,
    }
    flagged = [row["feature"] for row in proxies if row["proxy_for"]]
    report["mitigation"] = {
        "threshold_review": {a: threshold_review(test, a) for a in ATTRIBUTES},
        "neutralise_proxies": without_proxies(model, parts, audit, flagged, threshold)
        if flagged
        else None,
        "not_adopted": "Group-specific thresholds would need the protected attribute at decision "
        "time. Credify never uses it, so that option is out of scope by design.",
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Fairness evaluation — {report['model']['name']} {report['model']['version']}",
        "",
        "> Synthetic data with a **deliberately injected** income gap (the synthetic female group "
        "earns about 12 % less) and an age-dependent income and employment mix. `sex` and "
        "`age_band` are audit-only: the model never sees them. The four-fifths rule is used as a "
        "heuristic, not as a legal test. Generated by `python -m credify_ml.fairness`.",
        "",
        f"Test split, operating threshold {report['threshold']:.2f}, "
        f"bands {report['band_cutoffs']}.",
        "",
    ]
    for attribute in ATTRIBUTES:
        gaps = report["disparities"][attribute]
        lines += [
            f"## By {attribute.replace('_', ' ')}",
            "",
            *_table(
                report["groups"][attribute],
                [
                    ("group", "Group"),
                    ("borrowers", "Borrowers"),
                    ("observed_default_rate", "Observed default"),
                    ("mean_pd", "Mean PD"),
                    ("flagged_rate", "Flagged"),
                    ("elevated_band_rate", "Band D/E"),
                    ("true_positive_rate", "TPR"),
                    ("false_positive_rate", "FPR"),
                    ("precision", "Precision"),
                    ("roc_auc", "ROC-AUC"),
                ],
            ),
            "",
            f"Disparate impact ratio {gaps['disparate_impact_ratio']:.3f} (bands "
            f"{gaps['disparate_impact_ratio_bands']:.3f}); "
            f"{'passes' if gaps['passes_four_fifths_heuristic'] else '**fails**'} the four-fifths "
            f"heuristic. Equal-opportunity difference {gaps['equal_opportunity_difference']:.3f}, "
            f"false-positive-rate difference {gaps['false_positive_rate_difference']:.3f}, largest "
            f"within-group calibration gap {gaps['max_calibration_gap']:.3f}.",
            "",
        ]
    numeric_rows = [r for r in report["proxy_check"]["features"] if "age_band_eta_squared" in r]
    categorical_rows = [
        r for r in report["proxy_check"]["features"] if "age_band_max_share_difference" in r
    ]
    lines += [
        "## Proxy check (training split)",
        "",
        f"{report['proxy_check']['rules']}.",
        "",
        *_table(
            numeric_rows,
            [
                ("feature", "Feature"),
                ("sex_standardised_mean_difference", "Sex SMD"),
                ("age_band_eta_squared", "Age band η²"),
                ("proxy_for", "Proxy for"),
            ],
        ),
        "",
        *_table(
            categorical_rows,
            [
                ("feature", "Feature"),
                ("sex_max_share_difference", "Sex share diff"),
                ("age_band_max_share_difference", "Age band share diff"),
                ("proxy_for", "Proxy for"),
            ],
        ),
        "",
        "## Mitigation analyses (not adopted automatically)",
        "",
        "**1. Threshold review.** One global threshold applies to everyone; moving it changes "
        "workload and group gaps together.",
        "",
    ]
    for attribute in ATTRIBUTES:
        lines += [
            f"*{attribute}*",
            "",
            *_table(
                report["mitigation"]["threshold_review"][attribute],
                [
                    ("threshold", "Threshold"),
                    ("flagged_rate_overall", "Flagged overall"),
                    ("disparate_impact_ratio", "Disparate impact"),
                    ("equal_opportunity_difference", "Equal-opportunity diff"),
                ],
            ),
            "",
        ]
    neutral = report["mitigation"]["neutralise_proxies"]
    if neutral:
        lines += [
            f"**2. Neutralising proxy features** ({', '.join(neutral['neutralised_features'])}): "
            f"test ROC-AUC {neutral['test_roc_auc']:.4f}. Disparate impact by sex "
            f"{neutral['sex']['disparate_impact_ratio']:.3f}, by age band "
            f"{neutral['age_band']['disparate_impact_ratio']:.3f}. Removing a feature that "
            "carries real risk signal costs accuracy and can move disparity elsewhere; the "
            "served model keeps all features, and these figures are recorded for reviewers.",
            "",
        ]
    lines += [f"**3. Not adopted.** {report['mitigation']['not_adopted']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fairness evaluation of the packaged model.")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--artefacts", type=Path, default=ARTEFACTS_DIR)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--check", action="store_true", help="Compare with the committed report.")
    args = parser.parse_args(argv)
    report = fairness(args.data, args.artefacts)
    out = args.reports / f"{MODEL_NAME}-{MODEL_VERSION}"
    if args.check:
        expected = json.loads((out / "fairness.json").read_text(encoding="utf-8"))
        problems = differences(expected, json.loads(json.dumps(report)), tolerance=CHECK_TOLERANCE)
        for problem in problems:
            print(f"MISMATCH {problem}")
        print("Reproducible." if not problems else f"{len(problems)} difference(s).")
        return 1 if problems else 0
    out.mkdir(parents=True, exist_ok=True)
    (out / "fairness.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    (out / "fairness.md").write_text(render_markdown(report), "utf-8")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
