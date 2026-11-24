"""The documented data-generating process for the default outcome (ML_STRATEGY.md §2, stage 4).

``defaulted_12m ~ Bernoulli(sigmoid(β₀ + Σ βᵢ·gᵢ(x) + interaction + ε))``, then a small share
of labels is flipped. Every term below has an intended direction, recorded in the dataset
manifest so Phase 7 can compare what the model learned with what is actually true.

The outcome depends only on model features. Sex and age band have **no direct effect**: any
disparity between groups arises through features they influence (chiefly income), which is
deliberate, so the fairness evaluation has something real to find.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

INTERCEPT = -1.91  # calibrated once (bisection on 30,000 rows) for a default rate of about 12 %
NOISE_SD = 0.6  # ε: unobserved heterogeneity, so the classes are not separable
LABEL_FLIP_RATE = 0.01  # recording noise


@dataclass(frozen=True, slots=True)
class Term:
    name: str
    coefficient: float
    transform: str
    direction: str


TERMS: tuple[Term, ...] = (
    Term("debt_to_income", 2.2, "dti − 0.35", "higher debt-to-income → higher risk"),
    Term("missed_payments_12m", 0.35, "min(missed, 6)", "more missed payments → higher risk"),
    Term(
        "months_since_delinquency",
        0.5,
        "1 if a delinquency in the last 12 months",
        "recent delinquency → higher risk",
    ),
    Term(
        "months_since_delinquency",
        0.2,
        "1 if the last delinquency was 12+ months ago",
        "older delinquency → slightly higher risk",
    ),
    Term(
        "revolving_utilisation",
        1.1,
        "utilisation − 0.4 (no revolving credit: −0.1 instead)",
        "higher utilisation → higher risk",
    ),
    Term(
        "residual_income",
        -0.35,
        "asinh(residual / 5000)",
        "more residual income → lower risk",
    ),
    Term(
        "employment_tenure_months",
        -0.006,
        "min(tenure, 120)",
        "longer tenure → lower risk",
    ),
    Term("loan_to_income", 0.4, "lti − 0.25", "larger loan relative to income → higher risk"),
    Term("term_months", 0.012, "term − 24", "longer term → slightly higher risk"),
    Term("num_credit_accounts", 0.08, "accounts − 2", "more open accounts → higher risk"),
)

EMPLOYMENT_EFFECTS = {
    "PERMANENT": 0.0,
    "CONTRACT": 0.25,
    "SELF_EMPLOYED": 0.35,
    "PART_TIME": 0.30,
    "UNEMPLOYED": 0.90,
    "RETIRED": 0.10,
}
PURPOSE_EFFECTS = {
    "DEBT_CONSOLIDATION": 0.20,
    "BUSINESS": 0.35,
    "MEDICAL": 0.15,
    "EDUCATION": -0.10,
}
INTERACTION = (
    0.6  # extra DTI risk when there are missed payments: 0.6 × (dti − 0.35) × 1[missed > 0]
)


def linear_predictor(features: pd.DataFrame) -> np.ndarray:
    """β₀ + Σ βᵢ·gᵢ(x) + interaction, without noise. Uses model features only."""
    dti = features["debt_to_income"].to_numpy(dtype=float)
    missed = features["missed_payments_12m"].to_numpy(dtype=float)
    since = features["months_since_delinquency"].to_numpy(dtype=float)
    util = features["revolving_utilisation"].to_numpy(dtype=float)
    residual = features["residual_income"].to_numpy(dtype=float)
    tenure = features["employment_tenure_months"].to_numpy(dtype=float)
    lti = features["loan_to_income"].to_numpy(dtype=float)
    term = features["term_months"].to_numpy(dtype=float)
    accounts = features["num_credit_accounts"].to_numpy(dtype=float)

    recent = (~np.isnan(since)) & (since < 12)
    older = (~np.isnan(since)) & (since >= 12)
    utilisation = np.where(np.isnan(util), -0.1, 1.1 * (util - 0.4))
    eta = (
        INTERCEPT
        + 2.2 * (dti - 0.35)
        + 0.35 * np.minimum(missed, 6)
        + 0.5 * recent
        + 0.2 * older
        + utilisation
        - 0.35 * np.arcsinh(residual / 5000)
        - 0.006 * np.minimum(tenure, 120)
        + 0.4 * (lti - 0.25)
        + 0.012 * (term - 24)
        + 0.08 * (accounts - 2)
        + INTERACTION * (dti - 0.35) * (missed > 0)
    )
    eta = eta + features["employment_type"].map(EMPLOYMENT_EFFECTS).to_numpy(dtype=float)
    eta = eta + features["loan_purpose"].map(PURPOSE_EFFECTS).fillna(0.0).to_numpy(dtype=float)
    return np.asarray(eta, dtype=float)


def sample_outcomes(
    features: pd.DataFrame, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """(the probability each borrower defaulted with, the observed 0/1 label)."""
    eta = linear_predictor(features) + rng.normal(0.0, NOISE_SD, size=len(features))
    probability = 1.0 / (1.0 + np.exp(-eta))
    label = rng.random(len(features)) < probability
    flip = rng.random(len(features)) < LABEL_FLIP_RATE
    return probability, np.where(flip, ~label, label).astype(np.int8)


def describe() -> dict[str, object]:
    """The DGP as recorded in the manifest."""
    return {
        "form": "Bernoulli(sigmoid(intercept + Σ coefficient·transform + interaction + ε))",
        "intercept": INTERCEPT,
        "noise_sd": NOISE_SD,
        "label_flip_rate": LABEL_FLIP_RATE,
        "terms": [
            {
                "feature": t.name,
                "coefficient": t.coefficient,
                "transform": t.transform,
                "intended_direction": t.direction,
            }
            for t in TERMS
        ],
        "employment_type_effects": EMPLOYMENT_EFFECTS,
        "loan_purpose_effects": PURPOSE_EFFECTS,
        "interaction": {
            "coefficient": INTERACTION,
            "transform": "(dti − 0.35) × 1[missed_payments_12m > 0]",
            "intended_direction": "missed payments make high DTI riskier",
        },
        "protected_attributes": "no direct effect; they shape income and employment only",
    }
