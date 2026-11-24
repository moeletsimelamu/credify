"""The shared credit-risk feature builder (docs/ML_STRATEGY.md §3)."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

import pytest

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
from app.modules.credit_risk import features as f
from app.modules.loans.engine.amortisation import LoanTerms

D = Decimal


def _example_c() -> f.RiskInput:
    """Worked example C (FINANCIAL_CALCULATIONS.md §4.3) through the real calculator."""
    result = assess(
        AffordabilityInput(
            currency=Currency.ZAR,
            loan=LoanTerms(
                amount=D("30000.00"),
                annual_interest_rate=D("0.215"),
                term_months=24,
                monthly_service_fee=D("69.00"),
            ),
            thresholds=Thresholds(D("0.45"), D("0.35"), D("1500.00"), D("0.10")),
            as_of=date(2026, 1, 1),
            incomes=(
                IncomeItem("i", IncomeFrequency.MONTHLY, D("32000"), D("25400"), Currency.ZAR),
            ),
            expenses=(ExpenseItem("e", ExpenseCategory.HOUSING, D("9500"), Currency.ZAR),),
            commitments=(
                CommitmentItem("c", CommitmentType.VEHICLE_FINANCE, D("3200"), Currency.ZAR),
            ),
        )
    )
    return f.RiskInput.from_affordability(
        result,
        employment_type=EmploymentType.PERMANENT,
        employment_tenure_months=48,
        num_credit_accounts=2,
        revolving_utilisation=D("0.35"),
        missed_payments_12m=0,
        months_since_delinquency=None,
        requested_amount=D("30000.00"),
        term_months=24,
        loan_purpose=LoanPurpose.DEBT_CONSOLIDATION,
    )


def test_builder_uses_the_affordability_figures() -> None:
    built = f.build_features(_example_c())
    assert list(built) == list(f.FEATURE_NAMES)
    assert built["gross_monthly_income"] == 32000.0
    assert built["living_expenses_applied"] == 9500.0
    assert built["existing_debt_instalments"] == 3200.0
    assert built["debt_to_income"] == 0.1506
    assert built["repayment_burden"] == 0.1897
    assert built["residual_income"] == 11082.05
    assert built["loan_to_income"] == 0.0781  # 30,000 / 384,000
    assert built["employment_type"] == "PERMANENT"
    assert built["months_since_delinquency"] is None


def test_no_protected_attribute_can_reach_the_model() -> None:
    assert not set(f.FEATURE_NAMES) & f.PROTECTED_ATTRIBUTES
    assert not f.INPUT_FIELDS & f.PROTECTED_ATTRIBUTES
    # Every input field is a feature: there is nowhere to smuggle anything else in.
    assert set(f.FEATURE_NAMES) >= f.INPUT_FIELDS
    assert {field.name for field in fields(f.RiskInput)} == f.INPUT_FIELDS


def test_schema_lists_every_feature_in_order() -> None:
    schema = f.schema()
    assert schema["version"] == f.FEATURE_SCHEMA_VERSION
    names = [item["name"] for item in schema["features"]]  # type: ignore[attr-defined]
    assert names == list(f.FEATURE_NAMES)
    assert set(f.CATEGORICAL) == {"employment_type", "loan_purpose"}
    assert len(f.NUMERIC) + len(f.CATEGORICAL) == len(f.FEATURES) == 17


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"missed_payments_12m": 13}, "missed_payments_12m must be at most 12"),
        ({"employment_tenure_months": -1}, "employment_tenure_months must be at least 0"),
        ({"revolving_utilisation": D("2.5")}, "revolving_utilisation must be at most 2"),
        ({"term_months": 0}, "term_months must be at least 1"),
        ({"residual_income": D("NaN")}, "residual_income must be a finite number"),
    ],
)
def test_out_of_range_inputs_are_rejected(changes: dict[str, object], fragment: str) -> None:
    with pytest.raises(f.FeatureValidationError) as excinfo:
        f.build_features(replace(_example_c(), **changes))  # type: ignore[arg-type]
    assert fragment in excinfo.value.problems


def test_missing_values_only_where_they_mean_something() -> None:
    no_income = replace(
        _example_c(),
        gross_monthly_income=D("0"),
        net_monthly_income=D("0"),
        debt_to_income=None,
        repayment_burden=None,
        revolving_utilisation=None,
    )
    built = f.build_features(no_income)
    assert built["loan_to_income"] is None
    assert built["debt_to_income"] is None
    assert built["revolving_utilisation"] is None
    assert f.loan_to_income(D("1000"), D("0")) is None
