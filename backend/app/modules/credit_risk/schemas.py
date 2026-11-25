from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.modules.credit_risk.models import (
    AssessmentStatus,
    ModelStatus,
    RiskBand,
    RiskRunReason,
)

ADVISORY = (
    "Experimental model output — advisory only. It was trained on synthetic data and never "
    "approves or declines an application; a human records every decision."
)


class RiskAssessmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    version: int
    run_reason: RiskRunReason
    status: AssessmentStatus
    model_version_id: uuid.UUID | None
    affordability_assessment_id: uuid.UUID | None
    bureau_report_id: uuid.UUID | None
    probability_of_default: Decimal | None
    risk_band: RiskBand | None
    decision_threshold: Decimal | None
    above_threshold: bool | None  # True: "review carefully" guidance (bands D and E)
    explanation: dict[str, Any] | None
    feature_snapshot: dict[str, Any] | None
    failure_reason: str | None
    requested_by: uuid.UUID | None
    created_at: datetime
    advisory_notice: str = ADVISORY


class BureauReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    provider: str  # always SIMULATED: no real bureau is ever contacted
    simulator_version: str
    missed_payments_12m: int
    months_since_delinquency: int | None
    created_at: datetime


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    version: str
    algorithm: str
    status: ModelStatus
    artefact_sha256: str
    training_dataset_sha256: str
    dataset_generator_version: str
    random_seed: int
    decision_threshold: Decimal
    band_cutoffs: dict[str, Any]
    metrics: dict[str, Any]
    feature_schema: dict[str, Any]
    libraries: dict[str, Any]
    git_commit: str | None
    trained_at: datetime
    activated_at: datetime | None
    retired_at: datetime | None
    created_at: datetime
