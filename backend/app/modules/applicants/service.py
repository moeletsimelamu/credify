"""Applicant profile and financial-profile use cases.

Object-level rule: an applicant only ever reaches their own profile. Any other applicant's
profile is reported as "not found" so its existence is never confirmed (ARCHITECTURE.md §4).
Staff may read every profile but cannot change one.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, business_rule_error
from app.core.ids import uuid7
from app.core.money import Currency
from app.core.pagination import PageParams
from app.modules.applicants.domain.eligibility import (
    MINIMUM_AGE,
    age_on,
    date_of_birth_problems,
)
from app.modules.applicants.models import (
    Applicant,
    CreditCommitment,
    EmploymentRecord,
    ExpenseRecord,
    IncomeRecord,
)
from app.modules.applicants.schemas import (
    ApplicantCreate,
    ApplicantFields,
    ApplicantUpdate,
    CommitmentFields,
    EmploymentFields,
    ExpenseFields,
    IncomeFields,
)
from app.modules.audit import service as audit
from app.modules.identity.models import Role, User
from app.modules.identity.permissions import Permission, has_permission

APPLICANT_FIELDS = tuple(ApplicantFields.model_fields)
AnyRecord = EmploymentRecord | IncomeRecord | ExpenseRecord | CreditCommitment


class ProfileLockedError(ConflictError):
    code = "PROFILE_LOCKED"
    title = "The applicant profile cannot be changed right now"


def _snapshot(obj: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(obj, field) for field in fields}


def _validated[M: BaseModel](schema: type[M], data: Mapping[str, Any]) -> M:
    try:
        return schema.model_validate(dict(data))
    except ValidationError as exc:
        raise business_rule_error(exc) from exc


# --- Applicant profile -------------------------------------------------------------------


def find_own_applicant(session: Session, user: User) -> Applicant | None:
    return session.scalar(select(Applicant).where(Applicant.user_id == user.id))


def get_own_applicant(session: Session, user: User) -> Applicant:
    applicant = find_own_applicant(session, user)
    if applicant is None:
        raise NotFoundError("You have not created an applicant profile yet")
    return applicant


def get_applicant_for_actor(
    session: Session, applicant_id: uuid.UUID, actor: User, *, for_update: bool = False
) -> Applicant:
    query = select(Applicant).where(Applicant.id == applicant_id)
    if not has_permission(actor.role, Permission.APPLICANT_READ_ANY):
        query = query.where(Applicant.user_id == actor.id)
    if for_update:
        query = query.with_for_update()
    applicant = session.scalar(query)
    if applicant is None:
        raise NotFoundError("Applicant not found")
    return applicant


def _generate_synthetic_reference() -> str:
    return f"SYN-{secrets.token_hex(5).upper()}"


def create_own_applicant(
    session: Session,
    data: ApplicantCreate,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> Applicant:
    if actor.role is not Role.APPLICANT:
        raise BusinessRuleError("Only applicant accounts can create their own applicant profile")
    if find_own_applicant(session, actor) is not None:
        raise ConflictError("You already have an applicant profile")
    problems = date_of_birth_problems(data.date_of_birth, business_date(clock))
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})

    values = data.model_dump()
    values["email"] = values["email"] or actor.email
    applicant = Applicant(
        id=uuid7(),
        user_id=actor.id,
        created_by=actor.id,
        synthetic_reference=_generate_synthetic_reference(),
        **values,
    )
    session.add(applicant)
    try:
        session.flush()
    except IntegrityError as exc:  # concurrent create for the same user
        raise ConflictError("You already have an applicant profile") from exc
    audit.record(
        session,
        action="applicant.created",
        entity_type="applicant",
        entity_id=applicant.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes=audit.diff({}, _snapshot(applicant, APPLICANT_FIELDS)),
    )
    session.commit()
    return applicant


def update_applicant(
    session: Session,
    applicant: Applicant,
    data: ApplicantUpdate,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> Applicant:
    before = _snapshot(applicant, APPLICANT_FIELDS)
    merged = _validated(ApplicantFields, {**before, **data.model_dump(exclude_unset=True)})
    problems = date_of_birth_problems(merged.date_of_birth, business_date(clock))
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})
    after = merged.model_dump()
    changes = audit.diff(before, after)
    if not changes:
        return applicant
    for field, value in after.items():
        setattr(applicant, field, value)
    session.flush()
    audit.record(
        session,
        action="applicant.updated",
        entity_type="applicant",
        entity_id=applicant.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes=changes,
    )
    session.commit()
    session.refresh(applicant)
    return applicant


def list_applicants(
    session: Session, params: PageParams, *, search: str | None = None
) -> tuple[list[Applicant], int]:
    query = select(Applicant)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Applicant.first_name.ilike(pattern),
                Applicant.last_name.ilike(pattern),
                Applicant.email.ilike(pattern),
                Applicant.synthetic_reference.ilike(pattern),
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(Applicant.last_name, Applicant.first_name, Applicant.id)
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    return list(rows), total


# --- Financial records (employment, income, expenses, commitments) -----------------------


def _employment_problems(fields: BaseModel, today: date) -> list[str]:
    assert isinstance(fields, EmploymentFields)  # noqa: S101 — registry wiring invariant
    problems = []
    if fields.start_date and fields.start_date > today:
        problems.append("start_date cannot be in the future")
    if fields.end_date and fields.end_date > today:
        problems.append("end_date cannot be in the future")
    return problems


@dataclass(frozen=True, slots=True)
class RecordKind:
    """How one financial-record table is validated and audited."""

    model: type[AnyRecord]
    fields: type[BaseModel]
    entity_type: str
    label: str
    extra_rules: Callable[[BaseModel, date], list[str]] | None = None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(self.fields.model_fields)


EMPLOYMENT = RecordKind(
    EmploymentRecord, EmploymentFields, "employment_record", "Employment record",
    _employment_problems,
)  # fmt: skip
INCOME = RecordKind(IncomeRecord, IncomeFields, "income_record", "Income record")
EXPENSE = RecordKind(ExpenseRecord, ExpenseFields, "expense_record", "Expense record")
COMMITMENT = RecordKind(
    CreditCommitment, CommitmentFields, "credit_commitment", "Credit commitment"
)


def list_records(
    session: Session, kind: RecordKind, applicant: Applicant, *, include_inactive: bool = False
) -> list[Any]:
    query = select(kind.model).where(kind.model.applicant_id == applicant.id)
    if not include_inactive:
        query = query.where(kind.model.is_active.is_(True))
    return list(session.scalars(query.order_by(kind.model.created_at, kind.model.id)))


def _check_rules(kind: RecordKind, fields: BaseModel, clock: Clock) -> None:
    problems = kind.extra_rules(fields, business_date(clock)) if kind.extra_rules else []
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})


def create_record(
    session: Session,
    kind: RecordKind,
    applicant: Applicant,
    data: BaseModel,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> Any:
    fields = _validated(kind.fields, data.model_dump())
    _check_rules(kind, fields, clock)
    record = kind.model(id=uuid7(), applicant_id=applicant.id, **fields.model_dump())
    session.add(record)
    session.flush()
    audit.record(
        session,
        action=f"{kind.entity_type}.created",
        entity_type=kind.entity_type,
        entity_id=record.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes=audit.diff({}, _snapshot(record, kind.field_names)),
        context={"applicant_id": applicant.id},
    )
    session.commit()
    session.refresh(record)
    return record


def _get_active_record(
    session: Session, kind: RecordKind, applicant: Applicant, record_id: uuid.UUID
) -> Any:
    record = session.scalar(
        select(kind.model)
        .where(
            kind.model.id == record_id,
            kind.model.applicant_id == applicant.id,
            kind.model.is_active.is_(True),
        )
        .with_for_update()
    )
    if record is None:
        raise NotFoundError(f"{kind.label} not found")
    return record


def update_record(
    session: Session,
    kind: RecordKind,
    applicant: Applicant,
    record_id: uuid.UUID,
    data: BaseModel,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> Any:
    record = _get_active_record(session, kind, applicant, record_id)
    before = _snapshot(record, kind.field_names)
    # Re-validate the merged record so cross-field rules hold after a partial update.
    fields = _validated(kind.fields, {**before, **data.model_dump(exclude_unset=True)})
    _check_rules(kind, fields, clock)
    after = fields.model_dump()
    changes = audit.diff(before, after)
    if not changes:
        return record
    for field, value in after.items():
        setattr(record, field, value)
    session.flush()
    audit.record(
        session,
        action=f"{kind.entity_type}.updated",
        entity_type=kind.entity_type,
        entity_id=record.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes=changes,
        context={"applicant_id": applicant.id},
    )
    session.commit()
    session.refresh(record)
    return record


def remove_record(
    session: Session,
    kind: RecordKind,
    applicant: Applicant,
    record_id: uuid.UUID,
    *,
    actor: User,
    clock: Clock,
    ip_address: str | None,
) -> None:
    """Soft delete: financial data is never hard-deleted (DATABASE.md §1)."""
    record = _get_active_record(session, kind, applicant, record_id)
    record.is_active = False
    session.flush()
    audit.record(
        session,
        action=f"{kind.entity_type}.removed",
        entity_type=kind.entity_type,
        entity_id=record.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=ip_address,
        changes={"is_active": {"from": True, "to": False}},
        context={"applicant_id": applicant.id},
    )
    session.commit()


# --- Read model used by the applications module ------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    """Facts the application state machine needs to decide whether a profile is complete."""

    is_adult: bool
    employment_records: int
    income_records: int
    expense_records: int
    records_in_other_currencies: int


def profile_summary(
    session: Session, applicant: Applicant, *, today: date, currency: Currency
) -> ProfileSummary:
    def count(model: type[AnyRecord], *criteria: Any) -> int:
        query = select(func.count()).where(
            model.applicant_id == applicant.id, model.is_active.is_(True), *criteria
        )
        return session.scalar(query) or 0

    other_currency = (
        count(IncomeRecord, IncomeRecord.currency != currency)
        + count(ExpenseRecord, ExpenseRecord.currency != currency)
        + count(CreditCommitment, CreditCommitment.currency != currency)
    )
    return ProfileSummary(
        is_adult=age_on(applicant.date_of_birth, today) >= MINIMUM_AGE,
        employment_records=count(EmploymentRecord),
        income_records=count(IncomeRecord),
        expense_records=count(ExpenseRecord),
        records_in_other_currencies=other_currency,
    )
