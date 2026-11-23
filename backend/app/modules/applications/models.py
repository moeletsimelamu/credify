from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, Timestamps, UUIDPrimaryKey, enum_type, money_type
from app.core.money import Currency
from app.modules.applications.domain.enums import (
    DocumentStatus,
    DocumentType,
    InformationRequestStatus,
    LoanPurpose,
    NoteType,
)
from app.modules.applications.domain.state_machine import TERMINAL, ApplicationStatus

REFERENCE_SEQUENCE = Sequence("loan_application_reference_seq", metadata=Base.metadata)

_terminal_sql = ", ".join(f"'{status.value}'" for status in sorted(TERMINAL))


class LoanApplication(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "loan_applications"
    __table_args__ = (
        CheckConstraint("requested_amount > 0", name="amount_positive"),
        CheckConstraint("requested_term_months BETWEEN 1 AND 120", name="term_range"),
        Index("ix_loan_applications_status_submitted_at", "status", "submitted_at"),
        Index("ix_loan_applications_assigned_officer_status", "assigned_officer_id", "status"),
        # One open application per applicant: assessments always describe a single request.
        Index(
            "uq_loan_applications_one_open_per_applicant",
            "applicant_id",
            unique=True,
            postgresql_where=text(f"status NOT IN ({_terminal_sql})"),
        ),
    )

    reference: Mapped[str] = mapped_column(String(20), unique=True)
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applicants.id", ondelete="RESTRICT"), index=True
    )
    loan_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_products.id", ondelete="RESTRICT")
    )
    requested_amount: Mapped[Decimal] = mapped_column(money_type())
    currency: Mapped[Currency] = mapped_column(enum_type(Currency, "currency", length=3))
    requested_term_months: Mapped[int] = mapped_column(SmallInteger)
    purpose: Mapped[LoanPurpose] = mapped_column(enum_type(LoanPurpose, "loan_purpose"))
    purpose_description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus, "application_status")
    )
    product_terms_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assigned_officer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_id: Mapped[int] = mapped_column(Integer, server_default="1")

    # Optimistic locking: every UPDATE checks and increments version_id, so a write based on
    # a stale read fails with StaleDataError (mapped to HTTP 409).
    __mapper_args__ = {"version_id_col": version_id}  # noqa: RUF012


class ApplicationStatusHistory(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only: every status change, who made it and why."""

    __tablename__ = "application_status_history"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), index=True
    )
    from_status: Mapped[ApplicationStatus | None] = mapped_column(
        enum_type(ApplicationStatus, "from_status")
    )
    to_status: Mapped[ApplicationStatus] = mapped_column(enum_type(ApplicationStatus, "to_status"))
    # NULL actor means the system made the change.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reason: Mapped[str | None] = mapped_column(Text)


class ReviewNote(UUIDPrimaryKey, CreatedAt, Base):
    """Append-only internal notes. Applicants never see these."""

    __tablename__ = "review_notes"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    note_type: Mapped[NoteType] = mapped_column(enum_type(NoteType, "note_type"))
    body: Mapped[str] = mapped_column(Text)


class InformationRequest(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "information_requests"
    __table_args__ = (
        CheckConstraint(
            "(status = 'RESPONDED') = (response_text IS NOT NULL AND responded_at IS NOT NULL)",
            name="response_matches_status",
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), index=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[InformationRequestStatus] = mapped_column(
        enum_type(InformationRequestStatus, "information_request_status")
    )
    response_text: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentReference(UUIDPrimaryKey, Base):
    """Metadata about a supporting document. v1 stores no file content (simulated storage)."""

    __tablename__ = "document_references"
    __table_args__ = (CheckConstraint("size_bytes > 0", name="size_positive"),)

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loan_applications.id", ondelete="RESTRICT"), index=True
    )
    document_type: Mapped[DocumentType] = mapped_column(enum_type(DocumentType, "document_type"))
    file_name: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[DocumentStatus] = mapped_column(enum_type(DocumentStatus, "document_status"))
