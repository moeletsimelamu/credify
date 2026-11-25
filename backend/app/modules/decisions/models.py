from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UUIDPrimaryKey, enum_type, money_type
from app.modules.decisions.domain.reason_codes import DecisionOutcome, Recommendation


class LoanRecommendation(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only: a loan officer's recommendation and the evidence it cites."""

    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint("char_length(btrim(rationale)) >= 20", name="rationale_length"),
        Index("ix_recommendations_application_id_created_at", "application_id", "created_at"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT")
    )
    recommended_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    recommendation: Mapped[Recommendation] = mapped_column(
        enum_type(Recommendation, "recommendation")
    )
    rationale: Mapped[str] = mapped_column(Text)
    affordability_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("affordability_assessments.id", ondelete="RESTRICT")
    )
    # The latest scored risk assessment when recommending (None if scoring failed).
    credit_risk_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credit_risk_assessments.id", ondelete="RESTRICT")
    )


class LendingDecision(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only: the one final, human decision on an application."""

    __tablename__ = "lending_decisions"
    __table_args__ = (
        CheckConstraint("model_acknowledged", name="model_acknowledged"),
        CheckConstraint("cardinality(reason_codes) >= 1", name="reason_codes_present"),
        CheckConstraint("char_length(btrim(explanation)) >= 1", name="explanation_present"),
        CheckConstraint(
            "(decision = 'APPROVED') = (approved_amount IS NOT NULL "
            "AND approved_term_months IS NOT NULL)",
            name="approved_terms_match_decision",
        ),
        CheckConstraint("approved_amount IS NULL OR approved_amount > 0", name="amount_positive"),
        CheckConstraint(
            "approved_term_months IS NULL OR approved_term_months BETWEEN 1 AND 120",
            name="term_range",
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), unique=True
    )
    decision: Mapped[DecisionOutcome] = mapped_column(enum_type(DecisionOutcome, "decision"))
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id", ondelete="RESTRICT")
    )
    affordability_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("affordability_assessments.id", ondelete="RESTRICT")
    )
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    explanation: Mapped[str] = mapped_column(Text)
    model_acknowledged: Mapped[bool]
    # True when the decision differs from the recommendation it responds to.
    departs_from_recommendation: Mapped[bool]
    approved_amount: Mapped[Decimal | None] = mapped_column(money_type())
    approved_term_months: Mapped[int | None] = mapped_column(SmallInteger)
    # The scored risk assessment reviewed, and whether the decision goes against its guidance.
    # Both NULL when no scored assessment existed (scoring is advisory and may fail).
    credit_risk_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credit_risk_assessments.id", ondelete="RESTRICT")
    )
    departs_from_model: Mapped[bool | None]
