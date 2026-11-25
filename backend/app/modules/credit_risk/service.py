"""Experimental credit-risk scoring (docs/ML_STRATEGY.md §6–§9).

Scoring assembles the model's inputs from what the application already holds — the latest
affordability assessment, the current employment record, the declared credit commitments
and the simulated bureau report — builds features with the shared builder, and stores the
probability, band and explanation with the exact feature snapshot and model version.

Every attempt is recorded. If anything fails (no active model, a model file that fails
verification, an input out of range), the attempt is stored as ``FAILED`` with the reason,
and the application carries on to human review: the model is advisory and never blocks it.
Nothing here commits; callers own the transaction.
"""

from __future__ import annotations

import bisect
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.ids import uuid7
from app.core.money import FINANCIAL_CONTEXT, quantize_ratio
from app.modules.affordability.models import AffordabilityAssessment
from app.modules.applicants.domain.enums import CommitmentType, EmploymentType
from app.modules.applicants.models import Applicant, CreditCommitment, EmploymentRecord
from app.modules.applications.models import LoanApplication
from app.modules.audit import service as audit
from app.modules.credit_risk import explain as explainer
from app.modules.credit_risk import features as shared
from app.modules.credit_risk import registry
from app.modules.credit_risk.domain import bureau
from app.modules.credit_risk.models import (
    AssessmentStatus,
    CreditBureauReport,
    CreditRiskAssessment,
    ModelVersion,
    RiskBand,
    RiskRunReason,
)
from app.modules.identity.models import User

logger = structlog.get_logger("credify.credit_risk")

REVOLVING = frozenset({CommitmentType.CREDIT_CARD, CommitmentType.STORE_ACCOUNT})
MAX_UTILISATION = Decimal("2")
MAX_TENURE_MONTHS = 720
PD_PLACES = Decimal("0.000001")


class ScoringError(RuntimeError):
    """A scoring attempt that must be recorded as FAILED, with this message as the reason."""


# --- Inputs ------------------------------------------------------------------------------


def ensure_bureau_report(
    session: Session, application: LoanApplication, applicant: Applicant, now: datetime
) -> CreditBureauReport:
    """The application's simulated bureau report, pulled once and then reused."""
    existing = session.scalar(
        select(CreditBureauReport).where(CreditBureauReport.application_id == application.id)
    )
    if existing is not None:
        return existing
    report = bureau.simulate(applicant.synthetic_reference)
    row = CreditBureauReport(
        id=uuid7(),
        application_id=application.id,
        applicant_id=applicant.id,
        provider=report.provider,
        simulator_version=report.simulator_version,
        missed_payments_12m=report.missed_payments_12m,
        months_since_delinquency=report.months_since_delinquency,
        created_at=now,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="credit_bureau_report.pulled",
        entity_type="credit_bureau_report",
        entity_id=row.id,
        occurred_at=now,
        context={
            "application_id": application.id,
            "provider": report.provider,
            "simulator_version": report.simulator_version,
        },
    )
    return row


def months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def _employment(
    session: Session, applicant_id: uuid.UUID, today: date
) -> tuple[EmploymentType, int]:
    records = session.scalars(
        select(EmploymentRecord)
        .where(EmploymentRecord.applicant_id == applicant_id, EmploymentRecord.is_active.is_(True))
        .order_by(
            EmploymentRecord.is_current.desc(),
            EmploymentRecord.start_date.desc().nulls_last(),
            EmploymentRecord.created_at.desc(),
        )
    ).all()
    if not records:
        raise ScoringError("no employment record is on file")
    current = records[0]
    tenure = months_between(current.start_date, today) if current.start_date else 0
    return current.employment_type, min(tenure, MAX_TENURE_MONTHS)


def revolving_position(commitments: Sequence[CreditCommitment]) -> Decimal | None:
    """Σ balances ÷ Σ limits over revolving accounts with a limit (None without any)."""
    revolving = [
        c for c in commitments if c.commitment_type in REVOLVING and (c.credit_limit or 0) > 0
    ]
    if not revolving:
        return None
    with localcontext(FINANCIAL_CONTEXT):
        limit = sum((c.credit_limit or Decimal(0) for c in revolving), Decimal(0))
        balance = sum((c.outstanding_balance for c in revolving), Decimal(0))
        return min(quantize_ratio(balance / limit), MAX_UTILISATION)


def risk_input(
    session: Session,
    application: LoanApplication,
    assessment: AffordabilityAssessment,
    report: CreditBureauReport,
    today: date,
) -> shared.RiskInput:
    employment_type, tenure = _employment(session, application.applicant_id, today)
    commitments = session.scalars(
        select(CreditCommitment).where(
            CreditCommitment.applicant_id == application.applicant_id,
            CreditCommitment.is_active.is_(True),
        )
    ).all()
    return shared.RiskInput(
        gross_monthly_income=assessment.gross_monthly_income,
        net_monthly_income=assessment.net_monthly_income,
        employment_type=employment_type,
        employment_tenure_months=tenure,
        living_expenses_applied=assessment.applied_living_expenses,
        existing_debt_instalments=assessment.existing_debt_instalments,
        num_credit_accounts=len(commitments),
        revolving_utilisation=revolving_position(commitments),
        missed_payments_12m=report.missed_payments_12m,
        months_since_delinquency=report.months_since_delinquency,
        requested_amount=application.requested_amount,
        term_months=application.requested_term_months,
        loan_purpose=application.purpose,
        debt_to_income=assessment.debt_to_income_ratio,
        repayment_burden=assessment.repayment_burden_ratio,
        residual_income=assessment.residual_income,
    )


# --- Scoring -----------------------------------------------------------------------------


