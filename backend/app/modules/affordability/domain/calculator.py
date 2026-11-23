"""Affordability calculator (FINANCIAL_CALCULATIONS.md §2 and §4).

Pure: it receives plain values, returns plain values and touches no database. Its result is
**input to a human reviewer**, never a lending decision.

Rounding: monthly totals are rounded to cents once, after summing at full precision. Ratios
are computed at full precision and rounded to four places; the rules compare the rounded
ratios, so the figures stored on an assessment always explain its outcome exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import StrEnum

from app.core.money import (
    FINANCIAL_CONTEXT,
    Currency,
    CurrencyMismatchError,
    quantize_money,
    quantize_ratio,
)
from app.modules.affordability.domain.expense_norm import DEFAULT_EXPENSE_NORM, ExpenseNorm
from app.modules.applicants.domain.enums import (
    CommitmentType,
    ExpenseCategory,
    IncomeFrequency,
    VerificationStatus,
)
from app.modules.loans.engine import amortisation

VERSION = "affordability-1.0.0"
ZERO = Decimal("0.00")

# Monthly equivalent = amount × numerator / denominator.
MONTHLY_FACTORS: dict[IncomeFrequency, tuple[int, int]] = {
    IncomeFrequency.WEEKLY: (52, 12),
    IncomeFrequency.FORTNIGHTLY: (26, 12),
    IncomeFrequency.MONTHLY: (1, 1),
    IncomeFrequency.ANNUALLY: (1, 12),
}


class Outcome(StrEnum):
    AFFORDABLE = "AFFORDABLE"
    MARGINAL = "MARGINAL"
    UNAFFORDABLE = "UNAFFORDABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# Worst wins: UNAFFORDABLE > MARGINAL > AFFORDABLE.
SEVERITY = {Outcome.AFFORDABLE: 0, Outcome.MARGINAL: 1, Outcome.UNAFFORDABLE: 2}


class RuleStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_EVALUATED = "NOT_EVALUATED"


# --- Inputs ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncomeItem:
    id: str
    frequency: IncomeFrequency
    gross_amount: Decimal
    net_amount: Decimal
    currency: Currency
    verification_status: VerificationStatus = VerificationStatus.DECLARED

    @property
    def included(self) -> bool:
        """Rejected income (by a reviewer) never counts towards affordability."""
        return self.verification_status is not VerificationStatus.REJECTED


@dataclass(frozen=True, slots=True)
class ExpenseItem:
    id: str
    category: ExpenseCategory
    monthly_amount: Decimal
    currency: Currency


@dataclass(frozen=True, slots=True)
class CommitmentItem:
    id: str
    commitment_type: CommitmentType
    monthly_instalment: Decimal
    currency: Currency


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Affordability thresholds, taken from the product terms frozen at submission."""

    max_dti: Decimal
    warn_dti: Decimal
    min_residual_amount: Decimal
    min_residual_rate: Decimal


@dataclass(frozen=True, slots=True)
class AffordabilityInput:
    currency: Currency
    loan: amortisation.LoanTerms
    thresholds: Thresholds
    as_of: date  # the schedule's start date; it fixes due dates, never an amount
    incomes: tuple[IncomeItem, ...] = ()
    expenses: tuple[ExpenseItem, ...] = ()
    commitments: tuple[CommitmentItem, ...] = ()
    expense_norm: ExpenseNorm = DEFAULT_EXPENSE_NORM


# --- Result ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule_id: str
    name: str
    status: RuleStatus
    effect: Outcome  # what the rule contributes when it fails
    threshold: Decimal | None
    actual: Decimal | None
    explanation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "status": self.status.value,
            "passed": None if self.status is RuleStatus.NOT_EVALUATED else not self.failed,
            "effect": self.effect.value,
            "threshold": self.threshold,
            "actual": self.actual,
            "explanation": self.explanation,
        }

    @property
    def failed(self) -> bool:
        return self.status is RuleStatus.FAILED


@dataclass(frozen=True, slots=True)
class AffordabilityResult:
    currency: Currency
    gross_monthly_income: Decimal  # G
    net_monthly_income: Decimal  # N
    declared_living_expenses: Decimal  # E_d
    expense_norm: Decimal  # E_n
    applied_living_expenses: Decimal  # E = max(E_d, E_n)
    existing_debt_instalments: Decimal  # C
    disposable_income: Decimal  # DI = N − E − C (may be negative)
    proposed_instalment: Decimal  # I: first-period total due, service fee included
    residual_income: Decimal  # RI = DI − I (may be negative)
    required_buffer: Decimal  # Buf
    debt_to_income_ratio: Decimal | None  # (C + I) / G
    repayment_burden_ratio: Decimal | None  # (C + I) / N
    new_loan_burden_ratio: Decimal | None  # I / N
    outcome: Outcome
    rule_results: tuple[RuleResult, ...]
    income_records_used: int
    expense_records_used: int
    calculator_version: str = VERSION
    amortisation_version: str = amortisation.VERSION

    @property
    def expense_norm_applied(self) -> bool:
        return self.expense_norm > self.declared_living_expenses


# --- Calculation -----------------------------------------------------------------------------


def monthly_equivalent(amount: Decimal, frequency: IncomeFrequency) -> Decimal:
    """Full precision; callers round the *total*, never each record."""
    numerator, denominator = MONTHLY_FACTORS[frequency]
    with localcontext(FINANCIAL_CONTEXT):
        return amount * numerator / denominator


