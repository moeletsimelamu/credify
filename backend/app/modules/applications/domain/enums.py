"""Application vocabularies (DATABASE.md §3.4). Pure: no SQLAlchemy, no I/O."""

from __future__ import annotations

from enum import StrEnum


class LoanPurpose(StrEnum):
    DEBT_CONSOLIDATION = "DEBT_CONSOLIDATION"
    HOME_IMPROVEMENT = "HOME_IMPROVEMENT"
    EDUCATION = "EDUCATION"
    MEDICAL = "MEDICAL"
    VEHICLE = "VEHICLE"
    BUSINESS = "BUSINESS"
    PERSONAL = "PERSONAL"
    OTHER = "OTHER"


class NoteType(StrEnum):
    OFFICER_NOTE = "OFFICER_NOTE"
    ANALYST_ASSESSMENT = "ANALYST_ASSESSMENT"
    SYSTEM = "SYSTEM"


class InformationRequestStatus(StrEnum):
    OPEN = "OPEN"
    RESPONDED = "RESPONDED"
    CANCELLED = "CANCELLED"


class DocumentType(StrEnum):
    PAYSLIP = "PAYSLIP"
    BANK_STATEMENT = "BANK_STATEMENT"
    ID_DOCUMENT = "ID_DOCUMENT"
    PROOF_OF_ADDRESS = "PROOF_OF_ADDRESS"
    OTHER = "OTHER"


class DocumentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


# Document references are metadata only in v1: no file bytes are stored or transferred.
ALLOWED_CONTENT_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
