"""The synthetic generator: reproducible, documented, and blind to protected attributes."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from app.modules.credit_risk import features as shared

from credify_ml import dgp
from credify_ml.config import AUDIT_FILE, BORROWERS_FILE, ID, LABEL, MANIFEST_FILE
from credify_ml.generate import content_hash, generate, main

# Pinned so a change to the generator, the affordability engine or the feature builder that
# alters the data cannot go unnoticed (bump GENERATOR_VERSION and update this on purpose).
GOLDEN_500 = "17f29466c214939ce558cfa2a19bbf9e94e2ed0ecb60470a9507bc764b8516c7"


@pytest.fixture(scope="module")
def small() -> pd.DataFrame:
    return generate(2000).borrowers


def test_same_seed_same_data() -> None:
    first, second = generate(1000, seed=7), generate(1000, seed=7)
    assert content_hash(first.borrowers) == content_hash(second.borrowers)
    assert content_hash(first.audit) == content_hash(second.audit)
    assert generate(1000, seed=8).manifest["content_sha256"] != first.manifest["content_sha256"]


def test_golden_hash() -> None:
    assert generate(500).manifest["content_sha256"]["borrowers"] == GOLDEN_500


def test_columns_and_separation_of_audit_attributes(small: pd.DataFrame) -> None:
    dataset = generate(200)
    assert list(dataset.borrowers.columns) == [ID, *shared.FEATURE_NAMES, "dgp_probability", LABEL]
    assert list(dataset.audit.columns) == [ID, "sex", "age_band"]
    assert not set(dataset.borrowers.columns) & shared.PROTECTED_ATTRIBUTES
    assert (dataset.borrowers[ID] == dataset.audit[ID]).all()


def test_features_match_the_production_builder(small: pd.DataFrame) -> None:
    """Every row satisfies the shared schema's ranges and its derived figures are consistent."""
    for spec in shared.FEATURES:
        column = small[spec.name]
        if not spec.nullable:
            assert column.notna().all(), spec.name
        if spec.kind is shared.FeatureKind.CATEGORY:
            assert set(column.dropna()) <= set(spec.categories)
        else:
            assert column.min() >= float(spec.minimum or Decimal("-1e12")), spec.name
            assert column.max() <= float(spec.maximum or Decimal("1e12")), spec.name
    # loan_to_income is requested ÷ (12 × gross), as the builder defines it.
    expected = (small["requested_amount"] / (12 * small["gross_monthly_income"])).round(4)
    assert (expected - small["loan_to_income"]).abs().max() < 1e-4
    # Living expenses never fall below the illustrative norm's 2,000 floor.
    assert small["living_expenses_applied"].min() >= 2000


def test_plausible_distribution(small: pd.DataFrame) -> None:
    assert 0.08 < small[LABEL].mean() < 0.16
    assert small["net_monthly_income"].le(small["gross_monthly_income"]).all()
    assert small["revolving_utilisation"].isna().any()  # missing = no revolving credit
    assert small["months_since_delinquency"].isna().any()  # missing = never delinquent
    # The outcome follows the documented directions.
    high_dti = small["debt_to_income"] > small["debt_to_income"].quantile(0.8)
    assert small.loc[high_dti, LABEL].mean() > small.loc[~high_dti, LABEL].mean()
    missed = small["missed_payments_12m"] > 0
    assert small.loc[missed, LABEL].mean() > small.loc[~missed, LABEL].mean()


def test_injected_income_gap_is_visible_only_in_the_audit_file() -> None:
    dataset = generate(4000)
    joined = dataset.borrowers.merge(dataset.audit, on=ID)
    medians = joined.groupby("sex")["gross_monthly_income"].median()
    assert medians["F"] < medians["M"]


def test_dgp_uses_model_features_only() -> None:
    frame = generate(50).borrowers
    eta = dgp.linear_predictor(frame[list(shared.FEATURE_NAMES)])
    assert len(eta) == 50
    terms = dgp.describe()["terms"]
    assert isinstance(terms, list)
    assert {t["feature"] for t in terms} <= set(shared.FEATURE_NAMES)


def test_write_and_manifest(tmp_path: Path) -> None:
    assert main(["--rows", "120", "--seed", "3", "--out", str(tmp_path)]) == 0
    manifest = json.loads((tmp_path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["rows"] == 120
    assert manifest["seed"] == 3
    assert manifest["feature_schema"]["version"] == shared.FEATURE_SCHEMA_VERSION
    assert set(manifest["files"]) == {BORROWERS_FILE, AUDIT_FILE}
    reloaded = pd.read_parquet(tmp_path / BORROWERS_FILE)
    assert content_hash(reloaded) == manifest["content_sha256"]["borrowers"]
    assert "dgp" in manifest
    assert manifest["dgp"]["intercept"] == dgp.INTERCEPT


def test_rejects_tiny_datasets() -> None:
    with pytest.raises(ValueError, match="at least 10"):
        generate(5)
