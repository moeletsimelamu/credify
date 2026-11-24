"""The one definition of the credit-risk model's features (docs/ML_STRATEGY.md §3).

Both the offline ML pipeline (``ml/credify_ml``) and the API (Phase 7) build model inputs
with :func:`build_features`, so training and serving can never disagree about what a
feature means. The module is pure: no database, no HTTP, no scikit-learn.

Money and ratios arrive as ``Decimal`` from the affordability engine. They become floats
only here, at the model boundary, because the model itself works in floating point.
Nothing in this module feeds back into a financial calculation.

Protected and proxy attributes (age, sex, race, postal code and the like) are not part of
:class:`RiskInput` at all, so the builder *cannot* read them; a unit test pins this.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal, localcontext
from enum import StrEnum

from app.core.money import FINANCIAL_CONTEXT, quantize_ratio
from app.modules.affordability.domain.calculator import AffordabilityResult
from app.modules.applicants.domain.enums import EmploymentType
from app.modules.applications.domain.enums import LoanPurpose

FEATURE_SCHEMA_VERSION = "features-1.0.0"

# Never model inputs (docs/ML_STRATEGY.md §3). Geographic fields are proxies.
PROTECTED_ATTRIBUTES = frozenset(
    {
        "age", "age_band", "date_of_birth", "sex", "gender", "race", "ethnicity",
        "marital_status", "dependants", "number_of_dependants", "religion", "nationality",
        "disability", "health", "language", "postal_code", "city", "region", "province",
        "address", "address_line1", "address_line2", "country", "first_name", "last_name",
        "name", "email", "phone",
    }
)  # fmt: skip


class FeatureKind(StrEnum):
    MONEY = "money"  # non-negative amount, log-transformed by the model pipeline
    SIGNED_MONEY = "signed_money"  # may be negative (residual income)
    COUNT = "count"
    RATIO = "ratio"
    CATEGORY = "category"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    kind: FeatureKind
    description: str
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    categories: tuple[str, ...] = ()
    nullable: bool = False  # missing has a meaning (e.g. no revolving credit)

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "description": self.description,
            "minimum": None if self.minimum is None else str(self.minimum),
            "maximum": None if self.maximum is None else str(self.maximum),
            "categories": list(self.categories),
            "nullable": self.nullable,
        }


def _d(value: str) -> Decimal:
    return Decimal(value)


K = FeatureKind
MAX_MONEY = Decimal("2000000")
FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("gross_monthly_income", K.MONEY, "Gross monthly income (G)", _d("0"), MAX_MONEY),
    FeatureSpec("net_monthly_income", K.MONEY, "Net monthly income (N)", _d("0"), MAX_MONEY),
    FeatureSpec(
        "employment_type",
        K.CATEGORY,
        "Current employment type",
        categories=tuple(e.value for e in EmploymentType),
    ),
    FeatureSpec(
        "employment_tenure_months", K.COUNT, "Months in current employment", _d("0"), _d("720")
    ),
    FeatureSpec(
        "living_expenses_applied",
        K.MONEY,
        "Living expenses used by affordability: max(declared, norm)",
        _d("0"),
        MAX_MONEY,
    ),
    FeatureSpec(
        "existing_debt_instalments",
        K.MONEY,
        "Monthly instalments on existing credit (C)",
        _d("0"),
        MAX_MONEY,
    ),
    FeatureSpec("num_credit_accounts", K.COUNT, "Open credit accounts", _d("0"), _d("50")),
    FeatureSpec(
        "revolving_utilisation",
        K.RATIO,
        "Revolving balance ÷ limit (missing when there is no revolving credit)",
        _d("0"),
        _d("2"),
        nullable=True,
    ),
    FeatureSpec(
        "missed_payments_12m", K.COUNT, "Missed payments in the last 12 months", _d("0"), _d("12")
    ),
    FeatureSpec(
        "months_since_delinquency",
        K.COUNT,
        "Months since the last delinquency (missing when there has never been one)",
        _d("0"),
        _d("600"),
        nullable=True,
    ),
    FeatureSpec("requested_amount", K.MONEY, "Requested loan amount", _d("0"), _d("5000000")),
    FeatureSpec("term_months", K.COUNT, "Requested term in months", _d("1"), _d("120")),
    FeatureSpec(
        "loan_purpose",
        K.CATEGORY,
        "Stated loan purpose",
        categories=tuple(p.value for p in LoanPurpose),
    ),
    FeatureSpec(
        "debt_to_income",
        K.RATIO,
        "(C + I) ÷ G from the affordability engine (missing without income)",
        _d("0"),
        _d("100"),
        nullable=True,
    ),
    FeatureSpec(
        "repayment_burden",
        K.RATIO,
        "(C + I) ÷ N from the affordability engine (missing without income)",
        _d("0"),
        _d("100"),
        nullable=True,
    ),
    FeatureSpec(
        "residual_income",
        K.SIGNED_MONEY,
        "N − E − C − I from the affordability engine (may be negative)",
        _d("-5000000"),
        _d("5000000"),
    ),
    FeatureSpec(
        "loan_to_income",
        K.RATIO,
        "Requested amount ÷ annual gross income (missing without income)",
        _d("0"),
        _d("1000"),
        nullable=True,
    ),
)  # fmt: skip

FEATURE_NAMES = tuple(spec.name for spec in FEATURES)
CATEGORICAL = tuple(s.name for s in FEATURES if s.kind is FeatureKind.CATEGORY)
NUMERIC = tuple(s.name for s in FEATURES if s.kind is not FeatureKind.CATEGORY)

type FeatureValue = float | str | None


class FeatureValidationError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class RiskInput:
    """Everything the model may see about one application, and nothing else."""

    gross_monthly_income: Decimal
    net_monthly_income: Decimal
    employment_type: EmploymentType
    employment_tenure_months: int
    living_expenses_applied: Decimal
    existing_debt_instalments: Decimal
    num_credit_accounts: int
    revolving_utilisation: Decimal | None
    missed_payments_12m: int
    months_since_delinquency: int | None
    requested_amount: Decimal
    term_months: int
    loan_purpose: LoanPurpose
    debt_to_income: Decimal | None
    repayment_burden: Decimal | None
    residual_income: Decimal

    @classmethod
    def from_affordability(
        cls,
        result: AffordabilityResult,
        *,
        employment_type: EmploymentType,
        employment_tenure_months: int,
        num_credit_accounts: int,
        revolving_utilisation: Decimal | None,
        missed_payments_12m: int,
        months_since_delinquency: int | None,
        requested_amount: Decimal,
        term_months: int,
        loan_purpose: LoanPurpose,
    ) -> RiskInput:
        """Take every affordability figure from the production calculator's result."""
        return cls(
            gross_monthly_income=result.gross_monthly_income,
            net_monthly_income=result.net_monthly_income,
            employment_type=employment_type,
            employment_tenure_months=employment_tenure_months,
            living_expenses_applied=result.applied_living_expenses,
            existing_debt_instalments=result.existing_debt_instalments,
            num_credit_accounts=num_credit_accounts,
            revolving_utilisation=revolving_utilisation,
            missed_payments_12m=missed_payments_12m,
            months_since_delinquency=months_since_delinquency,
            requested_amount=requested_amount,
            term_months=term_months,
            loan_purpose=loan_purpose,
            debt_to_income=result.debt_to_income_ratio,
            repayment_burden=result.repayment_burden_ratio,
            residual_income=result.residual_income,
        )


