"""Model pipelines and their tuning grids (docs/ML_STRATEGY.md §4).

Preprocessing is part of every fitted pipeline, so the artefact the API loads in Phase 7
transforms raw features from the shared builder exactly as during training.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from app.modules.credit_risk.features import FEATURES, FeatureKind
from app.modules.credit_risk.transforms import signed_log
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, OrdinalEncoder, StandardScaler

MONEY = [s.name for s in FEATURES if s.kind is FeatureKind.MONEY]
SIGNED_MONEY = [s.name for s in FEATURES if s.kind is FeatureKind.SIGNED_MONEY]
PLAIN = [s.name for s in FEATURES if s.kind in (FeatureKind.COUNT, FeatureKind.RATIO)]
CATEGORICAL = [s.name for s in FEATURES if s.kind is FeatureKind.CATEGORY]
CATEGORIES = [list(s.categories) for s in FEATURES if s.kind is FeatureKind.CATEGORY]
NUMERIC = MONEY + SIGNED_MONEY + PLAIN


def linear_preprocessor() -> ColumnTransformer:
    """Impute (with missing-value indicators), log-transform money, scale, one-hot encode."""
    return ColumnTransformer(
        [
            (
                "money",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("log", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
                        ("scale", StandardScaler()),
                    ]
                ),
                MONEY,
            ),
            (
                "signed_money",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("log", FunctionTransformer(signed_log, feature_names_out="one-to-one")),
                        ("scale", StandardScaler()),
                    ]
                ),
                SIGNED_MONEY,
            ),
            (
                "plain",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                PLAIN,
            ),
            (
                "categorical",
                OneHotEncoder(categories=CATEGORIES, handle_unknown="ignore", drop=None),
                CATEGORICAL,
            ),
        ],
        verbose_feature_names_out=True,
    )


def tree_preprocessor() -> ColumnTransformer:
    """Trees need no scaling and handle missing values natively; categories become codes."""
    return ColumnTransformer(
        [
            ("numeric", "passthrough", NUMERIC),
            (
                "categorical",
                OrdinalEncoder(
                    categories=CATEGORIES, handle_unknown="use_encoded_value", unknown_value=-1
                ),
                CATEGORICAL,
            ),
        ],
        verbose_feature_names_out=False,
    )


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    pipeline: Pipeline
    grid: dict[str, list[Any]] = field(default_factory=dict)


def candidates(seed: int, *, quick: bool = False) -> list[Candidate]:
    """The models compared in Phase 6. ``quick`` shrinks the grids for tests."""
    lr_grid: dict[str, list[Any]] = {
        "model__C": [0.001, 0.003, 0.01, 0.03, 0.1, 1.0],
        "model__class_weight": [None, "balanced"],
    }
    gbm_grid: dict[str, list[Any]] = {
        "model__learning_rate": [0.05, 0.1],
        "model__max_leaf_nodes": [15, 31],
        "model__l2_regularization": [0.0, 1.0],
    }
    if quick:
        lr_grid = {"model__C": [0.1, 1.0], "model__class_weight": [None]}
        gbm_grid = {"model__learning_rate": [0.1], "model__max_leaf_nodes": [15]}
    categorical_codes = list(range(len(NUMERIC), len(NUMERIC) + len(CATEGORICAL)))
    return [
        Candidate(
            "baseline",
            "Prior-rate model: predicts the training default rate for everyone (sanity floor)",
            Pipeline(
                [("features", tree_preprocessor()), ("model", DummyClassifier(strategy="prior"))]
            ),
        ),
        Candidate(
            "logistic_regression",
            "L2-regularised logistic regression on imputed, log-transformed, scaled features",
            Pipeline(
                [
                    ("features", linear_preprocessor()),
                    ("model", LogisticRegression(max_iter=5000, solver="lbfgs")),
                ]
            ),
            lr_grid,
        ),
        Candidate(
            "hist_gradient_boosting",
            "Histogram gradient boosting with native missing-value and categorical handling",
            Pipeline(
                [
                    ("features", tree_preprocessor()),
                    (
                        "model",
                        HistGradientBoostingClassifier(
                            max_iter=500,
                            early_stopping=True,
                            validation_fraction=0.1,
                            n_iter_no_change=20,
                            min_samples_leaf=40,
                            categorical_features=categorical_codes,
                            random_state=seed,
                        ),
                    ),
                ]
            ),
            gbm_grid,
        ),
    ]
