"""Reducing-balance amortisation (FINANCIAL_CALCULATIONS.md §3).

Pure and deterministic: the same inputs always give the same schedule, cent for cent.
Every calculation runs in :data:`FINANCIAL_CONTEXT` and money is rounded only at the points
the specification names:

* the level instalment ``A = q(P · r / (1 − (1 + r)^−n))`` (``q(P / n)`` at a zero rate);
* each period's interest ``q(B · r)``.

The final instalment takes whatever principal is left, so the balance closes at exactly
0.00 and the principal column sums to the amount financed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from app.core.money import CENT, FINANCIAL_CONTEXT, quantize_money
from app.modules.loans.engine.calendar import add_months

VERSION = "amortisation-1.0.0"
MONTHS_PER_YEAR = Decimal(12)
ZERO = Decimal("0.00")
MAX_TERM_MONTHS = 600


class ScheduleError(ValueError):
    """The inputs cannot produce a valid schedule."""


@dataclass(frozen=True, slots=True)
class LoanTerms:
    """What the engine needs to amortise a loan."""

    amount: Decimal  # the approved (or requested) amount, before capitalised fees
    annual_interest_rate: Decimal  # nominal, compounded monthly: 0.215 = 21.5 % p.a.
    term_months: int
    initiation_fee: Decimal = ZERO  # capitalised into the amount financed
    monthly_service_fee: Decimal = ZERO  # added to each instalment, never amortised

    @property
    def principal(self) -> Decimal:
        """The amount financed: ``P = amount + initiation_fee``."""
        return self.amount + self.initiation_fee


@dataclass(frozen=True, slots=True)
class Instalment:
    number: int
    due_date: date
    opening_balance: Decimal
    instalment: Decimal  # interest + principal
    interest: Decimal
    principal: Decimal
    service_fee: Decimal
    total_due: Decimal  # instalment + service fee
    closing_balance: Decimal


@dataclass(frozen=True, slots=True)
class Schedule:
    terms: LoanTerms
    disbursement_date: date
    level_instalment: Decimal  # A: every instalment except possibly the last
    instalments: tuple[Instalment, ...]
    version: str = VERSION

    @property
    def principal(self) -> Decimal:
        return self.terms.principal

    @property
    def first_total_due(self) -> Decimal:
        return self.instalments[0].total_due

    @property
    def total_interest(self) -> Decimal:
        return sum((row.interest for row in self.instalments), ZERO)

    @property
    def total_service_fees(self) -> Decimal:
        return sum((row.service_fee for row in self.instalments), ZERO)

    @property
    def total_repayable(self) -> Decimal:
        return sum((row.total_due for row in self.instalments), ZERO)

    @property
    def cost_of_credit(self) -> Decimal:
        """Everything repaid beyond the amount itself: interest, fees, capitalised fees."""
        return self.total_repayable - self.terms.amount


def _is_cents(value: Decimal) -> bool:
    return value == value.quantize(CENT)


def validate_terms(terms: LoanTerms) -> list[str]:
    problems = []
    if terms.amount <= 0 or not _is_cents(terms.amount):
        problems.append("amount must be a positive amount in whole cents")
    if not 1 <= terms.term_months <= MAX_TERM_MONTHS:
        problems.append(f"term_months must be between 1 and {MAX_TERM_MONTHS}")
    if not Decimal(0) <= terms.annual_interest_rate < 1:
        problems.append("annual_interest_rate must be at least 0 and below 1")
    for name in ("initiation_fee", "monthly_service_fee"):
        fee = getattr(terms, name)
        if fee < 0 or not _is_cents(fee):
            problems.append(f"{name} must be zero or a positive amount in whole cents")
    return problems


def monthly_rate(annual_interest_rate: Decimal) -> Decimal:
    """``r = R / 12`` at full precision (never rounded)."""
    with localcontext(FINANCIAL_CONTEXT):
        return annual_interest_rate / MONTHS_PER_YEAR


def level_instalment(
    principal: Decimal, annual_interest_rate: Decimal, term_months: int
) -> Decimal:
    """The amortising instalment ``A``, rounded to cents."""
    with localcontext(FINANCIAL_CONTEXT):
        rate = monthly_rate(annual_interest_rate)
        if rate == 0:
            return quantize_money(principal / term_months)
        return quantize_money(principal * rate / (1 - (1 + rate) ** -term_months))


def build_schedule(terms: LoanTerms, disbursement_date: date) -> Schedule:
    """The full repayment schedule. Raises :class:`ScheduleError` for unusable inputs."""
    problems = validate_terms(terms)
    if problems:
        raise ScheduleError("; ".join(problems))

    with localcontext(FINANCIAL_CONTEXT):
        principal = terms.principal
        rate = monthly_rate(terms.annual_interest_rate)
        level = level_instalment(principal, terms.annual_interest_rate, terms.term_months)
        fee = terms.monthly_service_fee
        balance = principal
        rows = []
        for number in range(1, terms.term_months + 1):
            interest = quantize_money(balance * rate)
            final = number == terms.term_months
            principal_part = balance if final else level - interest
            if not final and not ZERO < principal_part < balance:
                # Rounding to whole cents would leave the balance flat, growing or cleared
                # before the last instalment: these terms cannot be amortised as specified.
                raise ScheduleError(
                    "these terms cannot be repaid in whole-cent instalments; increase the "
                    "amount, shorten the term or lower the rate"
                )
            instalment = interest + principal_part
            closing = balance - principal_part
            rows.append(
                Instalment(
                    number=number,
                    due_date=add_months(disbursement_date, number),
                    opening_balance=balance,
                    instalment=instalment,
                    interest=interest,
                    principal=principal_part,
                    service_fee=fee,
                    total_due=instalment + fee,
                    closing_balance=closing,
                )
            )
            balance = closing
    return Schedule(
        terms=terms,
        disbursement_date=disbursement_date,
        level_instalment=level,
        instalments=tuple(rows),
    )
