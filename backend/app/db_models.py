"""Import every ORM model so ``Base.metadata`` is complete (used by Alembic and tests)."""

from app.core.db import Base
from app.modules.applicants.models import (
    Applicant,
    CreditCommitment,
    EmploymentRecord,
    ExpenseRecord,
    IncomeRecord,
)
from app.modules.applications.models import (
    ApplicationStatusHistory,
    DocumentReference,
    InformationRequest,
    LoanApplication,
    ReviewNote,
)
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import RefreshToken, User
from app.modules.products.models import LoanProduct

__all__ = [
    "Applicant",
    "ApplicationStatusHistory",
    "AuditEvent",
    "Base",
    "CreditCommitment",
    "DocumentReference",
    "EmploymentRecord",
    "ExpenseRecord",
    "IncomeRecord",
    "InformationRequest",
    "LoanApplication",
    "LoanProduct",
    "RefreshToken",
    "ReviewNote",
    "User",
]
