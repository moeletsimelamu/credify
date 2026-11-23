from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.db import get_session
from app.core.deps import client_ip
from app.core.pagination import Page, PageParams, page_params
from app.modules.identity.dependencies import get_current_user, require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission
from app.modules.products import service
from app.modules.products.models import LoanProduct
from app.modules.products.schemas import ProductCreate, ProductRead, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])
_manage_products = require(Permission.PRODUCT_MANAGE)


def _can_see_inactive(user: User) -> bool:
    # Staff need inactive products to read older applications; applicants only see the catalogue.
    return user.is_staff


@router.get("", response_model=Page[ProductRead])
def list_products(
    include_inactive: bool = False,
    params: PageParams = Depends(page_params),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Page[ProductRead]:
    rows, total = service.list_products(
        session, params, include_inactive=include_inactive and _can_see_inactive(user)
    )
    return Page[ProductRead](
        items=[ProductRead.model_validate(p) for p in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{product_id}", response_model=ProductRead)
def get_product(
    product_id: uuid.UUID,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> LoanProduct:
    return service.get_product(session, product_id, include_inactive=_can_see_inactive(user))


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    data: ProductCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    actor: User = Depends(_manage_products),
) -> LoanProduct:
    return service.create_product(
        session, data, actor=actor, clock=clock, ip_address=client_ip(request)
    )


@router.patch("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    actor: User = Depends(_manage_products),
) -> LoanProduct:
    return service.update_product(
        session, product_id, data, actor=actor, clock=clock, ip_address=client_ip(request)
    )
