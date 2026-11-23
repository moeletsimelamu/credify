"""Applicant profile and financial-profile endpoints.

Reads: the owner, or staff with ``applicant:read_any``. Writes: the owner only, and only
while no application is under review (the profile is frozen so reviewers assess exactly
what was submitted).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.db import get_session
from app.core.deps import client_ip
from app.core.pagination import Page, PageParams, page_params
from app.modules.applicants import service
from app.modules.applicants.models import Applicant
from app.modules.applicants.schemas import (
    ApplicantCreate,
    ApplicantRead,
    ApplicantUpdate,
    CommitmentCreate,
    CommitmentRead,
    CommitmentUpdate,
    EmploymentCreate,
    EmploymentRead,
    EmploymentUpdate,
    ExpenseCreate,
    ExpenseRead,
    ExpenseUpdate,
    FinancialProfileRead,
    IncomeCreate,
    IncomeRead,
    IncomeUpdate,
)
from app.modules.applications import service as applications
from app.modules.identity.dependencies import get_current_user, require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission

router = APIRouter(prefix="/applicants", tags=["applicants"])
_write_own = require(Permission.APPLICANT_WRITE_OWN)
_read_any = require(Permission.APPLICANT_READ_ANY)


def _writable(session: Session, applicant_id: uuid.UUID, user: User) -> Applicant:
    # Row lock serialises profile edits with submission, which locks the same row.
    applicant = service.get_applicant_for_actor(session, applicant_id, user, for_update=True)
    reason = applications.profile_lock_reason(session, applicant.id)
    if reason:
        raise service.ProfileLockedError(reason)
    return applicant


class _Writer:
    """Shared plumbing for the four financial-record collections."""

    def __init__(self, kind: service.RecordKind) -> None:
        self.kind = kind

    def list(
        self, session: Session, applicant_id: uuid.UUID, user: User, include_inactive: bool
    ) -> list[Any]:
        applicant = service.get_applicant_for_actor(session, applicant_id, user)
        return service.list_records(
            session, self.kind, applicant, include_inactive=include_inactive and user.is_staff
        )

    def create(
        self,
        session: Session,
        applicant_id: uuid.UUID,
        data: BaseModel,
        user: User,
        clock: Clock,
        request: Request,
    ) -> Any:
        applicant = _writable(session, applicant_id, user)
        return service.create_record(
            session, self.kind, applicant, data,
            actor=user, clock=clock, ip_address=client_ip(request),
        )  # fmt: skip

    def update(
        self,
        session: Session,
        applicant_id: uuid.UUID,
        record_id: uuid.UUID,
        data: BaseModel,
        user: User,
        clock: Clock,
        request: Request,
    ) -> Any:
        applicant = _writable(session, applicant_id, user)
        return service.update_record(
            session, self.kind, applicant, record_id, data,
            actor=user, clock=clock, ip_address=client_ip(request),
        )  # fmt: skip

    def remove(
        self,
        session: Session,
        applicant_id: uuid.UUID,
        record_id: uuid.UUID,
        user: User,
        clock: Clock,
        request: Request,
    ) -> Response:
        applicant = _writable(session, applicant_id, user)
        service.remove_record(
            session, self.kind, applicant, record_id,
            actor=user, clock=clock, ip_address=client_ip(request),
        )  # fmt: skip
        return Response(status_code=status.HTTP_204_NO_CONTENT)


_employment = _Writer(service.EMPLOYMENT)
_income = _Writer(service.INCOME)
_expenses = _Writer(service.EXPENSE)
_commitments = _Writer(service.COMMITMENT)


# --- Profile -----------------------------------------------------------------------------


@router.post("", response_model=ApplicantRead, status_code=status.HTTP_201_CREATED)
def create_my_profile(
    data: ApplicantCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Applicant:
    return service.create_own_applicant(
        session, data, actor=user, clock=clock, ip_address=client_ip(request)
    )


@router.get("/me", response_model=ApplicantRead)
def get_my_profile(
    session: Session = Depends(get_session), user: User = Depends(_write_own)
) -> Applicant:
    return service.get_own_applicant(session, user)


@router.get("", response_model=Page[ApplicantRead])
def list_applicants(
    search: str | None = None,
    params: PageParams = Depends(page_params),
    session: Session = Depends(get_session),
    _: User = Depends(_read_any),
) -> Page[ApplicantRead]:
    rows, total = service.list_applicants(session, params, search=search)
    return Page[ApplicantRead](
        items=[ApplicantRead.model_validate(a) for a in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{applicant_id}", response_model=ApplicantRead)
def get_applicant(
    applicant_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Applicant:
    return service.get_applicant_for_actor(session, applicant_id, user)


@router.patch("/{applicant_id}", response_model=ApplicantRead)
def update_applicant(
    applicant_id: uuid.UUID,
    data: ApplicantUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Applicant:
    applicant = _writable(session, applicant_id, user)
    return service.update_applicant(
        session, applicant, data, actor=user, clock=clock, ip_address=client_ip(request)
    )


@router.get("/{applicant_id}/financial-profile", response_model=FinancialProfileRead)
def get_financial_profile(
    applicant_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> FinancialProfileRead:
    applicant = service.get_applicant_for_actor(session, applicant_id, user)
    return FinancialProfileRead.model_validate(
        {
            "applicant": applicant,
            "employment": service.list_records(session, service.EMPLOYMENT, applicant),
            "income": service.list_records(session, service.INCOME, applicant),
            "expenses": service.list_records(session, service.EXPENSE, applicant),
            "commitments": service.list_records(session, service.COMMITMENT, applicant),
        },
        from_attributes=True,
    )


# --- Employment --------------------------------------------------------------------------


@router.get("/{applicant_id}/employment", response_model=list[EmploymentRead])
def list_employment(
    applicant_id: uuid.UUID,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[Any]:
    return _employment.list(session, applicant_id, user, include_inactive)


@router.post(
    "/{applicant_id}/employment", response_model=EmploymentRead, status_code=status.HTTP_201_CREATED
)
def add_employment(
    applicant_id: uuid.UUID,
    data: EmploymentCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _employment.create(session, applicant_id, data, user, clock, request)


@router.patch("/{applicant_id}/employment/{record_id}", response_model=EmploymentRead)
def update_employment(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    data: EmploymentUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _employment.update(session, applicant_id, record_id, data, user, clock, request)


@router.delete("/{applicant_id}/employment/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_employment(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Response:
    return _employment.remove(session, applicant_id, record_id, user, clock, request)


# --- Income ------------------------------------------------------------------------------


@router.get("/{applicant_id}/income", response_model=list[IncomeRead])
def list_income(
    applicant_id: uuid.UUID,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[Any]:
    return _income.list(session, applicant_id, user, include_inactive)


@router.post(
    "/{applicant_id}/income", response_model=IncomeRead, status_code=status.HTTP_201_CREATED
)
def add_income(
    applicant_id: uuid.UUID,
    data: IncomeCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _income.create(session, applicant_id, data, user, clock, request)


@router.patch("/{applicant_id}/income/{record_id}", response_model=IncomeRead)
def update_income(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    data: IncomeUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _income.update(session, applicant_id, record_id, data, user, clock, request)


@router.delete("/{applicant_id}/income/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_income(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Response:
    return _income.remove(session, applicant_id, record_id, user, clock, request)


# --- Expenses ----------------------------------------------------------------------------


@router.get("/{applicant_id}/expenses", response_model=list[ExpenseRead])
def list_expenses(
    applicant_id: uuid.UUID,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[Any]:
    return _expenses.list(session, applicant_id, user, include_inactive)


@router.post(
    "/{applicant_id}/expenses", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED
)
def add_expense(
    applicant_id: uuid.UUID,
    data: ExpenseCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _expenses.create(session, applicant_id, data, user, clock, request)


@router.patch("/{applicant_id}/expenses/{record_id}", response_model=ExpenseRead)
def update_expense(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    data: ExpenseUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _expenses.update(session, applicant_id, record_id, data, user, clock, request)


@router.delete("/{applicant_id}/expenses/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_expense(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Response:
    return _expenses.remove(session, applicant_id, record_id, user, clock, request)


# --- Existing credit commitments ---------------------------------------------------------


@router.get("/{applicant_id}/commitments", response_model=list[CommitmentRead])
def list_commitments(
    applicant_id: uuid.UUID,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[Any]:
    return _commitments.list(session, applicant_id, user, include_inactive)


@router.post(
    "/{applicant_id}/commitments",
    response_model=CommitmentRead,
    status_code=status.HTTP_201_CREATED,
)
def add_commitment(
    applicant_id: uuid.UUID,
    data: CommitmentCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _commitments.create(session, applicant_id, data, user, clock, request)


@router.patch("/{applicant_id}/commitments/{record_id}", response_model=CommitmentRead)
def update_commitment(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    data: CommitmentUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Any:
    return _commitments.update(session, applicant_id, record_id, data, user, clock, request)


@router.delete("/{applicant_id}/commitments/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_commitment(
    applicant_id: uuid.UUID,
    record_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(_write_own),
) -> Response:
    return _commitments.remove(session, applicant_id, record_id, user, clock, request)
