"""Loan application endpoints. State changes are explicit action endpoints (ARCHITECTURE.md §6).

Transition endpoints accept an optional ``If-Match: "<version>"`` header; a stale version
returns 409 instead of overwriting a concurrent change.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date, get_clock
from app.core.db import get_session
from app.core.deps import client_ip, if_match_version
from app.core.pagination import Page, PageParams, page_params
from app.modules.affordability.models import AffordabilityAssessment
from app.modules.affordability.schemas import AffordabilityAssessmentRead
from app.modules.applications import service
from app.modules.applications.domain.state_machine import ApplicationStatus
from app.modules.applications.models import (
    ApplicationStatusHistory,
    DocumentReference,
    InformationRequest,
    LoanApplication,
    ReviewNote,
)
from app.modules.applications.schemas import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationRead,
    ApplicationUpdate,
    AssignRequest,
    DocumentCreate,
    DocumentRead,
    DocumentReview,
    InformationRequestCreate,
    InformationRequestRead,
    InformationResponse,
    ReviewNoteCreate,
    ReviewNoteRead,
    StatusHistoryRead,
    WithdrawRequest,
)
from app.modules.decisions.domain.reason_codes import ReasonCode
from app.modules.decisions.models import LendingDecision, LoanRecommendation
from app.modules.decisions.schemas import (
    ApplicantDecisionRead,
    DecisionCreate,
    DecisionRead,
    ReasonRead,
    RecommendationCreate,
    RecommendationRead,
    SendBackRequest,
)
from app.modules.identity.dependencies import get_current_user, require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission
from app.modules.loans.schemas import LoanAccountRead, RepaymentSchedulePreview

router = APIRouter(prefix="/applications", tags=["applications"])

_own = require(Permission.APPLICATION_CREATE_OWN)
_assign = require(Permission.APPLICATION_ASSIGN)
_request_info = require(Permission.APPLICATION_REQUEST_INFO)
_note = require(Permission.APPLICATION_NOTE)
_run_affordability = require(Permission.AFFORDABILITY_RUN)
_recommend = require(Permission.APPLICATION_RECOMMEND)
_decide = require(Permission.APPLICATION_DECIDE)
_disburse = require(Permission.LOAN_DISBURSE)
_read_any = require(Permission.APPLICATION_READ_ANY)


def _ctx(request: Request, user: User, clock: Clock) -> service.Context:
    return service.Context(user=user, clock=clock, ip_address=client_ip(request))


def _detail(session: Session, application: LoanApplication, user: User) -> ApplicationDetail:
    loaded = service.load(session, application.id, user)
    return ApplicationDetail.model_validate(
        {
            **ApplicationRead.model_validate(loaded.application).model_dump(),
            "product_terms_snapshot": loaded.application.product_terms_snapshot,
            "available_actions": service.actions_for(loaded, user),
        }
    )


# --- Collection ----------------------------------------------------------------------------


@router.get("", response_model=Page[ApplicationRead])
def list_applications(
    status_: list[ApplicationStatus] | None = Query(None, alias="status"),
    loan_product_id: uuid.UUID | None = None,
    assigned_officer_id: uuid.UUID | None = None,
    applicant_id: uuid.UUID | None = None,
    submitted_from: datetime | None = None,
    submitted_to: datetime | None = None,
    params: PageParams = Depends(page_params),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Page[ApplicationRead]:
    filters = service.ApplicationFilters(
        statuses=tuple(status_ or ()),
        loan_product_id=loan_product_id,
        assigned_officer_id=assigned_officer_id,
        applicant_id=applicant_id,
        submitted_from=submitted_from,
        submitted_to=submitted_to,
    )
    rows, total = service.list_applications(session, user, params, filters)
    return Page[ApplicationRead](
        items=[ApplicationRead.model_validate(a) for a in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.post("", response_model=ApplicationDetail, status_code=status.HTTP_201_CREATED)
def create_application(
    data: ApplicationCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> ApplicationDetail:
    application = service.create_application(session, data, _ctx(request, user, clock))
    return _detail(session, application, user)


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ApplicationDetail:
    loaded = service.load(session, application_id, user)
    return _detail(session, loaded.application, user)


@router.patch("/{application_id}", response_model=ApplicationDetail)
def update_draft(
    application_id: uuid.UUID,
    data: ApplicationUpdate,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> ApplicationDetail:
    application = service.update_draft(
        session, application_id, data, _ctx(request, user, clock), expected_version=expected_version
    )
    return _detail(session, application, user)


# --- Applicant actions ---------------------------------------------------------------------


@router.post("/{application_id}/submit", response_model=ApplicationDetail)
def submit(
    application_id: uuid.UUID,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> ApplicationDetail:
    application = service.submit(
        session, application_id, _ctx(request, user, clock), expected_version=expected_version
    )
    return _detail(session, application, user)


@router.post("/{application_id}/withdraw", response_model=ApplicationDetail)
def withdraw(
    application_id: uuid.UUID,
    request: Request,
    data: WithdrawRequest | None = None,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> ApplicationDetail:
    application = service.withdraw(
        session,
        application_id,
        _ctx(request, user, clock),
        reason=data.reason if data else None,
        expected_version=expected_version,
    )
    return _detail(session, application, user)


@router.post("/{application_id}/resume-review", response_model=ApplicationDetail)
def resume_review(
    application_id: uuid.UUID,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> ApplicationDetail:
    """Send the application back to the review queue once every request is answered.

    The financial profile is re-assessed first, so reviewers see the updated figures.
    """
    application = service.resume_review(
        session, application_id, _ctx(request, user, clock), expected_version=expected_version
    )
    return _detail(session, application, user)


@router.post(
    "/{application_id}/information-requests/{request_id}/respond",
    response_model=InformationRequestRead,
)
def respond_to_information_request(
    application_id: uuid.UUID,
    request_id: uuid.UUID,
    data: InformationResponse,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> InformationRequest:
    return service.respond_to_information_request(
        session, application_id, request_id, data.response_text, _ctx(request, user, clock)
    )


@router.post(
    "/{application_id}/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def add_document(
    application_id: uuid.UUID,
    data: DocumentCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_own),
) -> DocumentReference:
    return service.add_document(session, application_id, data, _ctx(request, user, clock))


# --- Staff actions -------------------------------------------------------------------------


@router.post("/{application_id}/assign", response_model=ApplicationDetail)
def assign(
    application_id: uuid.UUID,
    data: AssignRequest,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_assign),
) -> ApplicationDetail:
    application = service.assign(
        session,
        application_id,
        data.officer_id,
        _ctx(request, user, clock),
        expected_version=expected_version,
    )
    return _detail(session, application, user)


@router.post("/{application_id}/start-review", response_model=ApplicationDetail)
def start_review(
    application_id: uuid.UUID,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_assign),
) -> ApplicationDetail:
    application = service.start_review(
        session, application_id, _ctx(request, user, clock), expected_version=expected_version
    )
    return _detail(session, application, user)


@router.post(
    "/{application_id}/request-information",
    response_model=InformationRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def request_information(
    application_id: uuid.UUID,
    data: InformationRequestCreate,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_request_info),
) -> InformationRequest:
    return service.request_information(
        session,
        application_id,
        data.message,
        _ctx(request, user, clock),
        expected_version=expected_version,
    )


@router.patch("/{application_id}/documents/{document_id}", response_model=DocumentRead)
def review_document(
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    data: DocumentReview,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_request_info),
) -> DocumentReference:
    return service.review_document(
        session, application_id, document_id, data.status, _ctx(request, user, clock)
    )


@router.get("/{application_id}/notes", response_model=list[ReviewNoteRead])
def list_notes(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(_note),
) -> Sequence[ReviewNote]:
    return service.list_notes(session, application_id, user)


@router.post(
    "/{application_id}/notes", response_model=ReviewNoteRead, status_code=status.HTTP_201_CREATED
)
def add_note(
    application_id: uuid.UUID,
    data: ReviewNoteCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_note),
) -> ReviewNote:
    return service.add_note(session, application_id, data.body, _ctx(request, user, clock))


# --- Shared reads (owner or staff) ---------------------------------------------------------


@router.get("/{application_id}/status-history", response_model=list[StatusHistoryRead])
def list_status_history(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Sequence[ApplicationStatusHistory]:
    return service.list_history(session, application_id, user)


@router.get("/{application_id}/information-requests", response_model=list[InformationRequestRead])
def list_information_requests(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Sequence[InformationRequest]:
    return service.list_information_requests(session, application_id, user)


@router.get("/{application_id}/documents", response_model=list[DocumentRead])
def list_documents(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Sequence[DocumentReference]:
    return service.list_documents(session, application_id, user)


# --- Affordability and repayment schedule -------------------------------------------------


@router.get(
    "/{application_id}/affordability-assessments",
    response_model=list[AffordabilityAssessmentRead],
)
def list_affordability_assessments(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Sequence[AffordabilityAssessment]:
    """Every version, newest first. Applicants see their own (the calculation is open)."""
    return service.list_affordability(session, application_id, user)


@router.post(
    "/{application_id}/affordability-assessments",
    response_model=AffordabilityAssessmentRead,
    status_code=status.HTTP_201_CREATED,
)
def run_affordability_assessment(
    application_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_run_affordability),
) -> AffordabilityAssessment:
    """Re-run on the applicant's current profile, creating a new version."""
    return service.run_affordability(session, application_id, _ctx(request, user, clock))


