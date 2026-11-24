"""Synthetic borrower generator (docs/ML_STRATEGY.md §2).

    python -m credify_ml.generate [--rows 50000] [--seed 20261118] [--out data]

Four stages:

1. **Latent context (audit only):** a synthetic ``sex`` and ``age_band``. They shape income and
   employment realistically (including a deliberate income gap) and are written to a
   *separate* file for fairness auditing. They are never model inputs.
2. **Observable profile:** employment, income, expenses, credit history and the loan request,
   drawn from documented distributions.
3. **Derived features:** each borrower goes through the production affordability calculator
   and the shared feature builder (``app.modules.credit_risk.features``), exactly as a real
   application would.
4. **Outcome:** ``defaulted_12m`` from the documented process in :mod:`credify_ml.dgp`.

The same seed and row count always produce the same data, byte for byte in the canonical
form hashed by :func:`content_hash`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from app.core.money import Currency
from app.modules.affordability.domain.calculator import (
    AffordabilityInput,
    CommitmentItem,
    ExpenseItem,
    IncomeItem,
    Thresholds,
    assess,
)
from app.modules.applicants.domain.enums import (
    CommitmentType,
    EmploymentType,
    ExpenseCategory,
    IncomeFrequency,
)
from app.modules.applications.domain.enums import LoanPurpose
from app.modules.credit_risk import features as shared
from app.modules.loans.engine.amortisation import LoanTerms

from credify_ml import dgp
from credify_ml.config import (
    AUDIT_FILE,
    BORROWERS_FILE,
    DATA_DIR,
    DEFAULT_ROWS,
    DEFAULT_SEED,
    GENERATOR_VERSION,
    ID,
    LABEL,
    MANIFEST_FILE,
    PRODUCT_CODE,
    PRODUCT_TERMS,
    SCHEDULE_START,
)

SEXES = ("F", "M")
AGE_BANDS = ("18-24", "25-34", "35-44", "45-54", "55-64", "65+")
AGE_BAND_P = (0.12, 0.28, 0.25, 0.18, 0.12, 0.05)
AGE_BAND_RANGE = {"18-24": (18, 25), "25-34": (25, 35), "35-44": (35, 45),
                  "45-54": (45, 55), "55-64": (55, 65), "65+": (65, 80)}  # fmt: skip
AGE_INCOME = {"18-24": 0.70, "25-34": 0.95, "35-44": 1.10, "45-54": 1.15, "55-64": 1.05,
              "65+": 0.90}  # fmt: skip
FEMALE_INCOME_FACTOR = 0.88  # the deliberately injected income gap (documented)

EMPLOYMENT = tuple(e.value for e in EmploymentType)
_EMPLOYMENT_P = {
    "default": (0.58, 0.14, 0.10, 0.10, 0.04, 0.04),
    "18-24": (0.40, 0.22, 0.05, 0.24, 0.09, 0.00),
    "55-64": (0.52, 0.10, 0.14, 0.10, 0.04, 0.10),
    "65+": (0.15, 0.03, 0.12, 0.12, 0.03, 0.55),
}
# (median gross monthly income, log-normal sigma, tenure gamma shape, tenure gamma scale)
_EMPLOYMENT_PROFILE = {
    "PERMANENT": (26_000, 0.45, 2.0, 30.0),
    "CONTRACT": (22_000, 0.45, 2.0, 9.0),
    "SELF_EMPLOYED": (28_000, 0.70, 1.5, 32.0),
    "PART_TIME": (9_000, 0.45, 1.5, 13.0),
    "UNEMPLOYED": (2_500, 0.30, 0.0, 0.0),
    "RETIRED": (12_000, 0.45, 0.0, 0.0),
}
TERMS = (6, 12, 18, 24, 36, 48, 60)
TERM_P = (0.04, 0.14, 0.10, 0.24, 0.22, 0.12, 0.14)
PURPOSES = tuple(p.value for p in LoanPurpose)
PURPOSE_P = (0.30, 0.15, 0.08, 0.07, 0.10, 0.05, 0.20, 0.05)

D = Decimal
CENT = D("0.01")


@dataclass(frozen=True)
class Dataset:
    borrowers: pd.DataFrame  # id, model features, the DGP probability and the label
    audit: pd.DataFrame  # id, sex, age_band: fairness auditing only, never a feature
    manifest: dict[str, Any]


def _beta(rng: np.random.Generator, mean: np.ndarray, concentration: float) -> np.ndarray:
    mean = np.clip(mean, 1e-3, 1 - 1e-3)
    return rng.beta(mean * concentration, (1 - mean) * concentration)


def _cents(values: np.ndarray) -> list[Decimal]:
    return [D(f"{v:.2f}") for v in values]


def _profiles(rows: int, rng: np.random.Generator) -> pd.DataFrame:
    """Stages 1 and 2: latent context, then the observable profile and loan request."""
    sex = rng.choice(SEXES, size=rows)
    age_band = rng.choice(AGE_BANDS, size=rows, p=AGE_BAND_P)
    low = np.array([AGE_BAND_RANGE[b][0] for b in age_band])
    high = np.array([AGE_BAND_RANGE[b][1] for b in age_band])
    age = low + rng.random(rows) * (high - low)

    employment = np.array(
        [
            rng.choice(EMPLOYMENT, p=_EMPLOYMENT_P.get(band, _EMPLOYMENT_P["default"]))
            for band in age_band
        ]
    )
    profile = np.array([_EMPLOYMENT_PROFILE[e] for e in employment], dtype=float)
    median, sigma, shape, scale = profile.T
    tenure = np.where(shape > 0, rng.gamma(np.maximum(shape, 1e-9), np.maximum(scale, 1e-9)), 0.0)
    tenure = np.floor(np.minimum(tenure, (age - 18) * 12)).astype(int)

    multiplier = np.array([AGE_INCOME[b] for b in age_band]) * np.where(
        sex == "F", FEMALE_INCOME_FACTOR, 1.0
    )
    gross = np.clip(np.exp(rng.normal(np.log(median * multiplier), sigma)), 1_500, 400_000)
    deduction_mean = 0.12 + 0.18 / (1 + np.exp(-(np.log(gross) - np.log(30_000)) / 0.6))
    net = gross * (1 - _beta(rng, deduction_mean, 40))
    declared = net * _beta(rng, np.full(rows, 0.45), 20)

    accounts = np.minimum(
        rng.poisson(np.maximum(0.2, 0.6 + 0.5 * np.log(gross / 5_000))), 15
    ).astype(int)
    debt_share = _beta(rng, np.minimum(0.06 + 0.02 * accounts, 0.30), 15)
    debt = np.where(accounts > 0, gross * debt_share, 0.0)
    revolving = (accounts > 0) & (rng.random(rows) < 0.7)
    utilisation = rng.beta(2, 3, size=rows)
    over_limit = rng.random(rows) < 0.03
    utilisation = np.where(over_limit, 1 + rng.random(rows) * 0.2, utilisation)
    utilisation = np.where(revolving, np.round(utilisation, 4), np.nan)

    p_missed = 0.12 + 0.2 * np.where(revolving, utilisation, 0.2)
    missed = np.where(rng.random(rows) < p_missed, 1 + rng.poisson(0.8, size=rows), 0)
    missed = np.minimum(missed, 12).astype(int)
    old_delinquency = rng.random(rows) < 0.18
    since = np.where(
        missed > 0,
        rng.integers(0, 12, size=rows),
        np.where(old_delinquency, rng.integers(12, 97, size=rows), -1),
    )

    amount = np.exp(rng.normal(np.log(1.6 * gross), 0.55))
    amount = np.clip(np.round(amount / 500) * 500, 5_000, 150_000)
    term = rng.choice(TERMS, size=rows, p=TERM_P)
    purpose = rng.choice(PURPOSES, size=rows, p=PURPOSE_P)

    return pd.DataFrame(
        {
            "sex": sex,
            "age_band": age_band,
            "employment_type": employment,
            "employment_tenure_months": tenure,
            "gross": _cents(gross),
            "net": _cents(net),
            "declared_expenses": _cents(declared),
            "existing_debt": _cents(debt),
            "num_credit_accounts": accounts,
            "revolving_utilisation": pd.Series(
                [None if np.isnan(u) else D(f"{u:.4f}") for u in utilisation], dtype="object"
            ),
            "missed_payments_12m": missed,
            # object dtype keeps None as None (a numeric column would turn it into NaN)
            "months_since_delinquency": pd.Series(
                [None if s < 0 else int(s) for s in since], dtype="object"
            ),
            "requested_amount": _cents(amount),
            "term_months": term.astype(int),
            "loan_purpose": purpose,
        }
    )


_THRESHOLDS = Thresholds(
    max_dti=PRODUCT_TERMS["max_dti"],
    warn_dti=PRODUCT_TERMS["warn_dti"],
    min_residual_amount=PRODUCT_TERMS["min_residual_amount"],
    min_residual_rate=PRODUCT_TERMS["min_residual_rate"],
)


def _features(row: Any) -> dict[str, shared.FeatureValue]:
    """Stage 3 for one borrower: the production calculator, then the shared builder."""
    zar = Currency.ZAR
    commitments = (
        (CommitmentItem("debt", CommitmentType.OTHER, row.existing_debt, zar),)
        if row.existing_debt > 0
        else ()
    )
    result = assess(
        AffordabilityInput(
            currency=zar,
            loan=LoanTerms(
                amount=row.requested_amount,
                annual_interest_rate=PRODUCT_TERMS["annual_interest_rate"],
                term_months=int(row.term_months),
                initiation_fee=PRODUCT_TERMS["initiation_fee"],
                monthly_service_fee=PRODUCT_TERMS["monthly_service_fee"],
            ),
            thresholds=_THRESHOLDS,
            as_of=SCHEDULE_START,
            incomes=(IncomeItem("income", IncomeFrequency.MONTHLY, row.gross, row.net, zar),),
            expenses=(ExpenseItem("expenses", ExpenseCategory.OTHER, row.declared_expenses, zar),),
            commitments=commitments,
        )
    )
    return shared.build_features(
        shared.RiskInput.from_affordability(
            result,
            employment_type=EmploymentType(row.employment_type),
            employment_tenure_months=int(row.employment_tenure_months),
            num_credit_accounts=int(row.num_credit_accounts),
            revolving_utilisation=row.revolving_utilisation,
            missed_payments_12m=int(row.missed_payments_12m),
            months_since_delinquency=row.months_since_delinquency,
            requested_amount=row.requested_amount,
            term_months=int(row.term_months),
            loan_purpose=LoanPurpose(row.loan_purpose),
        )
    )


def feature_frame(records: list[dict[str, shared.FeatureValue]]) -> pd.DataFrame:
    """Rows from the shared builder → a frame with stable column order and dtypes."""
    frame = pd.DataFrame.from_records(records, columns=list(shared.FEATURE_NAMES))
    for name in shared.NUMERIC:
        frame[name] = pd.to_numeric(frame[name]).astype("float64")
    for name in shared.CATEGORICAL:
        frame[name] = frame[name].astype("string")
    return frame


def generate(rows: int = DEFAULT_ROWS, seed: int = DEFAULT_SEED) -> Dataset:
    if rows < 10:
        raise ValueError("generate at least 10 rows")
    rng = np.random.default_rng(seed)
    profiles = _profiles(rows, rng)
    features = feature_frame([_features(row) for row in profiles.itertuples(index=False)])
    probability, label = dgp.sample_outcomes(features, rng)

    ids = [f"SYN-{i:06d}" for i in range(1, rows + 1)]
    borrowers = pd.concat(
        [
            pd.DataFrame({ID: pd.Series(ids, dtype="string")}),
            features,
            pd.DataFrame({"dgp_probability": np.round(probability, 6), LABEL: label}),
        ],
        axis=1,
    )
    audit = pd.DataFrame(
        {
            ID: pd.Series(ids, dtype="string"),
            "sex": profiles["sex"].astype("string"),
            "age_band": profiles["age_band"].astype("string"),
        }
    )
    return Dataset(borrowers, audit, _manifest(borrowers, audit, rows, seed))


def content_hash(frame: pd.DataFrame) -> str:
    """SHA-256 of a canonical CSV rendering, independent of the Parquet encoder."""
    text = frame.to_csv(index=False, float_format="%.6f", lineterminator="\n", na_rep="")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _column_stats(frame: pd.DataFrame) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for name in frame.columns:
        column = frame[name]
        if name == ID:
            continue
        if pd.api.types.is_numeric_dtype(column):
            stats[name] = {
                "missing": int(column.isna().sum()),
                "min": round(float(column.min()), 6),
                "max": round(float(column.max()), 6),
                "mean": round(float(column.mean()), 6),
            }
        else:
            stats[name] = {
                "missing": int(column.isna().sum()),
                "counts": {str(k): int(v) for k, v in column.value_counts().sort_index().items()},
            }
    return stats


def _manifest(borrowers: pd.DataFrame, audit: pd.DataFrame, rows: int, seed: int) -> dict[str, Any]:
    return {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "rows": rows,
        "default_rate": round(float(borrowers[LABEL].mean()), 6),
        "content_sha256": {
            "borrowers": content_hash(borrowers),
            "audit_attributes": content_hash(audit),
        },
        "feature_schema": shared.schema(),
        "label": {
            "name": LABEL,
            "meaning": "simulated 90+ days past due within 12 months of disbursement",
        },
        "columns_not_for_training": [ID, "dgp_probability", LABEL],
        "audit_attributes": {
            "file": AUDIT_FILE,
            "columns": ["sex", "age_band"],
            "use": "fairness auditing only; never a model feature",
            "injected_effects": {
                "female_income_factor": FEMALE_INCOME_FACTOR,
                "age_income_multipliers": AGE_INCOME,
                "employment_mix_by_age_band": "see credify_ml.generate._EMPLOYMENT_P",
            },
        },
        "product_terms": {"code": PRODUCT_CODE, **{k: str(v) for k, v in PRODUCT_TERMS.items()}},
        "schedule_start": SCHEDULE_START.isoformat(),
        "dgp": dgp.describe(),
        "column_stats": _column_stats(borrowers),
        "libraries": {"numpy": np.__version__, "pandas": pd.__version__},
    }


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(dataset: Dataset, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    borrowers_path = out_dir / BORROWERS_FILE
    audit_path = out_dir / AUDIT_FILE
    dataset.borrowers.to_parquet(borrowers_path, index=False)
    dataset.audit.to_parquet(audit_path, index=False)
    manifest = {
        **dataset.manifest,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": {
            BORROWERS_FILE: _file_sha256(borrowers_path),
            AUDIT_FILE: _file_sha256(audit_path),
        },
    }
    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic credit dataset.")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    dataset = generate(args.rows, args.seed)
    path = write(dataset, args.out)
    manifest = dataset.manifest
    print(
        f"Generated {manifest['rows']} synthetic borrowers (seed {manifest['seed']}, "
        f"{GENERATOR_VERSION}); default rate {manifest['default_rate']:.2%}."
    )
    print(f"Content SHA-256: {manifest['content_sha256']['borrowers']}")
    print(f"Manifest: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
