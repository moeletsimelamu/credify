"""Exact per-prediction explanations for logistic regression (docs/ML_STRATEGY.md §6).

For a pipeline ``preprocess → LogisticRegression`` the log-odds are linear in the
transformed features ``z``::

    logit(x) = b + Σ_k w_k · z_k
             = (b + Σ_k w_k · z̄_k)  +  Σ_k w_k · (z_k − z̄_k)
               └── base log-odds ──┘     └─ contributions ─┘

where ``z̄`` is the mean transformed vector of the training data. A feature's contribution is
the sum over its transformed columns (a one-hot category or a missing-value indicator adds
columns), so the base plus every contribution reproduces the model's log-odds exactly.

Contributions describe what *the model* does, not what causes default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.modules.credit_risk.features import (
    CATEGORICAL,
    FEATURE_NAMES,
    FEATURES,
    FeatureKind,
    FeatureValue,
)

EXPLAINER_KIND = "linear_exact"

LABELS = {
    "gross_monthly_income": "Gross monthly income",
    "net_monthly_income": "Net monthly income",
    "employment_type": "Employment type",
    "employment_tenure_months": "Months in current employment",
    "living_expenses_applied": "Living expenses",
    "existing_debt_instalments": "Existing debt instalments",
    "num_credit_accounts": "Open credit accounts",
    "revolving_utilisation": "Revolving credit utilisation",
    "missed_payments_12m": "Missed payments (12 months)",
    "months_since_delinquency": "Months since last delinquency",
    "requested_amount": "Requested amount",
    "term_months": "Term (months)",
    "loan_purpose": "Loan purpose",
    "debt_to_income": "Debt-to-income",
    "repayment_burden": "Repayment burden",
    "residual_income": "Residual income",
    "loan_to_income": "Loan-to-income",
}
_KINDS = {spec.name: spec.kind for spec in FEATURES}
CAVEAT = (
    "Contributions show how this experimental model weighs each factor relative to an average "
    "applicant. They describe the model, not the causes of default, and the model is advisory."
)


class ExplanationError(RuntimeError):
    """The explanation would not reproduce the model's own output."""


def _original_feature(transformed: str) -> str:
    name = transformed.split("__", 1)[-1].removeprefix("missingindicator_")
    if name in FEATURE_NAMES:
        return name
    for categorical in CATEGORICAL:
        if name.startswith(categorical + "_"):
            return categorical
    raise ValueError(f"cannot map transformed column {transformed} to a feature")


def build_linear_explainer(model: Any, train_features: pd.DataFrame) -> dict[str, Any]:
    """Everything needed to explain ``model`` later, recorded at training time."""
    preprocess, estimator = model[:-1], model[-1]
    names = [str(n) for n in preprocess.get_feature_names_out()]
    baseline = np.asarray(preprocess.transform(train_features), dtype=float).mean(axis=0)
    coefficients = np.asarray(estimator.coef_, dtype=float).ravel()
    intercept = float(np.asarray(estimator.intercept_, dtype=float)[0])
    base = intercept + float(coefficients @ baseline)
    return {
        "kind": EXPLAINER_KIND,
        "space": "log-odds; contribution of feature j = Σ coef_k × (z_k − z̄_k) over its columns",
        "transformed_features": names,
        "feature_of": [_original_feature(n) for n in names],
        "coefficients": [float(c) for c in coefficients],
        "baseline_means": [float(b) for b in baseline],
        "intercept": intercept,
        "base_log_odds": base,
        "base_probability": float(1 / (1 + np.exp(-base))),
    }


@dataclass(frozen=True, slots=True)
class Contribution:
    feature: str
    label: str
    value: FeatureValue
    log_odds: float

    @property
    def direction(self) -> str:
        if self.log_odds > 0:
            return "increases"
        return "decreases" if self.log_odds < 0 else "neutral"

    def text(self) -> str:
        return (
            f"{self.label} {_display(self.feature, self.value)} — {self.direction} risk "
            f"({self.log_odds:+.2f} log-odds)"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "label": self.label,
            "value": self.value,
            "display_value": _display(self.feature, self.value),
            "log_odds": round(self.log_odds, 6),
            "direction": self.direction,
            "text": self.text(),
        }


def _display(feature: str, value: FeatureValue) -> str:
    if value is None:
        return "none on record"
    if isinstance(value, str):
        return value.replace("_", " ").lower()
    kind = _KINDS[feature]
    if kind in (FeatureKind.MONEY, FeatureKind.SIGNED_MONEY):
        return f"{value:,.2f}"
    if kind is FeatureKind.RATIO:
        return f"{value:.4f}"
    return f"{value:.0f}"


def feature_frame(features: dict[str, FeatureValue]) -> pd.DataFrame:
    """One application's features as the one-row frame the pipeline expects.

    Dtypes match training: numeric columns are float64 (missing → NaN), categories strings.
    """
    row = pd.DataFrame([features], columns=list(FEATURE_NAMES))
    for name in FEATURE_NAMES:
        if name in CATEGORICAL:
            row[name] = row[name].astype("string")
        else:
            row[name] = pd.to_numeric(row[name]).astype("float64")
    return row


def explain(
    model: Any, explainer: dict[str, Any], features: dict[str, FeatureValue]
) -> dict[str, Any]:
    """Ranked contributions for one application. Raises if they don't add up exactly."""
    if explainer.get("kind") != EXPLAINER_KIND:
        raise ExplanationError(f"unsupported explainer {explainer.get('kind')!r}")
    row = feature_frame(features)
    z = np.asarray(model[:-1].transform(row), dtype=float).ravel()
    coefficients = np.asarray(explainer["coefficients"], dtype=float)
    baseline = np.asarray(explainer["baseline_means"], dtype=float)
    if len(z) != len(coefficients):
        raise ExplanationError("the explainer does not match the model's transformed features")
    parts = coefficients * (z - baseline)
    totals: dict[str, float] = dict.fromkeys(FEATURE_NAMES, 0.0)
    for feature, part in zip(explainer["feature_of"], parts, strict=True):
        totals[feature] += float(part)

    log_odds = float(np.asarray(model.decision_function(row), dtype=float).ravel()[0])
    reconstructed = float(explainer["base_log_odds"]) + float(parts.sum())
    if abs(reconstructed - log_odds) > 1e-6:
        raise ExplanationError(
            f"contributions sum to {reconstructed:.8f} but the model gives {log_odds:.8f}"
        )
    ranked = sorted(
        (Contribution(f, LABELS[f], features[f], v) for f, v in totals.items()),
        key=lambda c: abs(c.log_odds),
        reverse=True,
    )
    return {
        "kind": EXPLAINER_KIND,
        "base_log_odds": round(float(explainer["base_log_odds"]), 6),
        "base_probability": round(float(explainer["base_probability"]), 6),
        "log_odds": round(log_odds, 6),
        "contributions": [c.as_dict() for c in ranked],
        "top_increasing": [c.text() for c in ranked if c.log_odds > 0][:3],
        "top_decreasing": [c.text() for c in ranked if c.log_odds < 0][:3],
        "caveat": CAVEAT,
    }
