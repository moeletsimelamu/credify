"""A small, real credit-risk model for tests: same structure as the served one (imputation with
missing-value indicators, scaling, one-hot categories, logistic regression), trained in a
second on random but valid feature rows, and packaged with real metadata and explainer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from app.modules.credit_risk import features as shared
from app.modules.credit_risk.explain import build_linear_explainer
from app.modules.credit_risk.transforms import signed_log

SEED = 7
CATEGORIES = [list(s.categories) for s in shared.FEATURES if s.kind is shared.FeatureKind.CATEGORY]


def random_rows(n: int, seed: int = SEED) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    gross = rng.uniform(5_000, 60_000, n)
    net = gross * rng.uniform(0.7, 0.85, n)
    debt = gross * rng.uniform(0, 0.2, n)
    instalment = rng.uniform(300, 4_000, n)
    expenses = net * rng.uniform(0.3, 0.5, n)
    frame = pd.DataFrame(
        {
            "gross_monthly_income": gross.round(2),
            "net_monthly_income": net.round(2),
            "employment_type": rng.choice(shared.FEATURES[2].categories, n),
            "employment_tenure_months": rng.integers(0, 200, n).astype(float),
            "living_expenses_applied": expenses.round(2),
            "existing_debt_instalments": debt.round(2),
            "num_credit_accounts": rng.integers(0, 6, n).astype(float),
            "revolving_utilisation": np.where(rng.random(n) < 0.5, np.nan, rng.uniform(0, 1, n)),
            "missed_payments_12m": rng.integers(0, 4, n).astype(float),
            "months_since_delinquency": np.where(
                rng.random(n) < 0.6, np.nan, rng.integers(0, 60, n)
            ),
            "requested_amount": rng.uniform(5_000, 100_000, n).round(2),
            "term_months": rng.choice([12, 24, 36, 48], n).astype(float),
            "loan_purpose": rng.choice(shared.FEATURES[12].categories, n),
            "debt_to_income": ((debt + instalment) / gross).round(4),
            "repayment_burden": ((debt + instalment) / net).round(4),
            "residual_income": (net - expenses - debt - instalment).round(2),
            "loan_to_income": rng.uniform(0.01, 0.5, n).round(4),
        }
    )
    for name in shared.CATEGORICAL:
        frame[name] = frame[name].astype("string")
    logit = -2 + 3 * (frame["debt_to_income"] - 0.3) + 0.5 * frame["missed_payments_12m"]
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return frame[list(shared.FEATURE_NAMES)], y


def pipeline() -> Pipeline:
    money = [s.name for s in shared.FEATURES if s.kind is shared.FeatureKind.MONEY]
    signed = [s.name for s in shared.FEATURES if s.kind is shared.FeatureKind.SIGNED_MONEY]
    plain = [
        s.name
        for s in shared.FEATURES
        if s.kind in (shared.FeatureKind.COUNT, shared.FeatureKind.RATIO)
    ]
    preprocess = ColumnTransformer(
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
                money,
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
                signed,
            ),
            (
                "plain",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                plain,
            ),
            (
                "categorical",
                OneHotEncoder(categories=CATEGORIES, handle_unknown="ignore"),
                list(shared.CATEGORICAL),
            ),
        ]
    )
    return Pipeline([("features", preprocess), ("model", LogisticRegression(C=1.0, max_iter=2000))])


@dataclass(frozen=True)
class PackagedModel:
    pipeline: Pipeline
    metadata: dict[str, Any]
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def train(version: str = "0.0.1-test", *, threshold: str = "0.13") -> PackagedModel:
    rows, y = random_rows(2_000)
    model = pipeline().fit(rows, y)
    path_free = joblib_bytes(model)
    metadata = {
        "name": "credify-pd",
        "version": version,
        "algorithm": "logistic_regression",
        "artefact_file": "model.joblib",
        "artefact_sha256": hashlib.sha256(path_free).hexdigest(),
        "feature_schema": shared.schema(),
        "dataset": {
            "content_sha256": "0" * 64,
            "generator_version": "test-fixture",
            "seed": SEED,
            "rows": len(rows),
            "default_rate": float(y.mean()),
        },
        "decision_threshold": float(threshold),
        "band_cutoffs": {"cuts": [0.04, 0.07, float(threshold), 0.25], "bands": list("ABCDE")},
        "metrics": {"note": "test fixture"},
        "explainer": build_linear_explainer(model, rows),
        "libraries": {"scikit-learn": sklearn.__version__},
        "git_commit": None,
        "trained_at": "2026-11-20T10:00:00+00:00",
    }
    return PackagedModel(model, json.loads(json.dumps(metadata)), path_free)


def joblib_bytes(model: Any) -> bytes:
    import io

    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    return buffer.getvalue()


def install(packaged: PackagedModel, model_dir: Path) -> str:
    """Write the artefact into the model directory; returns its registry URI."""
    uri = f"credify-pd/{packaged.metadata['version']}/model.joblib"
    target = model_dir / uri
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(packaged.data)
    return uri
