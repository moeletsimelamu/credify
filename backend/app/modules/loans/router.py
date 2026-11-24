"""Loan accounts, repayments, settlement quotes and schedule previews.

Everything here is simulated: no money moves and no payment provider is contacted.
Applicants see only their own loans (others are 404); staff with ``loan:read_any`` see all.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date, get_clock
from app.core.db import get_session
from app.core.deps import client_ip
from app.core.pagination import Page, PageParams, page_params
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.models import User
from app.modules.loans import service
from app.modules.loans.engine.ledger import SERVICING_VERSION, LoanStatus
from app.modules.loans.models import LoanCharge, LoanTransaction, RepaymentSchedule
from app.modules.loans.schemas import (
    AllocationRead,
    ChargeRead,
    EffectiveInstalmentRead,
    IdempotencyKey,
    InstalmentRead,
    LoanAccountRead,
    LoanScheduleRead,
    PaymentRead,
    RepaymentCreate,
    RepaymentSchedulePreview,
    ScheduleVersionDetail,
    ScheduleVersionRead,
    SettlementCreate,
    SettlementQuoteRead,
    TransactionRead,
)
from app.modules.products import service as products

router = APIRouter(tags=["repayment schedules"])
accounts = APIRouter(prefix="/loan-accounts", tags=["loan accounts"])

# Query strings are text, so amounts never pass through a float here.
AmountQuery = Query(gt=0, max_digits=18, decimal_places=2, description='e.g. "30000.00"')
TermQuery = Query(ge=1, le=120)
DateQuery = Query(None, description="Defaults to today's business date (SAST)")
IdempotencyHeader = Header(
    alias="Idempotency-Key",
    description="A unique key per payment (8–100 characters). Replaying it returns the "
    "original result instead of recording the payment twice.",
)


@router.get("/products/{product_id}/repayment-schedule", response_model=RepaymentSchedulePreview)
def preview_product_schedule(
    product_id: uuid.UUID,
    amount: Decimal = AmountQuery,
    term_months: int = TermQuery,
    disbursement_date: date | None = DateQuery,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(get_current_user),
) -> RepaymentSchedulePreview:
    product = products.get_product(session, product_id, include_inactive=user.is_staff)
    return service.preview_for_product(
        product, amount, term_months, disbursement_date or business_date(clock)
    )


@accounts.get("", response_model=Page[LoanAccountRead])
def list_loan_accounts(
    status_: list[LoanStatus] | None = Query(None, alias="status"),
    application_id: uuid.UUID | None = None,
    applicant_id: uuid.UUID | None = None,
    min_days_past_due: int | None = Query(None, ge=0),
    params: PageParams = Depends(page_params),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Page[LoanAccountRead]:
    filters = service.AccountFilters(
        statuses=tuple(status_ or ()),
        application_id=application_id,
        applicant_id=applicant_id,
        min_days_past_due=min_days_past_due,
    )
    rows, total = service.list_accounts(session, user, params, filters)
    return Page[LoanAccountRead](
        items=[LoanAccountRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@accounts.get("/{account_id}", response_model=LoanAccountRead)
def get_loan_account(
    account_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> LoanAccountRead:
    return LoanAccountRead.model_validate(service.load_account(session, account_id, user))


@accounts.get("/{account_id}/schedule", response_model=LoanScheduleRead)
def get_schedule(
    account_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> LoanScheduleRead:
    """The effective schedule, with what has been paid against each instalment."""
    account, book = service.effective_schedule(session, account_id, user)
    versions = {s.id: s.version for s in book.schedules}
    return LoanScheduleRead(
        loan_account_id=account.id,
        currency=account.currency,
        current_version=book.current_schedule.version,
        instalments=[
            EffectiveInstalmentRead.model_validate(
                {
                    **InstalmentRead.model_validate(row).model_dump(),
                    "schedule_version": versions[row.schedule_id],
                }
            )
            for _, row in sorted(book.instalments.items())
        ],
    )


@accounts.get("/{account_id}/schedules", response_model=list[ScheduleVersionRead])
def list_schedule_versions(
    account_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Sequence[RepaymentSchedule]:
    return service.list_schedules(session, account_id, user)


@accounts.get("/{account_id}/schedules/{version}", response_model=ScheduleVersionDetail)
def get_schedule_version(
    account_id: uuid.UUID,
    version: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ScheduleVersionDetail:
    schedule, rows = service.schedule_version(session, account_id, version, user)
    return ScheduleVersionDetail.model_validate(
        {
            **ScheduleVersionRead.model_validate(schedule).model_dump(),
            "instalments": [InstalmentRead.model_validate(row) for row in rows],
        }
    )


@accounts.get("/{account_id}/transactions", response_model=list[TransactionRead])
def list_transactions(
    account_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[TransactionRead]:
    """The account statement, oldest first, with each payment's allocations."""
    transactions, allocations = service.list_transactions(session, account_id, user)
    return [
        TransactionRead.model_validate(
            {
                **TransactionRead.model_validate(t).model_dump(exclude={"allocations"}),
                "allocations": [AllocationRead.model_validate(a) for a in allocations[t.id]],
            }
        )
        for t in transactions
    ]


@accounts.get("/{account_id}/charges", response_model=list[ChargeRead])
def list_charges(
    account_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Sequence[LoanCharge]:
    return service.list_charges(session, account_id, user)


def _transaction_read(session: Session, transaction: LoanTransaction) -> TransactionRead:
    return TransactionRead.model_validate(
        {
            **TransactionRead.model_validate(transaction).model_dump(exclude={"allocations"}),
            "allocations": [
                AllocationRead.model_validate(a)
                for a in service.allocations_for(session, transaction.id)
            ],
        }
    )


def _payment(session: Session, result: service.PaymentResult, response: Response) -> PaymentRead:
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return PaymentRead(
        transaction=_transaction_read(session, result.transaction),
        account=LoanAccountRead.model_validate(result.account),
        replayed=result.replayed,
    )


@accounts.post(
    "/{account_id}/repayments", response_model=PaymentRead, status_code=status.HTTP_201_CREATED
)
def record_repayment(
    account_id: uuid.UUID,
    data: RepaymentCreate,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = IdempotencyHeader,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(get_current_user),
) -> PaymentRead:
    """Record a simulated repayment (staff for any loan, applicants for their own)."""
    result = service.record_repayment(
        session,
        account_id,
        amount=data.amount,
        value_date=data.value_date,
        idempotency_key=idempotency_key,
        user=user,
        clock=clock,
        ip_address=client_ip(request),
    )
    return _payment(session, result, response)


@accounts.get("/{account_id}/settlement-quote", response_model=SettlementQuoteRead)
def get_settlement_quote(
    account_id: uuid.UUID,
    as_of: date | None = Query(None, description="Defaults to today's business date (SAST)"),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(get_current_user),
) -> SettlementQuoteRead:
    account, quote = service.settlement_quote(session, account_id, user, as_of=as_of, clock=clock)
    return SettlementQuoteRead.from_quote(account, quote, SERVICING_VERSION)


@accounts.post(
    "/{account_id}/settlement", response_model=PaymentRead, status_code=status.HTTP_201_CREATED
)
def settle(
    account_id: uuid.UUID,
    data: SettlementCreate,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey = IdempotencyHeader,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    user: User = Depends(get_current_user),
) -> PaymentRead:
    """Settle the loan today by paying exactly today's settlement quote."""
    result = service.settle(
        session,
        account_id,
        amount=data.amount,
        idempotency_key=idempotency_key,
        user=user,
        clock=clock,
        ip_address=client_ip(request),
    )
    return _payment(session, result, response)
