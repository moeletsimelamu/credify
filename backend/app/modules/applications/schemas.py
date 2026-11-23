from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.money import Currency, MoneyAmount, PositiveMoney
from app.modules.applications.domain.enums import (
    ALLOWED_CONTENT_TYPES,
    MAX_DOCUMENT_BYTES,
    DocumentStatus,
    DocumentType,
    InformationRequestStatus,
    LoanPurpose,
    NoteType,
)
from app.modules.applications.domain.state_machine import Action, ApplicationStatus

TermMonths = Annotated[int, Field(ge=1, le=120)]
PurposeText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
OptionalReason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class ApplicationCreate(BaseModel):
    """Currency is taken from the product, never from the client."""

    model_config = ConfigDict(extra="forbid")
    loan_product_id: uuid.UUID
    requested_amount: PositiveMoney
    requested_term_months: TermMonths
    purpose: LoanPurpose
    purpose_description: PurposeText | None = None


class ApplicationUpdate(BaseModel):
    """Drafts only."""

    model_config = ConfigDict(extra="forbid")
    loan_product_id: uuid.UUID | None = None
    requested_amount: PositiveMoney | None = None
    requested_term_months: TermMonths | None = None
    purpose: LoanPurpose | None = None
    purpose_description: PurposeText | None = None


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, validate_by_name=True, validate_by_alias=True)
    id: uuid.UUID
    reference: str
    applicant_id: uuid.UUID
    loan_product_id: uuid.UUID
    requested_amount: MoneyAmount
    currency: Currency
    requested_term_months: int
    purpose: LoanPurpose
    purpose_description: str | None
    status: ApplicationStatus
    assigned_officer_id: uuid.UUID | None
    submitted_at: datetime | None
    decided_at: datetime | None
    version: int = Field(validation_alias="version_id")
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationRead):
    product_terms_snapshot: dict[str, object] | None
    # Actions the current user may attempt from this status (guards are checked on attempt).
    available_actions: list[Action]


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: OptionalReason | None = None


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    officer_id: uuid.UUID


class InformationRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Message


class InformationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_text: Message


class InformationRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    requested_by: uuid.UUID
    message: str
    status: InformationRequestStatus
    response_text: str | None
    responded_at: datetime | None
    created_at: datetime


class StatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    actor_user_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


class ReviewNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Message


class ReviewNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    author_id: uuid.UUID | None
    note_type: NoteType
    body: str
    created_at: datetime


FileName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class DocumentCreate(BaseModel):
    """Metadata for a simulated upload. No file content is accepted or stored."""

    model_config = ConfigDict(extra="forbid")
    document_type: DocumentType
    file_name: FileName
    content_type: str
    size_bytes: Annotated[int, Field(gt=0, le=MAX_DOCUMENT_BYTES)]

    @field_validator("file_name")
    @classmethod
    def _plain_file_name(cls, value: str) -> str:
        if any(ch in value for ch in '/\\:*?"<>|') or value.startswith(".") or ".." in value:
            raise ValueError("file_name must be a plain file name without path characters")
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("file_name must not contain control characters")
        return value

    @field_validator("content_type")
    @classmethod
    def _allowed_content_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"content_type must be one of {sorted(ALLOWED_CONTENT_TYPES)}")
        return value


class DocumentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[DocumentStatus.ACCEPTED, DocumentStatus.REJECTED]


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    application_id: uuid.UUID
    document_type: DocumentType
    file_name: str
    storage_key: str
    content_type: str
    size_bytes: int
    uploaded_by: uuid.UUID
    uploaded_at: datetime
    status: DocumentStatus
