"""Repayment schedule previews (FINANCIAL_CALCULATIONS.md §3). Read-only: nothing is stored."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date, get_clock
from app.core.db import get_session
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.models import User
from app.modules.loans import service
from app.modules.loans.schemas import RepaymentSchedulePreview
from app.modules.products import service as products

router = APIRouter(tags=["repayment schedules"])

# Query strings are text, so amounts never pass through a float here.
AmountQuery = Query(gt=0, max_digits=18, decimal_places=2, description='e.g. "30000.00"')
TermQuery = Query(ge=1, le=120)
DateQuery = Query(None, description="Defaults to today's business date (SAST)")


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
