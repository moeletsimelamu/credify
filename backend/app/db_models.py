"""Import every ORM model so ``Base.metadata`` is complete (used by Alembic and tests)."""

from app.core.db import Base
from app.modules.affordability.models import AffordabilityAssessment
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
from app.modules.decisions.models import LendingDecision, LoanRecommendation
from app.modules.identity.models import RefreshToken, User
from app.modules.loans.models import (
    LoanAccount,
    LoanCharge,
    LoanTransaction,
    PaymentAllocation,
    RepaymentSchedule,
    ScheduleInstalment,
)
from app.modules.products.models import LoanProduct

__all__ = [
    "AffordabilityAssessment",
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
    "LendingDecision",
    "LoanAccount",
    "LoanApplication",
    "LoanCharge",
    "LoanProduct",
    "LoanRecommendation",
    "LoanTransaction",
    "PaymentAllocation",
    "RefreshToken",
    "RepaymentSchedule",
    "ReviewNote",
    "ScheduleInstalment",
    "User",
]
