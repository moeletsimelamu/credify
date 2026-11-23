"""Loan product use cases. Only administrators change products; everyone can read active ones."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, business_rule_error
from app.core.ids import uuid7
from app.core.pagination import PageParams
from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.products.models import LoanProduct
from app.modules.products.schemas import ProductCreate, ProductTerms, ProductUpdate, validate_terms

TERM_FIELDS = tuple(ProductTerms.model_fields)


def snapshot(product: LoanProduct) -> dict[str, Any]:
    """Every product field that affects a loan, for audit diffs and application snapshots."""
    return {
        "product_id": str(product.id),
        "code": product.code,
        "currency": product.currency.value,
        "is_active": product.is_active,
        **{field: getattr(product, field) for field in TERM_FIELDS},
    }


def terms_snapshot(product: LoanProduct, *, captured_at: datetime) -> dict[str, Any]:
    """The terms an application is assessed under, frozen at submission."""
    data = snapshot(product)
    data.pop("is_active")
    data.pop("description")
    return {**data, "captured_at": captured_at}


def create_product(
    session: Session,
    data: ProductCreate,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> LoanProduct:
    if session.scalar(select(func.count()).where(LoanProduct.code == data.code)):
        raise ConflictError(f"A product with code {data.code} already exists")
    product = LoanProduct(id=uuid7(), **data.model_dump())
    session.add(product)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError(f"A product with code {data.code} already exists") from exc
    audit.record(
        session,
        action="product.created",
        entity_type="loan_product",
        entity_id=product.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes=audit.diff({}, snapshot(product)),
    )
    session.commit()
    return product


def update_product(
    session: Session,
    product_id: uuid.UUID,
    data: ProductUpdate,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> LoanProduct:
    product = session.scalar(
        select(LoanProduct).where(LoanProduct.id == product_id).with_for_update()
    )
    if product is None:
        raise NotFoundError("Loan product not found")

    before = snapshot(product)
    # An explicit null only clears the optional description; elsewhere it means "unchanged".
    changes = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field == "description"
    }
    try:
        merged = ProductTerms.model_validate(
            {field: changes.get(field, getattr(product, field)) for field in TERM_FIELDS}
        )
    except ValidationError as exc:
        raise business_rule_error(exc) from exc
    problems = validate_terms(merged)
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})
    for field, value in changes.items():
        setattr(product, field, value)

    diff = audit.diff(before, snapshot(product))
    if not diff:
        return product
    session.flush()
    audit.record(
        session,
        action="product.updated",
        entity_type="loan_product",
        entity_id=product.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes=diff,
    )
    session.commit()
    session.refresh(product)
    return product


def get_product(
    session: Session, product_id: uuid.UUID, *, include_inactive: bool = False
) -> LoanProduct:
    product = session.get(LoanProduct, product_id)
    if product is None or (not product.is_active and not include_inactive):
        raise NotFoundError("Loan product not found")
    return product


def list_products(
    session: Session, params: PageParams, *, include_inactive: bool = False
) -> tuple[list[LoanProduct], int]:
    query = select(LoanProduct)
    if not include_inactive:
        query = query.where(LoanProduct.is_active.is_(True))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(LoanProduct.name, LoanProduct.id)
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    return list(rows), total
