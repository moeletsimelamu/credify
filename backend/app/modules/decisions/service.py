"""Persisting recommendations and final decisions.

Workflow (who may act, and when) belongs to the applications state machine; the
applications module calls these functions inside its own transaction once a transition
has been validated. Nothing here commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import uuid7
from app.modules.audit import service as audit
from app.modules.credit_risk.models import CreditRiskAssessment
from app.modules.decisions.domain.reason_codes import DecisionOutcome, Recommendation
from app.modules.decisions.models import LendingDecision, LoanRecommendation
from app.modules.identity.models import User


def record_recommendation(
    session: Session,
    *,
    application_id: uuid.UUID,
    recommendation: Recommendation,
    rationale: str,
    affordability_assessment_id: uuid.UUID,
    credit_risk_assessment_id: uuid.UUID | None,
    actor: User,
    now: datetime,
    ip_address: str | None,
) -> LoanRecommendation:
    row = LoanRecommendation(
        id=uuid7(),
        application_id=application_id,
        recommended_by=actor.id,
        recommendation=recommendation,
        rationale=rationale.strip(),
        affordability_assessment_id=affordability_assessment_id,
        credit_risk_assessment_id=credit_risk_assessment_id,
        created_at=now,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="recommendation.recorded",
        entity_type="recommendation",
        entity_id=row.id,
        actor=actor,
        occurred_at=now,
        ip_address=ip_address,
        context={
            "application_id": application_id,
            "recommendation": recommendation.value,
            "affordability_assessment_id": affordability_assessment_id,
            "credit_risk_assessment_id": credit_risk_assessment_id,
        },
    )
    return row


def departs_from_model(decision: DecisionOutcome, risk: CreditRiskAssessment | None) -> bool | None:
    """Whether the decision goes against the model's guidance (None without a scored model).

    The guidance is "review carefully" when the PD is at or above the operating threshold
    (bands D and E): approving such an application, or declining one below it, departs.
    """
    if risk is None or risk.above_threshold is None:
        return None
    return risk.above_threshold == (decision is DecisionOutcome.APPROVED)


def record_decision(
    session: Session,
    *,
    application_id: uuid.UUID,
    decision: DecisionOutcome,
    recommendation: LoanRecommendation,
    affordability_assessment_id: uuid.UUID,
    credit_risk_assessment: CreditRiskAssessment | None,
    reason_codes: Sequence[str],
    explanation: str,
    approved_amount: Decimal | None,
    approved_term_months: int | None,
    actor: User,
    now: datetime,
    ip_address: str | None,
) -> LendingDecision:
    follows = (decision is DecisionOutcome.APPROVED) == (
        recommendation.recommendation is Recommendation.APPROVE
    )
    row = LendingDecision(
        id=uuid7(),
        application_id=application_id,
        decision=decision,
        decided_by=actor.id,
        recommendation_id=recommendation.id,
        affordability_assessment_id=affordability_assessment_id,
        reason_codes=list(reason_codes),
        explanation=explanation.strip(),
        model_acknowledged=True,
        departs_from_recommendation=not follows,
        credit_risk_assessment_id=credit_risk_assessment.id if credit_risk_assessment else None,
        departs_from_model=departs_from_model(decision, credit_risk_assessment),
        approved_amount=approved_amount,
        approved_term_months=approved_term_months,
        created_at=now,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="decision.recorded",
        entity_type="lending_decision",
        entity_id=row.id,
        actor=actor,
        occurred_at=now,
        ip_address=ip_address,
        context={
            "application_id": application_id,
            "decision": decision.value,
            "reason_codes": list(reason_codes),
            "recommendation_id": recommendation.id,
            "recommended_by": recommendation.recommended_by,
            "departs_from_recommendation": not follows,
            "credit_risk_assessment_id": (
                credit_risk_assessment.id if credit_risk_assessment else None
            ),
            "departs_from_model": departs_from_model(decision, credit_risk_assessment),
            "approved_amount": approved_amount,
            "approved_term_months": approved_term_months,
        },
    )
    return row


def latest_recommendation(session: Session, application_id: uuid.UUID) -> LoanRecommendation | None:
    return session.scalar(
        select(LoanRecommendation)
        .where(LoanRecommendation.application_id == application_id)
        .order_by(LoanRecommendation.created_at.desc(), LoanRecommendation.id.desc())
        .limit(1)
    )


def list_recommendations(
    session: Session, application_id: uuid.UUID
) -> Sequence[LoanRecommendation]:
    return session.scalars(
        select(LoanRecommendation)
        .where(LoanRecommendation.application_id == application_id)
        .order_by(LoanRecommendation.created_at, LoanRecommendation.id)
    ).all()


def get_decision(session: Session, application_id: uuid.UUID) -> LendingDecision | None:
    return session.scalar(
        select(LendingDecision).where(LendingDecision.application_id == application_id)
    )
