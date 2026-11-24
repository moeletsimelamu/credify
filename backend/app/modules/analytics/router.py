from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date, get_clock
from app.core.db import get_session
from app.core.errors import BusinessRuleError
from app.core.money import Currency, MoneyAmount
from app.modules.analytics import service
from app.modules.identity.dependencies import require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission
from app.modules.loans.engine.ledger import ArrearsBucket

router = APIRouter(prefix="/analytics", tags=["analytics"])

DEFAULT_PERIOD_DAYS = 365


class BucketRead(BaseModel):
    bucket: ArrearsBucket
    accounts: int
    outstanding_principal: MoneyAmount


class CurrencyPortfolioRead(BaseModel):
    currency: Currency
    applications_by_status: dict[str, int]
    decisions_approved: int
    decisions_declined: int
    approval_rate: Decimal | None
    loans_by_status: dict[str, int]
    loans_disbursed: int
    principal_disbursed: MoneyAmount
    outstanding_principal: MoneyAmount
    outstanding_principal_in_arrears: MoneyAmount
    arrears_rate: Decimal | None
    repayments_received: MoneyAmount
    amount_due: MoneyAmount
    amount_collected: MoneyAmount
    collection_rate: Decimal | None
    arrears_buckets: list[BucketRead]


class PortfolioRead(BaseModel):
    date_from: date
    date_to: date
    collections_through: date
    currencies: list[CurrencyPortfolioRead]
    note: str = (
        "Synthetic, educational data. Arrears figures reflect each account's position as of "
        "its last evaluation (see position_as_of on the account)."
    )


@router.get("/portfolio", response_model=PortfolioRead)
def get_portfolio(
    date_from: date | None = Query(None, description="Defaults to 365 days before date_to"),
    date_to: date | None = Query(None, description="Defaults to today's business date"),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    _: User = Depends(require(Permission.ANALYTICS_PORTFOLIO)),
) -> PortfolioRead:
    today = business_date(clock)
    end = date_to or today
    start = date_from or end - timedelta(days=DEFAULT_PERIOD_DAYS)
    if start > end:
        raise BusinessRuleError("date_from must not be after date_to")
    result = service.portfolio(session, date_from=start, date_to=end, today=today)
    return PortfolioRead(
        date_from=result.date_from,
        date_to=result.date_to,
        collections_through=result.collections_through,
        currencies=[
            CurrencyPortfolioRead(
                currency=c.currency,
                applications_by_status=c.applications_by_status,
                decisions_approved=c.approved,
                decisions_declined=c.declined,
                approval_rate=c.approval_rate,
                loans_by_status=c.loans_by_status,
                loans_disbursed=c.loans_disbursed,
                principal_disbursed=c.principal_disbursed,
                outstanding_principal=c.outstanding_principal,
                outstanding_principal_in_arrears=c.outstanding_principal_in_arrears,
                arrears_rate=c.arrears_rate,
                repayments_received=c.repayments_received,
                amount_due=c.amount_due,
                amount_collected=c.amount_collected,
                collection_rate=c.collection_rate,
                arrears_buckets=[
                    BucketRead(
                        bucket=bucket,
                        accounts=figures.accounts,
                        outstanding_principal=figures.outstanding_principal,
                    )
                    for bucket, figures in c.arrears_buckets.items()
                ],
            )
            for c in result.currencies
        ],
    )