@router.get(
    "/{application_id}/affordability-assessments/{assessment_id}",
    response_model=AffordabilityAssessmentRead,
)
def get_affordability_assessment(
    application_id: uuid.UUID,
    assessment_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> AffordabilityAssessment:
    return service.get_affordability(session, application_id, assessment_id, user)


@router.get("/{application_id}/repayment-schedule", response_model=RepaymentSchedulePreview)
def get_repayment_schedule(
    application_id: uuid.UUID,
    disbursement_date: date | None = Query(
        None, description="Defaults to today's business date (SAST)"
    ),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(get_current_user),
) -> RepaymentSchedulePreview:
    """An illustrative schedule for the requested loan. Nothing is stored."""
    return service.repayment_schedule(
        session,
        application_id,
        user,
        disbursement_date=disbursement_date or business_date(clock),
    )


# --- Recommendation, decision and disbursement --------------------------------------------


@router.post(
    "/{application_id}/recommendation",
    response_model=RecommendationRead,
    status_code=status.HTTP_201_CREATED,
)
def recommend(
    application_id: uuid.UUID,
    data: RecommendationCreate,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_recommend),
) -> LoanRecommendation:
    """A loan officer recommends approval or rejection, citing the latest assessment."""
    return service.recommend(
        session,
        application_id,
        data,
        _ctx(request, user, clock),
        expected_version=expected_version,
    )


