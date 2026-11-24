from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.money import MoneyAmount, PositiveMoney
from app.modules.decisions.domain.reason_codes import (
    CATALOGUE,
    DecisionOutcome,
    ReasonCode,
    Recommendation,
)

Rationale = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Explanation = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class RecommendationCreate(BaseModel):
    """A loan officer's recommendation. It must cite the latest affordability assessment."""

    model_config = ConfigDict(extra="forbid")
    recommendation: Recommendation
    rationale: Rationale
    affordability_assessment_id: uuid.UUID


class RecommendationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    recommended_by: uuid.UUID
    recommendation: Recommendation
    rationale: str
    affordability_assessment_id: uuid.UUID
    created_at: datetime


class SendBackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason


class DecisionCreate(BaseModel):
    """The final human decision (credit analyst or administrator, never the recommender).

    ``approved_amount`` and ``approved_term_months`` default to what was requested. A
    counter-offer (a lower amount or a different term) must cite ``COUNTER_OFFER`` and may
    not cost more per month than the instalment that was assessed for affordability.
    """

    model_config = ConfigDict(extra="forbid")
    decision: DecisionOutcome
    reason_codes: Annotated[list[str], Field(max_length=10)]
    explanation: Explanation
    affordability_assessment_id: uuid.UUID
    model_output_acknowledged: bool
    approved_amount: PositiveMoney | None = None
    approved_term_months: Annotated[int, Field(ge=1, le=120)] | None = None


class DecisionRead(BaseModel):
    """The full decision record, for staff."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    decision: DecisionOutcome
    decided_by: uuid.UUID
    recommendation_id: uuid.UUID
    affordability_assessment_id: uuid.UUID
    reason_codes: list[ReasonCode]
    explanation: str
    model_acknowledged: bool
    departs_from_recommendation: bool
    approved_amount: MoneyAmount | None
    approved_term_months: int | None
    created_at: datetime


class ReasonRead(BaseModel):
    code: ReasonCode
    outcome: DecisionOutcome
    label: str
    applicant_text: str

    @classmethod
    def for_code(cls, code: ReasonCode) -> ReasonRead:
        info = CATALOGUE[code]
        return cls(
            code=info.code,
            outcome=info.outcome,
            label=info.label,
            applicant_text=info.applicant_text,
        )


class ApplicantDecisionRead(BaseModel):
    """What the applicant sees: the outcome, the reasons in plain language and the terms.

    The reviewer's internal explanation and identity are not shown.
    """

    application_id: uuid.UUID
    decision: DecisionOutcome
    reasons: list[ReasonRead]
    approved_amount: MoneyAmount | None
    approved_term_months: int | None
    decided_at: datetime
