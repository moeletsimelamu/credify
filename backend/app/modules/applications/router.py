"""Loan application endpoints. State changes are explicit action endpoints (ARCHITECTURE.md §6).

Transition endpoints accept an optional ``If-Match: "<version>"`` header; a stale version
returns 409 instead of overwriting a concurrent change.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.db import get_session
from app.core.deps import client_ip, if_match_version
from app.core.pagination import Page, PageParams, page_params
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
from app.modules.identity.dependencies import get_current_user, require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission

router = APIRouter(prefix="/applications", tags=["applications"])

_own = require(Permission.APPLICATION_CREATE_OWN)
_assign = require(Permission.APPLICATION_ASSIGN)
_request_info = require(Permission.APPLICATION_REQUEST_INFO)
_note = require(Permission.APPLICATION_NOTE)


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