@router.get("/{application_id}/recommendations", response_model=list[RecommendationRead])
def list_recommendations(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(_read_any),
) -> Sequence[LoanRecommendation]:
    """Every recommendation, oldest first (staff only: rationales are internal)."""
    return service.list_recommendations(session, application_id, user)


@router.post("/{application_id}/send-back", response_model=ApplicationDetail)
def send_back(
    application_id: uuid.UUID,
    data: SendBackRequest,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_decide),
) -> ApplicationDetail:
    """Return a recommended application to review instead of deciding it."""
    application = service.send_back(
        session,
        application_id,
        data.reason,
        _ctx(request, user, clock),
        expected_version=expected_version,
    )
    return _detail(session, application, user)


@router.post(
    "/{application_id}/decision", response_model=DecisionRead, status_code=status.HTTP_201_CREATED
)
def decide(
    application_id: uuid.UUID,
    data: DecisionCreate,
    request: Request,
    expected_version: int | None = Depends(if_match_version),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_decide),
) -> LendingDecision:
    """Record the final decision. The decider must not be the recommender (maker-checker)."""
    return service.decide(
        session,
        application_id,
        data,
        _ctx(request, user, clock),
        expected_version=expected_version,
    )


@router.get("/{application_id}/decision", response_model=DecisionRead | ApplicantDecisionRead)
def get_decision(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> DecisionRead | ApplicantDecisionRead:
    """Staff see the full record. Applicants see the outcome, reasons and approved terms."""
    decision = service.get_decision(session, application_id, user)
    if user.is_staff:
        return DecisionRead.model_validate(decision)
    return ApplicantDecisionRead(
        application_id=decision.application_id,
        decision=decision.decision,
        reasons=[ReasonRead.for_code(ReasonCode(code)) for code in decision.reason_codes],
        approved_amount=decision.approved_amount,
        approved_term_months=decision.approved_term_months,
        decided_at=decision.created_at,
    )


@router.post(
    "/{application_id}/disburse",
    response_model=LoanAccountRead,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Already disbursed: the existing account is returned"}},
)
def disburse(
    application_id: uuid.UUID,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_disburse),
) -> LoanAccountRead:
    """Open the loan account (simulated disbursement today). Safe to repeat."""
    account, created = service.disburse(session, application_id, _ctx(request, user, clock))
    if not created:
        response.status_code = status.HTTP_200_OK
    return LoanAccountRead.model_validate(account)


@router.get("/{application_id}/loan-account", response_model=LoanAccountRead)
def get_loan_account(
    application_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> LoanAccountRead:
    return LoanAccountRead.model_validate(service.get_loan_account(session, application_id, user))
