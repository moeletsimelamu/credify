"""Model registry, simulated bureau reports and risk assessments (DATABASE.md §3.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UUIDPrimaryKey, enum_type


def probability_type() -> Numeric[Decimal]:
    """NUMERIC(7,6) — probabilities and thresholds, 0 ≤ p ≤ 1."""
    return Numeric(7, 6)


class ModelStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class RiskBand(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class AssessmentStatus(StrEnum):
    SCORED = "SCORED"
    FAILED = "FAILED"


class RiskRunReason(StrEnum):
    SUBMISSION = "SUBMISSION"
    RESUBMISSION = "RESUBMISSION"
    STAFF_REQUEST = "STAFF_REQUEST"


class ModelVersion(UUIDPrimaryKey, CreatedAt, Base):
    """A registered model. Mutable only in its status (CANDIDATE → ACTIVE → RETIRED)."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_versions_name_version"),
        Index(
            "uq_model_versions_one_active",
            "name",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        CheckConstraint(
            "decision_threshold > 0 AND decision_threshold < 1", name="threshold_range"
        ),
        CheckConstraint("artefact_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        CheckConstraint(
            "((status = 'ACTIVE') = (activated_at IS NOT NULL AND retired_at IS NULL)) "
            "AND ((status = 'RETIRED') = (retired_at IS NOT NULL))",
            name="status_timestamps",
        ),
    )

    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(20))
    algorithm: Mapped[str] = mapped_column(String(60))
    # Relative to the read-only model directory; never an absolute path or a URL.
    artefact_uri: Mapped[str] = mapped_column(String(300))
    artefact_sha256: Mapped[str] = mapped_column(String(64))
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    training_dataset_sha256: Mapped[str] = mapped_column(String(64))
    dataset_generator_version: Mapped[str] = mapped_column(String(40))
    random_seed: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decision_threshold: Mapped[Decimal] = mapped_column(probability_type())
    band_cutoffs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explainer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    libraries: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ModelStatus] = mapped_column(enum_type(ModelStatus, "model_status"))
    git_commit: Mapped[str | None] = mapped_column(String(60))
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # No registered_by column: registration happens only from the offline pipeline (the audit
    # event records it), and a foreign key to users would tie the registry to demo data.


class CreditBureauReport(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only: the simulated bureau report pulled for an application."""

    __tablename__ = "credit_bureau_reports"
    __table_args__ = (
        CheckConstraint("missed_payments_12m BETWEEN 0 AND 12", name="missed_range"),
        CheckConstraint(
            "months_since_delinquency IS NULL OR months_since_delinquency BETWEEN 0 AND 600",
            name="months_range",
        ),
        CheckConstraint("provider = 'SIMULATED'", name="simulated_only"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), unique=True
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applicants.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    simulator_version: Mapped[str] = mapped_column(String(40))
    missed_payments_12m: Mapped[int] = mapped_column(SmallInteger)
    months_since_delinquency: Mapped[int | None] = mapped_column(SmallInteger)


class CreditRiskAssessment(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only: one scoring attempt. A failure is recorded too and never blocks review."""

    __tablename__ = "credit_risk_assessments"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_credit_risk_assessments_version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "probability_of_default IS NULL OR probability_of_default BETWEEN 0 AND 1",
            name="probability_range",
        ),
        CheckConstraint(
            "(status = 'SCORED') = (probability_of_default IS NOT NULL AND risk_band IS NOT NULL "
            "AND model_version_id IS NOT NULL AND feature_snapshot IS NOT NULL "
            "AND explanation IS NOT NULL AND above_threshold IS NOT NULL)",
            name="scored_has_results",
        ),
        CheckConstraint(
            "(status = 'FAILED') = (failure_reason IS NOT NULL)", name="failed_has_reason"
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    run_reason: Mapped[RiskRunReason] = mapped_column(enum_type(RiskRunReason, "risk_run_reason"))
    status: Mapped[AssessmentStatus] = mapped_column(
        enum_type(AssessmentStatus, "assessment_status")
    )
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT"), index=True
    )
    affordability_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("affordability_assessments.id", ondelete="RESTRICT")
    )
    bureau_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("credit_bureau_reports.id", ondelete="RESTRICT")
    )
    feature_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    probability_of_default: Mapped[Decimal | None] = mapped_column(probability_type())
    risk_band: Mapped[RiskBand | None] = mapped_column(enum_type(RiskBand, "risk_band", length=1))
    decision_threshold: Mapped[Decimal | None] = mapped_column(probability_type())
    above_threshold: Mapped[bool | None]
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
