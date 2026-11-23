"""Affordability calculator (FINANCIAL_CALCULATIONS.md §2 and §4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.money import Currency, CurrencyMismatchError
from app.modules.affordability.domain.calculator import (
    VERSION,
    AffordabilityInput,
    CommitmentItem,
    ExpenseItem,
    IncomeItem,
    Outcome,
    RuleStatus,
    Thresholds,
    assess,
    monthly_equivalent,
)
from app.modules.affordability.domain.expense_norm import (
    DEFAULT_EXPENSE_NORM,
    ExpenseNorm,
    NormBand,
)
from app.modules.applicants.domain.enums import (
    CommitmentType,
    ExpenseCategory,
    IncomeFrequency,
    VerificationStatus,
)
from app.modules.loans.engine.amortisation import LoanTerms, ScheduleError

D = Decimal
ZAR = Currency.ZAR
F = IncomeFrequency


def income(gross: str, net: str, frequency: F = F.MONTHLY, **extra: object) -> IncomeItem:
    return IncomeItem("inc", frequency, D(gross), D(net), extra.pop("currency", ZAR), **extra)  # type: ignore[arg-type]


def expense(amount: str, currency: Currency = ZAR) -> ExpenseItem:
    return ExpenseItem("exp", ExpenseCategory.HOUSING, D(amount), currency)


def commitment(amount: str, currency: Currency = ZAR) -> CommitmentItem:
    return CommitmentItem("com", CommitmentType.VEHICLE_FINANCE, D(amount), currency)


def example_c(**changes: object) -> AffordabilityInput:
    """§4.3 worked example C."""
    base = AffordabilityInput(
        currency=ZAR,
        loan=LoanTerms(D("30000.00"), D("0.215"), 24, monthly_service_fee=D("69.00")),
        thresholds=Thresholds(D("0.4500"), D("0.3500"), D("1500.00"), D("0.1000")),
        as_of=date(2026, 11, 18),
        incomes=(income("32000.00", "25400.00"),),
        expenses=(expense("9500.00"),),
        commitments=(commitment("2700.00"), commitment("500.00")),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def rule(result: object, rule_id: str) -> object:
    return next(r for r in result.rule_results if r.rule_id == rule_id)  # type: ignore[attr-defined]


# --- Worked example ----------------------------------------------------------------------------


def test_worked_example_c() -> None:
    result = assess(example_c())
    assert result.gross_monthly_income == D("32000.00")
    assert result.net_monthly_income == D("25400.00")
    assert result.declared_living_expenses == D("9500.00")
    assert result.expense_norm == D("4220.00")
    assert result.applied_living_expenses == D("9500.00")
    assert result.existing_debt_instalments == D("3200.00")
    assert result.disposable_income == D("12700.00")
    assert result.proposed_instalment == D("1617.95")
    assert result.residual_income == D("11082.05")
    assert result.debt_to_income_ratio == D("0.1506")
    assert result.repayment_burden_ratio == D("0.1897")
    assert result.new_loan_burden_ratio == D("0.0637")
    assert result.required_buffer == D("2540.00")
    assert result.outcome is Outcome.AFFORDABLE
    assert [r.status for r in result.rule_results] == [RuleStatus.PASSED] * 5
    assert result.calculator_version == VERSION
    assert not result.expense_norm_applied


def test_capitalised_initiation_fee_raises_the_instalment() -> None:
    loan = LoanTerms(
        D("30000.00"), D("0.215"), 24, initiation_fee=D("1000.00"), monthly_service_fee=D("69.00")
    )
    assert assess(example_c(loan=loan)).proposed_instalment > D("1617.95")


# --- Income normalisation (§2) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "amount", "monthly"),
    [
        (F.WEEKLY, "1000.00", "4333.33"),
        (F.FORTNIGHTLY, "1000.00", "2166.67"),
        (F.MONTHLY, "1000.00", "1000.00"),
        (F.ANNUALLY, "120000.00", "10000.00"),
    ],
)
def test_monthly_equivalents(frequency: F, amount: str, monthly: str) -> None:
    assert monthly_equivalent(D(amount), frequency).quantize(D("0.01")) == D(monthly)


def test_income_totals_are_rounded_once_after_summing() -> None:
    # Three weekly 1,000.00 incomes: 3 × 4,333.333… = 13,000.00 exactly. Rounding each record
    # first would give 12,999.99.
    data = example_c(incomes=(income("1000.00", "1000.00", F.WEEKLY),) * 3)
    assert assess(data).gross_monthly_income == D("13000.00")


def test_rejected_income_is_excluded() -> None:
    rejected = income("50000.00", "40000.00", verification_status=VerificationStatus.REJECTED)
    verified = income("32000.00", "25400.00", verification_status=VerificationStatus.VERIFIED)
    result = assess(example_c(incomes=(rejected, verified)))
    assert result.gross_monthly_income == D("32000.00")
    assert result.income_records_used == 1


# --- Expense norm (§4.1) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gross", "norm"),
    [
        ("0.00", "2000.00"),
        ("6000.00", "2000.00"),
        ("6000.01", "2000.00"),  # 2,000 + 10 % × 0.01 rounds to 2,000.00
        ("10000.00", "2400.00"),
        ("15000.00", "2900.00"),
        ("15000.01", "2900.00"),
        ("20000.00", "3300.00"),
        ("30000.00", "4100.00"),
        ("32000.00", "4220.00"),
        ("100000.00", "8300.00"),
    ],
)
def test_expense_norm_bands(gross: str, norm: str) -> None:
    assert DEFAULT_EXPENSE_NORM.minimum_for(D(gross)) == D(norm)


def test_norm_replaces_under_declared_expenses() -> None:
    result = assess(example_c(expenses=(expense("1000.00"),)))
    assert result.declared_living_expenses == D("1000.00")
    assert result.applied_living_expenses == D("4220.00")
    assert result.expense_norm_applied


@pytest.mark.parametrize(
    "bands",
    [
        (),
        (NormBand(D("100"), D("0"), D("0")),),
        (NormBand(D("0"), D("0"), D("0")), NormBand(D("0"), D("1"), D("0"))),
        (NormBand(D("0"), D("-1"), D("0")),),
        (NormBand(D("0"), D("0"), D("1.5")),),
    ],
)
def test_invalid_norm_configuration(bands: tuple[NormBand, ...]) -> None:
    with pytest.raises(ValueError, match=r"band|amounts"):
        ExpenseNorm("bad", bands)


# --- Outcome rules (§4.2) --------------------------------------------------------------------


def test_negative_residual_is_unaffordable() -> None:
    result = assess(example_c(expenses=(expense("21000.00"),)))
    assert result.residual_income < 0
    assert rule(result, "R1").status is RuleStatus.FAILED  # type: ignore[attr-defined]
    assert result.outcome is Outcome.UNAFFORDABLE


def test_dti_above_maximum_is_unaffordable() -> None:
    # (C + I) / G > 0.45 while the residual stays positive.
    result = assess(example_c(commitments=(commitment("13000.00"),)))
    assert result.debt_to_income_ratio is not None
    assert result.debt_to_income_ratio > D("0.45")
    assert rule(result, "R2").status is RuleStatus.FAILED  # type: ignore[attr-defined]
    assert result.outcome is Outcome.UNAFFORDABLE


def test_small_residual_is_marginal() -> None:
    result = assess(example_c(expenses=(expense("19000.00"),)))
    assert D(0) <= result.residual_income < result.required_buffer
    assert result.outcome is Outcome.MARGINAL


def test_dti_in_the_warning_band_is_marginal() -> None:
    # DTI = 11,617.95 / 32,000 = 0.3631; RI = 4,282.05 still covers the buffer.
    result = assess(example_c(commitments=(commitment("10000.00"),)))
    assert result.debt_to_income_ratio is not None
    assert D("0.35") < result.debt_to_income_ratio <= D("0.45")
    assert rule(result, "R4").status is RuleStatus.FAILED  # type: ignore[attr-defined]
    assert result.outcome is Outcome.MARGINAL


def _thresholds(**changes: str) -> Thresholds:
    base = Thresholds(D("0.4500"), D("0.3500"), D("1500.00"), D("0.1000"))
    return replace(base, **{k: D(v) for k, v in changes.items()})


def test_dti_exactly_at_the_limits() -> None:
    # Example C's DTI is 0.1506: put both limits exactly there.
    at_max = assess(example_c(thresholds=_thresholds(max_dti="0.1506", warn_dti="0.1000")))
    assert rule(at_max, "R2").status is RuleStatus.PASSED  # type: ignore[attr-defined]
    at_warn = assess(example_c(thresholds=_thresholds(warn_dti="0.1506")))
    assert rule(at_warn, "R4").status is RuleStatus.PASSED  # type: ignore[attr-defined]
    assert at_warn.outcome is Outcome.AFFORDABLE


def test_residual_exactly_at_the_buffer_passes() -> None:
    result = assess(example_c(thresholds=_thresholds(min_residual_amount="11082.05")))
    assert result.required_buffer == result.residual_income
    assert rule(result, "R3").status is RuleStatus.PASSED  # type: ignore[attr-defined]


def test_zero_residual_is_marginal_not_unaffordable() -> None:
    # RI = 25,400.00 − E − 3,200.00 − 1,617.95 = 0.00 when E = 20,582.05.
    result = assess(example_c(expenses=(expense("20582.05"),)))
    assert result.residual_income == D("0.00")
    assert result.outcome is Outcome.MARGINAL


def test_buffer_uses_the_larger_of_amount_and_rate() -> None:
    assert assess(example_c()).required_buffer == D("2540.00")
    low_income = example_c(incomes=(income("12000.00", "10000.00"),))
    assert assess(low_income).required_buffer == D("1500.00")


def test_worst_outcome_wins() -> None:
    # Negative residual (R1) and a DTI in the warning band (R4) at the same time.
    result = assess(
        example_c(
            incomes=(income("20000.00", "8000.00"),),
            commitments=(commitment("6000.00"),),  # DTI 7,617.95 / 20,000 = 0.3809
        )
    )
    failed = {r.rule_id for r in result.rule_results if r.failed}
    assert {"R1", "R4"} <= failed
    assert result.outcome is Outcome.UNAFFORDABLE


@pytest.mark.parametrize(
    "changes",
    [
        {"incomes": ()},
        {"expenses": ()},
        {"incomes": (income("1.00", "1.00", verification_status=VerificationStatus.REJECTED),)},
    ],
)
def test_insufficient_data_stops_evaluation(changes: dict[str, object]) -> None:
    result = assess(example_c(**changes))
    assert result.outcome is Outcome.INSUFFICIENT_DATA
    assert result.rule_results[0].status is RuleStatus.FAILED
    assert all(r.status is RuleStatus.NOT_EVALUATED for r in result.rule_results[1:])


def test_ratios_are_absent_without_income() -> None:
    result = assess(example_c(incomes=()))
    assert result.debt_to_income_ratio is None
    assert result.repayment_burden_ratio is None
    assert result.new_loan_burden_ratio is None


def test_rule_results_serialise_for_storage() -> None:
    stored = assess(example_c()).rule_results[2].as_dict()
    assert stored == {
        "rule_id": "R2",
        "name": "Debt-to-income ceiling",
        "status": "PASSED",
        "passed": True,
        "effect": "UNAFFORDABLE",
        "threshold": D("0.4500"),
        "actual": D("0.1506"),
        "explanation": "Debt-to-income must not exceed the product maximum",
    }


# --- Currency and loan terms -----------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"incomes": (income("1000.00", "900.00", currency=Currency.USD),)},
        {"expenses": (expense("100.00", Currency.EUR),)},
        {"commitments": (commitment("100.00", Currency.GBP),)},
    ],
)
def test_mixed_currencies_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(CurrencyMismatchError, match="ZAR only"):
        assess(example_c(**changes))


def test_unamortisable_terms_raise() -> None:
    with pytest.raises(ScheduleError):
        assess(example_c(loan=LoanTerms(D("1.00"), D("0"), 360)))


# --- Properties ------------------------------------------------------------------------------

money = st.decimals(min_value=D("0.00"), max_value=D("200000.00"), places=2)


@settings(max_examples=200, deadline=None)
@given(extra=st.decimals(min_value=D("0.01"), max_value=D("50000.00"), places=2), base=money)
def test_more_expenses_never_improve_the_outcome(extra: Decimal, base: Decimal) -> None:
    severity = {Outcome.AFFORDABLE: 0, Outcome.MARGINAL: 1, Outcome.UNAFFORDABLE: 2}
    before = assess(example_c(expenses=(expense(str(base)),)))
    after = assess(example_c(expenses=(expense(str(base)), expense(str(extra)))))
    assert severity[after.outcome] >= severity[before.outcome]
    assert after.residual_income <= before.residual_income


@settings(max_examples=200, deadline=None)
@given(
    gross=st.decimals(min_value=D("1.00"), max_value=D("500000.00"), places=2),
    net_share=st.decimals(min_value=D("0.01"), max_value=D("1.00"), places=2),
    expenses=money,
    debt=money,
)
def test_identities_hold(
    gross: Decimal, net_share: Decimal, expenses: Decimal, debt: Decimal
) -> None:
    net = max((gross * net_share).quantize(D("0.01")), D("0.01"))
    result = assess(
        example_c(
            incomes=(income(str(gross), str(net)),),
            expenses=(expense(str(expenses)),),
            commitments=(commitment(str(debt)),),
        )
    )
    assert result.applied_living_expenses == max(expenses, result.expense_norm)
    assert result.disposable_income == net - result.applied_living_expenses - debt
    assert result.residual_income == result.disposable_income - result.proposed_instalment
    assert result.required_buffer >= D("1500.00")
    assert result.outcome is not Outcome.INSUFFICIENT_DATA