def _check_currency(data: AffordabilityInput) -> None:
    currencies = [
        *(item.currency for item in data.incomes),
        *(item.currency for item in data.expenses),
        *(item.currency for item in data.commitments),
    ]
    others = sorted({currency.value for currency in currencies if currency is not data.currency})
    if others:
        raise CurrencyMismatchError(
            f"affordability is calculated in {data.currency.value} only; found amounts in "
            f"{', '.join(others)}"
        )


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    with localcontext(FINANCIAL_CONTEXT):
        return quantize_ratio(numerator / denominator)


def assess(data: AffordabilityInput) -> AffordabilityResult:
    """Run the affordability assessment. Raises ``CurrencyMismatchError`` for mixed currencies
    and ``amortisation.ScheduleError`` if the loan terms cannot be amortised."""
    _check_currency(data)
    thresholds = data.thresholds
    with localcontext(FINANCIAL_CONTEXT):
        counted = [item for item in data.incomes if item.included]
        gross = quantize_money(
            sum((monthly_equivalent(i.gross_amount, i.frequency) for i in counted), ZERO)
        )
        net = quantize_money(
            sum((monthly_equivalent(i.net_amount, i.frequency) for i in counted), ZERO)
        )
        declared = quantize_money(sum((e.monthly_amount for e in data.expenses), ZERO))
        norm = data.expense_norm.minimum_for(gross)
        applied = max(declared, norm)
        debt = quantize_money(sum((c.monthly_instalment for c in data.commitments), ZERO))
        disposable = net - applied - debt

        schedule = amortisation.build_schedule(data.loan, data.as_of)
        instalment = schedule.first_total_due
        residual = disposable - instalment
        buffer = max(
            thresholds.min_residual_amount,
            quantize_money(thresholds.min_residual_rate * net),
        )
        dti = _ratio(debt + instalment, gross)
        burden = _ratio(debt + instalment, net)
        new_burden = _ratio(instalment, net)

    rules = _evaluate_rules(
        has_income=bool(counted) and gross > 0,
        has_expenses=bool(data.expenses),
        residual=residual,
        buffer=buffer,
        dti=dti,
        thresholds=thresholds,
    )
    return AffordabilityResult(
        currency=data.currency,
        gross_monthly_income=gross,
        net_monthly_income=net,
        declared_living_expenses=declared,
        expense_norm=norm,
        applied_living_expenses=applied,
        existing_debt_instalments=debt,
        disposable_income=disposable,
        proposed_instalment=instalment,
        residual_income=residual,
        required_buffer=buffer,
        debt_to_income_ratio=dti,
        repayment_burden_ratio=burden,
        new_loan_burden_ratio=new_burden,
        outcome=_outcome(rules),
        rule_results=tuple(rules),
        income_records_used=len(counted),
        expense_records_used=len(data.expenses),
    )


def _evaluate_rules(
    *,
    has_income: bool,
    has_expenses: bool,
    residual: Decimal,
    buffer: Decimal,
    dti: Decimal | None,
    thresholds: Thresholds,
) -> list[RuleResult]:
    """Rules R0–R4 of §4.2, in order. R0 failing stops evaluation of the others."""
    complete = has_income and has_expenses
    missing = []
    if not has_income:
        missing.append("no counted income")
    if not has_expenses:
        missing.append("no expenses captured")
    rules = [
        RuleResult(
            "R0",
            "Data completeness",
            RuleStatus.PASSED if complete else RuleStatus.FAILED,
            Outcome.INSUFFICIENT_DATA,
            None,
            None,
            "Income and expenses are captured"
            if complete
            else f"Cannot assess: {' and '.join(missing)}",
        )
    ]

    def rule(
        rule_id: str,
        name: str,
        failed: bool,
        effect: Outcome,
        threshold: Decimal | None,
        actual: Decimal | None,
        explanation: str,
    ) -> RuleResult:
        if not complete:
            status = RuleStatus.NOT_EVALUATED
            explanation = "Not evaluated: data is incomplete"
        else:
            status = RuleStatus.FAILED if failed else RuleStatus.PASSED
        return RuleResult(rule_id, name, status, effect, threshold, actual, explanation)

    # A complete assessment always has G > 0, so DTI exists whenever these rules run.
    dti_value = dti if dti is not None else Decimal(0)
    rules += [
        rule(
            "R1",
            "Non-negative residual income",
            residual < 0,
            Outcome.UNAFFORDABLE,
            Decimal("0.00"),
            residual,
            "Residual income after the new instalment must not be negative",
        ),
        rule(
            "R2",
            "Debt-to-income ceiling",
            dti_value > thresholds.max_dti,
            Outcome.UNAFFORDABLE,
            thresholds.max_dti,
            dti,
            "Debt-to-income must not exceed the product maximum",
        ),
        rule(
            "R3",
            "Residual income buffer",
            Decimal(0) <= residual < buffer,
            Outcome.MARGINAL,
            buffer,
            residual,
            "Residual income should cover the required buffer",
        ),
        rule(
            "R4",
            "Debt-to-income warning",
            thresholds.warn_dti < dti_value <= thresholds.max_dti,
            Outcome.MARGINAL,
            thresholds.warn_dti,
            dti,
            "Debt-to-income above the warning level needs a closer look",
        ),
    ]
    return rules


def _outcome(rules: list[RuleResult]) -> Outcome:
    if rules[0].failed:
        return Outcome.INSUFFICIENT_DATA
    worst = Outcome.AFFORDABLE
    for result in rules[1:]:
        if result.failed and SEVERITY[result.effect] > SEVERITY[worst]:
            worst = result.effect
    return worst