INPUT_FIELDS = frozenset(f.name for f in fields(RiskInput))


def loan_to_income(requested_amount: Decimal, gross_monthly_income: Decimal) -> Decimal | None:
    """Requested amount ÷ annual gross income, to four places (None without income)."""
    if gross_monthly_income <= 0:
        return None
    with localcontext(FINANCIAL_CONTEXT):
        return quantize_ratio(requested_amount / (gross_monthly_income * 12))


def _raw_values(data: RiskInput) -> dict[str, Decimal | int | str | None]:
    values: dict[str, Decimal | int | str | None] = {
        name: getattr(data, name) for name in FEATURE_NAMES if name in INPUT_FIELDS
    }
    values["employment_type"] = data.employment_type.value
    values["loan_purpose"] = data.loan_purpose.value
    values["loan_to_income"] = loan_to_income(data.requested_amount, data.gross_monthly_income)
    return values


def _problems(spec: FeatureSpec, value: Decimal | int | str | None) -> list[str]:
    if value is None:
        return [] if spec.nullable else [f"{spec.name} is required"]
    if spec.kind is FeatureKind.CATEGORY:
        return [] if value in spec.categories else [f"{spec.name} has an unknown value {value!r}"]
    if isinstance(value, str):  # pragma: no cover - guarded by the dataclass types
        return [f"{spec.name} must be numeric"]
    number = Decimal(value)
    if not number.is_finite():
        return [f"{spec.name} must be a finite number"]
    if spec.minimum is not None and number < spec.minimum:
        return [f"{spec.name} must be at least {spec.minimum}"]
    if spec.maximum is not None and number > spec.maximum:
        return [f"{spec.name} must be at most {spec.maximum}"]
    return []


def build_features(data: RiskInput) -> dict[str, FeatureValue]:
    """The ordered model input for one application. Rejects out-of-range values."""
    raw = _raw_values(data)
    problems = [p for spec in FEATURES for p in _problems(spec, raw[spec.name])]
    if problems:
        raise FeatureValidationError(problems)
    result: dict[str, FeatureValue] = {}
    for spec in FEATURES:
        value = raw[spec.name]
        if value is None or isinstance(value, str):
            result[spec.name] = value
        else:
            result[spec.name] = float(value)  # the model boundary: floats from here on
    return result


def schema() -> dict[str, object]:
    """The feature schema recorded with every dataset and model."""
    return {
        "version": FEATURE_SCHEMA_VERSION,
        "features": [spec.as_dict() for spec in FEATURES],
    }