def band_for(probability: Decimal, cutoffs: dict[str, Any]) -> RiskBand:
    cuts = [Decimal(str(c)) for c in cutoffs["cuts"]]
    bands = cutoffs.get("bands") or [b.value for b in RiskBand]
    return RiskBand(bands[bisect.bisect_right(cuts, probability)])


def _latest_affordability(
    session: Session, application_id: uuid.UUID
) -> AffordabilityAssessment | None:
    return session.scalar(
        select(AffordabilityAssessment)
        .where(AffordabilityAssessment.application_id == application_id)
        .order_by(AffordabilityAssessment.version.desc())
        .limit(1)
    )


def _next_version(session: Session, application_id: uuid.UUID) -> int:
    current = session.scalar(
        select(func.max(CreditRiskAssessment.version)).where(
            CreditRiskAssessment.application_id == application_id
        )
    )
    return (current or 0) + 1


def _predict(
    model_dir: Path | None, version: ModelVersion, features: dict[str, shared.FeatureValue]
) -> tuple[Decimal, dict[str, Any]]:
    loaded = registry.load(model_dir, version)
    frame = explainer.feature_frame(features)
    probability = float(loaded.pipeline.predict_proba(frame)[0, 1])
    explanation = explainer.explain(loaded.pipeline, version.explainer, features)
    pd = Decimal(repr(probability)).quantize(PD_PLACES)
    return pd, explanation


def score(
    session: Session,
    application: LoanApplication,
    *,
    run_reason: RiskRunReason,
    requested_by: User | None,
    now: datetime,
    today: date,
    model_dir: Path | None,
    ip_address: str | None = None,
) -> CreditRiskAssessment:
    """Attempt to score ``application`` and record the attempt, scored or failed.

    The caller must hold a ``FOR UPDATE`` lock on the application row.
    """
    applicant = session.get(Applicant, application.applicant_id)
    if applicant is None:  # pragma: no cover - FK makes this unreachable
        raise ScoringError("applicant not found")
    report = ensure_bureau_report(session, application, applicant, now)
    affordability = _latest_affordability(session, application.id)
    version = registry.active_version(session)
    row = CreditRiskAssessment(
        id=uuid7(),
        application_id=application.id,
        version=_next_version(session, application.id),
        run_reason=run_reason,
        model_version_id=version.id if version else None,
        affordability_assessment_id=affordability.id if affordability else None,
        bureau_report_id=report.id,
        requested_by=requested_by.id if requested_by else None,
        created_at=now,
    )
    try:
        if affordability is None:
            raise ScoringError("no affordability assessment exists yet")
        if version is None:
            raise ScoringError("no credit-risk model is active")
        inputs = risk_input(session, application, affordability, report, today)
        features = shared.build_features(inputs)
        probability, explanation = _predict(model_dir, version, features)
    except (ScoringError, shared.FeatureValidationError, registry.ModelIntegrityError) as exc:
        _fail(row, str(exc))
    except Exception as exc:  # the model is advisory: any model failure is recorded, not raised
        logger.exception("risk_scoring_failed", application_id=str(application.id))
        _fail(row, f"scoring error ({type(exc).__name__})")
    else:
        row.status = AssessmentStatus.SCORED
        row.feature_snapshot = {
            "feature_schema_version": shared.FEATURE_SCHEMA_VERSION,
            "feature_order": list(shared.FEATURE_NAMES),  # JSONB does not keep key order
            "features": features,
            "sources": {
                "affordability_assessment_version": affordability.version,
                "bureau_simulator_version": report.simulator_version,
                "as_of": today.isoformat(),
            },
        }
        row.probability_of_default = probability
        row.decision_threshold = version.decision_threshold
        row.above_threshold = probability >= version.decision_threshold
        row.risk_band = band_for(probability, version.band_cutoffs)
        row.explanation = explanation
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="credit_risk_assessment.created",
        entity_type="credit_risk_assessment",
        entity_id=row.id,
        actor=requested_by,
        occurred_at=now,
        ip_address=ip_address,
        context={
            "application_id": application.id,
            "version": row.version,
            "status": row.status.value,
            "run_reason": run_reason.value,
            "model": f"{version.name} {version.version}" if version else None,
            "risk_band": row.risk_band.value if row.risk_band else None,
            "failure_reason": row.failure_reason,
        },
    )
    return row


def _fail(row: CreditRiskAssessment, reason: str) -> None:
    row.status = AssessmentStatus.FAILED
    row.failure_reason = reason[:1000]
    row.feature_snapshot = None
    row.explanation = None


# --- Reads -------------------------------------------------------------------------------


def list_assessments(session: Session, application_id: uuid.UUID) -> Sequence[CreditRiskAssessment]:
    return session.scalars(
        select(CreditRiskAssessment)
        .where(CreditRiskAssessment.application_id == application_id)
        .order_by(CreditRiskAssessment.version.desc())
    ).all()


def get_assessment(
    session: Session, application_id: uuid.UUID, assessment_id: uuid.UUID
) -> CreditRiskAssessment | None:
    return session.scalar(
        select(CreditRiskAssessment).where(
            CreditRiskAssessment.id == assessment_id,
            CreditRiskAssessment.application_id == application_id,
        )
    )


def latest_scored(session: Session, application_id: uuid.UUID) -> CreditRiskAssessment | None:
    return session.scalar(
        select(CreditRiskAssessment)
        .where(
            CreditRiskAssessment.application_id == application_id,
            CreditRiskAssessment.status == AssessmentStatus.SCORED,
        )
        .order_by(CreditRiskAssessment.version.desc())
        .limit(1)
    )


def bureau_report(session: Session, application_id: uuid.UUID) -> CreditBureauReport | None:
    return session.scalar(
        select(CreditBureauReport).where(CreditBureauReport.application_id == application_id)
    )
