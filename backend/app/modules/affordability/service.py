"""Persisting affordability assessments.

This module gathers an application's inputs, runs the pure calculator and stores the result
as a new immutable version. Access control and workflow (who may run it, and when) belong to
the applications module, which calls :func:`record_assessment` inside its own transaction.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import json_dumps
from app.core.errors import BusinessRuleError
from app.core.ids import uuid7
from app.core.money import CurrencyMismatchError, quantize_money
from app.modules.affordability.domain.calculator import (
    AffordabilityInput,
    AffordabilityResult,
    CommitmentItem,
    ExpenseItem,
    IncomeItem,
    Thresholds,
    assess,
    monthly_equivalent,
)
from app.modules.affordability.domain.expense_norm import DEFAULT_EXPENSE_NORM
from app.modules.affordability.models import AffordabilityAssessment, RunReason
from app.modules.applicants.models import CreditCommitment, ExpenseRecord, IncomeRecord
from app.modules.applications.models import LoanApplication
from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.loans.engine import amortisation

TERM_FIELDS = (
    "annual_interest_rate",
    "initiation_fee",
    "monthly_service_fee",
    "max_dti",
    "warn_dti",
    "min_residual_amount",
    "min_residual_rate",
)


def _decimal(terms: Mapping[str, Any], key: str) -> Decimal:
    try:
        return Decimal(str(terms[key]))
    except (KeyError, ArithmeticError) as exc:  # pragma: no cover - snapshot is server-built
        raise BusinessRuleError(f"The frozen product terms are missing {key}") from exc


def _active[M: (IncomeRecord, ExpenseRecord, CreditCommitment)](
    session: Session, model: type[M], applicant_id: uuid.UUID
) -> Sequence[M]:
    return session.scalars(
        select(model)
        .where(model.applicant_id == applicant_id, model.is_active.is_(True))
        .order_by(model.created_at, model.id)
    ).all()


def build_input(
    session: Session, application: LoanApplication, *, as_of: date
) -> AffordabilityInput:
    """Current financial profile + the product terms frozen when the application was submitted."""
    terms = application.product_terms_snapshot
    if not terms:
        raise BusinessRuleError("Affordability can only be assessed after submission")
    incomes = _active(session, IncomeRecord, application.applicant_id)
    expenses = _active(session, ExpenseRecord, application.applicant_id)
    commitments = _active(session, CreditCommitment, application.applicant_id)
    return AffordabilityInput(
        currency=application.currency,
        loan=amortisation.LoanTerms(
            amount=application.requested_amount,
            annual_interest_rate=_decimal(terms, "annual_interest_rate"),
            term_months=application.requested_term_months,
            initiation_fee=_decimal(terms, "initiation_fee"),
            monthly_service_fee=_decimal(terms, "monthly_service_fee"),
        ),
        thresholds=Thresholds(
            max_dti=_decimal(terms, "max_dti"),
            warn_dti=_decimal(terms, "warn_dti"),
            min_residual_amount=_decimal(terms, "min_residual_amount"),
            min_residual_rate=_decimal(terms, "min_residual_rate"),
        ),
        as_of=as_of,
        incomes=tuple(
            IncomeItem(
                id=str(r.id),
                frequency=r.frequency,
                gross_amount=r.gross_amount,
                net_amount=r.net_amount,
                currency=r.currency,
                verification_status=r.verification_status,
            )
            for r in incomes
        ),
        expenses=tuple(
            ExpenseItem(
                id=str(r.id),
                category=r.category,
                monthly_amount=r.monthly_amount,
                currency=r.currency,
            )
            for r in expenses
        ),
        commitments=tuple(
            CommitmentItem(
                id=str(r.id),
                commitment_type=r.commitment_type,
                monthly_instalment=r.monthly_instalment,
                currency=r.currency,
            )
            for r in commitments
        ),
        expense_norm=DEFAULT_EXPENSE_NORM,
    )


def inputs_snapshot(
    application: LoanApplication, data: AffordabilityInput, result: AffordabilityResult
) -> dict[str, Any]:
    """Everything needed to reproduce the assessment by hand, frozen as JSON."""
    terms = application.product_terms_snapshot or {}
    snapshot = {
        "calculator_version": result.calculator_version,
        "amortisation_version": result.amortisation_version,
        "currency": data.currency.value,
        "application": {
            "reference": application.reference,
            "requested_amount": data.loan.amount,
            "requested_term_months": data.loan.term_months,
        },
        "product_terms": {
            "code": terms.get("code"),
            "captured_at": terms.get("captured_at"),
            **{key: _decimal(terms, key) for key in TERM_FIELDS},
        },
        "income_records": [
            {
                "id": item.id,
                "frequency": item.frequency.value,
                "gross_amount": item.gross_amount,
                "net_amount": item.net_amount,
                # Rounded for display; the totals are summed at full precision.
                "monthly_gross_display": quantize_money(
                    monthly_equivalent(item.gross_amount, item.frequency)
                ),
                "monthly_net_display": quantize_money(
                    monthly_equivalent(item.net_amount, item.frequency)
                ),
                "verification_status": item.verification_status.value,
                "included": item.included,
            }
            for item in data.incomes
        ],
        "expense_records": [
            {"id": item.id, "category": item.category.value, "monthly_amount": item.monthly_amount}
            for item in data.expenses
        ],
        "credit_commitments": [
            {
                "id": item.id,
                "commitment_type": item.commitment_type.value,
                "monthly_instalment": item.monthly_instalment,
            }
            for item in data.commitments
        ],
        "expense_norm": data.expense_norm.as_dict(),
        "expense_norm_applied": result.expense_norm_applied,
    }
    # Round-trip through the JSONB serialiser so the in-memory copy matches what is stored.
    loaded: dict[str, Any] = json.loads(json_dumps(snapshot))
    return loaded


def latest_version(session: Session, application_id: uuid.UUID) -> int | None:
    return session.scalar(
        select(func.max(AffordabilityAssessment.version)).where(
            AffordabilityAssessment.application_id == application_id
        )
    )


def next_version(session: Session, application_id: uuid.UUID) -> int:
    """Callers hold the application row lock, so versions are allocated one at a time."""
    return (latest_version(session, application_id) or 0) + 1


def record_assessment(
    session: Session,
    application: LoanApplication,
    *,
    run_reason: RunReason,
    requested_by: User | None,
    now: datetime,
    as_of: date,
    ip_address: str | None = None,
) -> AffordabilityAssessment:
    """Assess and persist a new version (no commit: the caller owns the transaction).

    The caller must hold a ``FOR UPDATE`` lock on the application row.
    """
    data = build_input(session, application, as_of=as_of)
    try:
        result = assess(data)
    except CurrencyMismatchError as exc:
        raise BusinessRuleError(str(exc), extra={"violations": [str(exc)]}) from exc
    except amortisation.ScheduleError as exc:
        raise BusinessRuleError(str(exc), extra={"violations": [str(exc)]}) from exc

    assessment = AffordabilityAssessment(
        id=uuid7(),
        application_id=application.id,
        version=next_version(session, application.id),
        calculator_version=result.calculator_version,
        run_reason=run_reason,
        inputs_snapshot=inputs_snapshot(application, data, result),
        currency=result.currency,
        gross_monthly_income=result.gross_monthly_income,
        net_monthly_income=result.net_monthly_income,
        declared_living_expenses=result.declared_living_expenses,
        expense_norm=result.expense_norm,
        applied_living_expenses=result.applied_living_expenses,
        existing_debt_instalments=result.existing_debt_instalments,
        disposable_income=result.disposable_income,
        proposed_instalment=result.proposed_instalment,
        residual_income=result.residual_income,
        required_buffer=result.required_buffer,
        debt_to_income_ratio=result.debt_to_income_ratio,
        repayment_burden_ratio=result.repayment_burden_ratio,
        new_loan_burden_ratio=result.new_loan_burden_ratio,
        outcome=result.outcome,
        rule_results=json.loads(json_dumps([rule.as_dict() for rule in result.rule_results])),
        created_by=requested_by.id if requested_by else None,
        created_at=now,
    )
    session.add(assessment)
    session.flush()
    audit.record(
        session,
        action="affordability_assessment.created",
        entity_type="affordability_assessment",
        entity_id=assessment.id,
        actor=requested_by,
        occurred_at=now,
        ip_address=ip_address,
        context={
            "application_id": application.id,
            "version": assessment.version,
            "run_reason": run_reason.value,
            "outcome": result.outcome.value,
            "calculator_version": result.calculator_version,
        },
    )
    return assessment


def list_assessments(
    session: Session, application_id: uuid.UUID
) -> Sequence[AffordabilityAssessment]:
    return session.scalars(
        select(AffordabilityAssessment)
        .where(AffordabilityAssessment.application_id == application_id)
        .order_by(AffordabilityAssessment.version.desc())
    ).all()


def get_assessment(
    session: Session, application_id: uuid.UUID, assessment_id: uuid.UUID
) -> AffordabilityAssessment | None:
    return session.scalar(
        select(AffordabilityAssessment).where(
            AffordabilityAssessment.id == assessment_id,
            AffordabilityAssessment.application_id == application_id,
        )
    )
