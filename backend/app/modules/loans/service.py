"""Schedule previews. Loan accounts, repayments and arrears arrive in Phase 5."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.errors import BusinessRuleError
from app.core.money import Currency
from app.modules.loans.engine import amortisation
from app.modules.loans.schemas import RepaymentSchedulePreview
from app.modules.products.models import LoanProduct


def _build(terms: amortisation.LoanTerms, disbursement_date: date) -> amortisation.Schedule:
    try:
        return amortisation.build_schedule(terms, disbursement_date)
    except amortisation.ScheduleError as exc:
        raise BusinessRuleError(str(exc), extra={"violations": [str(exc)]}) from exc


def limit_problems(product: LoanProduct, amount: Decimal, term_months: int) -> list[str]:
    problems = []
    if not product.min_amount <= amount <= product.max_amount:
        problems.append(
            f"amount must be between {product.min_amount} and {product.max_amount} "
            f"{product.currency.value}"
        )
    if not product.min_term_months <= term_months <= product.max_term_months:
        problems.append(
            f"term_months must be between {product.min_term_months} and {product.max_term_months}"
        )
    return problems


def preview_for_product(
    product: LoanProduct, amount: Decimal, term_months: int, disbursement_date: date
) -> RepaymentSchedulePreview:
    """A schedule under a product's *current* terms, within its limits."""
    problems = limit_problems(product, amount, term_months)
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})
    terms = amortisation.LoanTerms(
        amount=amount,
        annual_interest_rate=product.annual_interest_rate,
        term_months=term_months,
        initiation_fee=product.initiation_fee,
        monthly_service_fee=product.monthly_service_fee,
    )
    return RepaymentSchedulePreview.from_schedule(
        _build(terms, disbursement_date), product.currency
    )


def preview_from_snapshot(
    terms_snapshot: Mapping[str, Any],
    amount: Decimal,
    term_months: int,
    currency: Currency,
    disbursement_date: date,
) -> RepaymentSchedulePreview:
    """A schedule under the terms frozen on an application at submission."""
    terms = amortisation.LoanTerms(
        amount=amount,
        annual_interest_rate=Decimal(str(terms_snapshot["annual_interest_rate"])),
        term_months=term_months,
        initiation_fee=Decimal(str(terms_snapshot["initiation_fee"])),
        monthly_service_fee=Decimal(str(terms_snapshot["monthly_service_fee"])),
    )
    return RepaymentSchedulePreview.from_schedule(_build(terms, disbursement_date), currency)
