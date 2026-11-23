"""Loan application use cases.

Every status change goes through ``domain.state_machine.transition`` and is persisted with a
status-history row and an audit event in the same transaction (ARCHITECTURE.md §5.2 #5).

Not wired yet, by design:

* ``QUEUE_FOR_REVIEW`` (SUBMITTED → PENDING_REVIEW) needs a persisted affordability
  assessment, which arrives in Phase 4. Submitted applications wait in SUBMITTED until then.
* ``RESUME_REVIEW`` needs re-run assessments (Phase 4).
* Recommendations, send-back and decisions arrive in Phase 5.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.ids import uuid7
from app.core.pagination import PageParams
from app.modules.applicants import service as applicants
from app.modules.applicants.models import Applicant
from app.modules.applications.domain.enums import (
    DocumentStatus,
    InformationRequestStatus,
    NoteType,
)
from app.modules.applications.domain.state_machine import (
    OPEN,
    PROFILE_LOCKED,
    Action,
    Actor,
    ActorNotPermittedError,
    ApplicationStatus,
    GuardViolationError,
    Transition,
    TransitionFacts,
    TransitionNotAllowedError,
    available_actions,
    transition,
)
from app.modules.applications.models import (
    REFERENCE_SEQUENCE,
    ApplicationStatusHistory,
    DocumentReference,
    InformationRequest,
    LoanApplication,
    ReviewNote,
)
from app.modules.applications.schemas import (
    ApplicationCreate,
    ApplicationUpdate,
    DocumentCreate,
)
from app.modules.audit import service as audit
from app.modules.identity.models import Role, User
from app.modules.identity.permissions import Permission, has_permission
from app.modules.products import service as products
from app.modules.products.models import LoanProduct

S = ApplicationStatus
ASSIGNABLE = frozenset({S.PENDING_REVIEW, S.UNDER_REVIEW, S.ADDITIONAL_INFO_REQUESTED})
SNAPSHOT_FIELDS = (
    "loan_product_id",
    "requested_amount",
    "currency",
    "requested_term_months",
    "purpose",
    "purpose_description",
)


class InvalidTransitionError(ConflictError):
    code = "INVALID_TRANSITION"
    title = "Invalid status transition"


class VersionConflictError(ConflictError):
    code = "VERSION_CONFLICT"
    title = "The application has changed since you last read it"


@dataclass(frozen=True, slots=True)
class Context:
    """Who is acting, when, and from where — passed to every use case."""

    user: User
    clock: Clock
    ip_address: str | None

    def now(self) -> datetime:
        return self.clock.now()


@dataclass(frozen=True, slots=True)
class Loaded:
    application: LoanApplication
    owner_user_id: uuid.UUID | None

    def actor_for(self, user: User) -> Actor:
        if user.role is Role.APPLICANT and user.id == self.owner_user_id:
            return Actor.owner(user.id)
        return Actor.staff(user.id, user.role)


# --- Loading with object-level access control --------------------------------------------


def _can_read_any(user: User) -> bool:
    return has_permission(user.role, Permission.APPLICATION_READ_ANY)


def _scoped[*Ts](query: Select[*Ts], user: User) -> Select[*Ts]:
    """Applicants only ever see applications attached to their own profile."""
    query = query.join(Applicant, Applicant.id == LoanApplication.applicant_id)
    if not _can_read_any(user):
        query = query.where(Applicant.user_id == user.id)
    return query


def load(
    session: Session, application_id: uuid.UUID, user: User, *, for_update: bool = False
) -> Loaded:
    query = _scoped(select(LoanApplication, Applicant.user_id), user).where(
        LoanApplication.id == application_id
    )
    if for_update:
        query = query.with_for_update(of=LoanApplication)
    row = session.execute(query).first()
    if row is None:
        # Same answer whether it does not exist or belongs to someone else (no enumeration).
        raise NotFoundError("Application not found")
    return Loaded(application=row[0], owner_user_id=row[1])


def _load_owned(
    session: Session, application_id: uuid.UUID, ctx: Context, expected_version: int | None
) -> Loaded:
    loaded = load(session, application_id, ctx.user, for_update=True)
    if ctx.user.role is not Role.APPLICANT or ctx.user.id != loaded.owner_user_id:
        # Staff can read applications but never act as the applicant.
        raise PermissionDeniedError("Only the applicant can perform this action")
    _check_version(loaded.application, expected_version)
    return loaded


def _check_version(application: LoanApplication, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != application.version_id:
        raise VersionConflictError(
            f"Expected version {expected_version} but the application is at version "
            f"{application.version_id}",
            extra={"current_version": application.version_id},
        )


def actions_for(loaded: Loaded, user: User) -> list[Action]:
    """Actions this user may attempt from the current status (guards run on attempt)."""
    return available_actions(loaded.application.status, loaded.actor_for(user))


# --- Transitions ---------------------------------------------------------------------------


def _apply(
    application: LoanApplication,
    action: Action,
    actor: Actor,
    facts: TransitionFacts | None = None,
) -> Transition:
    try:
        return transition(application.status, action, actor, facts)
    except TransitionNotAllowedError as exc:
        raise InvalidTransitionError(
            exc.message, extra={"action": action.value, "status": application.status.value}
        ) from exc
    except ActorNotPermittedError as exc:
        raise PermissionDeniedError(exc.message, extra={"action": action.value}) from exc
    except GuardViolationError as exc:
        raise BusinessRuleError(
            exc.message, extra={"action": action.value, "violations": exc.violations}
        ) from exc


def _record_transition(
    session: Session,
    application: LoanApplication,
    change: Transition,
    ctx: Context,
    *,
    reason: str | None = None,
    by_system: bool = False,
) -> None:
    """Persist a validated transition: status + history row + audit event, one transaction.

    ``by_system`` records an automatic step (e.g. queueing after assessment) with no actor.
    """
    actor = None if by_system else ctx.user
    application.status = change.target
    session.add(
        ApplicationStatusHistory(
            id=uuid7(),
            application_id=application.id,
            from_status=change.source,
            to_status=change.target,
            actor_user_id=actor.id if actor else None,
            reason=reason,
            created_at=ctx.now(),
        )
    )
    session.flush()
    audit.record(
        session,
        action="application.status_changed",
        entity_type="loan_application",
        entity_id=application.id,
        actor=actor,
        occurred_at=ctx.now(),
        ip_address=None if by_system else ctx.ip_address,
        changes={"status": {"from": change.source.value, "to": change.target.value}},
        context={"action": change.action.value, "reason": reason},
    )


# --- Drafts ----------------------------------------------------------------------------------


def _limit_problems(product: LoanProduct, amount: Any, term: int) -> list[str]:
    problems = []
    if not product.min_amount <= amount <= product.max_amount:
        problems.append(
            f"requested_amount must be between {product.min_amount} and {product.max_amount} "
            f"{product.currency.value}"
        )
    if not product.min_term_months <= term <= product.max_term_months:
        problems.append(
            f"requested_term_months must be between {product.min_term_months} and "
            f"{product.max_term_months}"
        )
    return problems


def _draft_snapshot(application: LoanApplication) -> dict[str, Any]:
    return {field: getattr(application, field) for field in SNAPSHOT_FIELDS}


def _next_reference(session: Session, ctx: Context) -> str:
    number = session.scalar(select(REFERENCE_SEQUENCE.next_value()))
    return f"CRD-{business_date(ctx.clock).year}-{number:06d}"


def open_application_reference(session: Session, applicant_id: uuid.UUID) -> str | None:
    return session.scalar(
        select(LoanApplication.reference).where(
            LoanApplication.applicant_id == applicant_id, LoanApplication.status.in_(OPEN)
        )
    )


def create_application(session: Session, data: ApplicationCreate, ctx: Context) -> LoanApplication:
    applicant = applicants.get_own_applicant(session, ctx.user)
    product = products.get_product(session, data.loan_product_id)
    problems = _limit_problems(product, data.requested_amount, data.requested_term_months)
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})
    existing = open_application_reference(session, applicant.id)
    if existing:
        raise ConflictError(
            f"You already have an open application ({existing}). Withdraw it or wait for a "
            "decision before starting another.",
            extra={"open_application": existing},
        )

    application = LoanApplication(
        id=uuid7(),
        reference=_next_reference(session, ctx),
        applicant_id=applicant.id,
        currency=product.currency,
        status=S.DRAFT,
        **data.model_dump(),
    )
    session.add(application)
    try:
        session.flush()
    except IntegrityError as exc:  # concurrent create; the partial unique index caught it
        raise ConflictError("You already have an open application") from exc
    session.add(
        ApplicationStatusHistory(
            id=uuid7(),
            application_id=application.id,
            from_status=None,
            to_status=S.DRAFT,
            actor_user_id=ctx.user.id,
            created_at=ctx.now(),
        )
    )
    audit.record(
        session,
        action="application.created",
        entity_type="loan_application",
        entity_id=application.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        changes=audit.diff({}, {**_draft_snapshot(application), "status": S.DRAFT.value}),
        context={"reference": application.reference},
    )
    session.commit()
    return application


def update_draft(
    session: Session,
    application_id: uuid.UUID,
    data: ApplicationUpdate,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanApplication:
    application = _load_owned(session, application_id, ctx, expected_version).application
    if application.status is not S.DRAFT:
        raise InvalidTransitionError(
            f"Only draft applications can be edited; this one is {application.status.value}"
        )
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if "purpose_description" in data.model_fields_set and data.purpose_description is None:
        changes["purpose_description"] = None
    before = _draft_snapshot(application)

    if "loan_product_id" in changes:
        # Switching product: only to one that is currently offered.
        product = products.get_product(session, changes["loan_product_id"])
    else:
        # Keeping the product: editable even if it was withdrawn (submit will block it).
        product = products.get_product(session, application.loan_product_id, include_inactive=True)
    amount = changes.get("requested_amount", application.requested_amount)
    term = changes.get("requested_term_months", application.requested_term_months)
    problems = _limit_problems(product, amount, term)
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})

    for field, value in changes.items():
        setattr(application, field, value)
    application.currency = product.currency
    diff = audit.diff(before, _draft_snapshot(application))
    if not diff:
        return application
    session.flush()
    audit.record(
        session,
        action="application.updated",
        entity_type="loan_application",
        entity_id=application.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        changes=diff,
    )
    session.commit()
    session.refresh(application)
    return application


# --- Applicant actions -----------------------------------------------------------------------


def submit(
    session: Session,
    application_id: uuid.UUID,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanApplication:
    loaded = _load_owned(session, application_id, ctx, expected_version)
    application = loaded.application
    product = session.get(LoanProduct, application.loan_product_id)
    # Lock the profile row too: profile edits take the same lock, so the facts checked
    # here cannot change between this check and the commit.
    applicant = session.get(Applicant, application.applicant_id, with_for_update=True)
    if product is None or applicant is None:  # FKs make this unreachable
        raise NotFoundError("Application not found")

    profile = applicants.profile_summary(
        session, applicant, today=business_date(ctx.clock), currency=application.currency
    )
    facts = TransitionFacts(
        product_active=product.is_active,
        amount_within_limits=product.min_amount
        <= application.requested_amount
        <= product.max_amount,
        term_within_limits=product.min_term_months
        <= application.requested_term_months
        <= product.max_term_months,
        currency_matches_product=application.currency == product.currency,
        applicant_is_adult=profile.is_adult,
        employment_records=profile.employment_records,
        income_records=profile.income_records,
        expense_records=profile.expense_records,
        records_in_other_currencies=profile.records_in_other_currencies,
    )
    change = _apply(application, Action.SUBMIT, loaded.actor_for(ctx.user), facts)
    now = ctx.now()
    application.submitted_at = now
    application.product_terms_snapshot = products.terms_snapshot(product, captured_at=now)
    _record_transition(session, application, change, ctx)
    session.commit()
    session.refresh(application)
    return application


def withdraw(
    session: Session,
    application_id: uuid.UUID,
    ctx: Context,
    *,
    reason: str | None,
    expected_version: int | None = None,
) -> LoanApplication:
    loaded = _load_owned(session, application_id, ctx, expected_version)
    application = loaded.application
    change = _apply(application, Action.WITHDRAW, loaded.actor_for(ctx.user))
    open_requests = session.scalars(
        select(InformationRequest).where(
            InformationRequest.application_id == application.id,
            InformationRequest.status == InformationRequestStatus.OPEN,
        )
    ).all()
    for request in open_requests:
        request.status = InformationRequestStatus.CANCELLED
    _record_transition(session, application, change, ctx, reason=reason or None)
    session.commit()
    session.refresh(application)
    return application


# --- Staff actions -------------------------------------------------------------------------


def _load_for_staff(
    session: Session, application_id: uuid.UUID, ctx: Context, expected_version: int | None
) -> Loaded:
    loaded = load(session, application_id, ctx.user, for_update=True)
    _check_version(loaded.application, expected_version)
    return loaded


def _assign(session: Session, application: LoanApplication, officer: User, ctx: Context) -> None:
    previous = application.assigned_officer_id
    application.assigned_officer_id = officer.id
    session.flush()
    audit.record(
        session,
        action="application.assigned",
        entity_type="loan_application",
        entity_id=application.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        changes={
            "assigned_officer_id": {
                "from": str(previous) if previous else None,
                "to": str(officer.id),
            }
        },
    )


def assign(
    session: Session,
    application_id: uuid.UUID,
    officer_id: uuid.UUID,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanApplication:
    application = _load_for_staff(session, application_id, ctx, expected_version).application
    if application.status not in ASSIGNABLE:
        raise InvalidTransitionError(
            f"Applications that are {application.status.value} cannot be assigned"
        )
    officer = session.get(User, officer_id)
    if officer is None or not officer.is_active or officer.role is not Role.LOAN_OFFICER:
        raise BusinessRuleError("Applications can only be assigned to an active loan officer")
    if application.assigned_officer_id == officer.id:
        return application
    _assign(session, application, officer, ctx)
    session.commit()
    session.refresh(application)
    return application


def start_review(
    session: Session,
    application_id: uuid.UUID,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanApplication:
    loaded = _load_for_staff(session, application_id, ctx, expected_version)
    application = loaded.application
    actor = loaded.actor_for(ctx.user)
    # An unassigned application is claimed by the officer who starts reviewing it.
    claim = (
        application.assigned_officer_id is None
        and application.status is S.PENDING_REVIEW
        and ctx.user.role is Role.LOAN_OFFICER
    )
    assigned_to = ctx.user.id if claim else application.assigned_officer_id
    change = _apply(
        application, Action.START_REVIEW, actor, TransitionFacts(assigned_officer_id=assigned_to)
    )
    if claim:
        _assign(session, application, ctx.user, ctx)
    _record_transition(session, application, change, ctx)
    session.commit()
    session.refresh(application)
    return application


def request_information(
    session: Session,
    application_id: uuid.UUID,
    message: str,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> InformationRequest:
    loaded = _load_for_staff(session, application_id, ctx, expected_version)
    application = loaded.application
    change = _apply(
        application,
        Action.REQUEST_INFORMATION,
        loaded.actor_for(ctx.user),
        TransitionFacts(message=message),
    )
    request = InformationRequest(
        id=uuid7(),
        application_id=application.id,
        requested_by=ctx.user.id,
        message=message,
        status=InformationRequestStatus.OPEN,
        created_at=ctx.now(),
    )
    session.add(request)
    _record_transition(session, application, change, ctx, reason=message)
    audit.record(
        session,
        action="information_request.created",
        entity_type="information_request",
        entity_id=request.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        context={"application_id": application.id},
    )
    session.commit()
    return request


def respond_to_information_request(
    session: Session,
    application_id: uuid.UUID,
    request_id: uuid.UUID,
    response_text: str,
    ctx: Context,
) -> InformationRequest:
    application = _load_owned(session, application_id, ctx, None).application
    request = session.scalar(
        select(InformationRequest)
        .where(
            InformationRequest.id == request_id,
            InformationRequest.application_id == application.id,
        )
        .with_for_update()
    )
    if request is None:
        raise NotFoundError("Information request not found")
    if request.status is not InformationRequestStatus.OPEN:
        raise ConflictError(f"This information request is already {request.status.value}")
    if application.status is not S.ADDITIONAL_INFO_REQUESTED:
        raise InvalidTransitionError("This application is not waiting for information")
    request.status = InformationRequestStatus.RESPONDED
    request.response_text = response_text
    request.responded_at = ctx.now()
    session.flush()
    audit.record(
        session,
        action="information_request.responded",
        entity_type="information_request",
        entity_id=request.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        changes={"status": {"from": "OPEN", "to": "RESPONDED"}},
        context={"application_id": application.id},
    )
    session.commit()
    return request


# --- Notes, documents, history, information requests --------------------------------------


def _note_type_for(role: Role) -> NoteType:
    return NoteType.ANALYST_ASSESSMENT if role is Role.CREDIT_ANALYST else NoteType.OFFICER_NOTE


def add_note(session: Session, application_id: uuid.UUID, body: str, ctx: Context) -> ReviewNote:
    application = load(session, application_id, ctx.user).application
    if application.status is S.DRAFT:
        raise InvalidTransitionError("Draft applications have not been submitted for review")
    note = ReviewNote(
        id=uuid7(),
        application_id=application.id,
        author_id=ctx.user.id,
        note_type=_note_type_for(ctx.user.role),
        body=body,
        created_at=ctx.now(),
    )
    session.add(note)
    session.flush()
    audit.record(
        session,
        action="review_note.created",
        entity_type="review_note",
        entity_id=note.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        context={"application_id": application.id, "note_type": note.note_type.value},
    )
    session.commit()
    return note


def list_notes(session: Session, application_id: uuid.UUID, user: User) -> Sequence[ReviewNote]:
    load(session, application_id, user)
    return session.scalars(
        select(ReviewNote)
        .where(ReviewNote.application_id == application_id)
        .order_by(ReviewNote.created_at, ReviewNote.id)
    ).all()


def list_history(
    session: Session, application_id: uuid.UUID, user: User
) -> Sequence[ApplicationStatusHistory]:
    load(session, application_id, user)
    return session.scalars(
        select(ApplicationStatusHistory)
        .where(ApplicationStatusHistory.application_id == application_id)
        .order_by(ApplicationStatusHistory.created_at, ApplicationStatusHistory.id)
    ).all()


def list_information_requests(
    session: Session, application_id: uuid.UUID, user: User
) -> Sequence[InformationRequest]:
    load(session, application_id, user)
    return session.scalars(
        select(InformationRequest)
        .where(InformationRequest.application_id == application_id)
        .order_by(InformationRequest.created_at, InformationRequest.id)
    ).all()


def add_document(
    session: Session, application_id: uuid.UUID, data: DocumentCreate, ctx: Context
) -> DocumentReference:
    application = _load_owned(session, application_id, ctx, None).application
    if application.status not in OPEN:
        raise InvalidTransitionError(
            f"Documents cannot be added to an application that is {application.status.value}"
        )
    document_id = uuid7()
    document = DocumentReference(
        id=document_id,
        application_id=application.id,
        # Simulated storage: a server-generated key, never a client-supplied path.
        storage_key=f"simulated/{application.id}/{document_id}",
        uploaded_by=ctx.user.id,
        uploaded_at=ctx.now(),
        status=DocumentStatus.RECEIVED,
        **data.model_dump(),
    )
    session.add(document)
    session.flush()
    audit.record(
        session,
        action="document.added",
        entity_type="document_reference",
        entity_id=document.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        context={
            "application_id": application.id,
            "document_type": document.document_type.value,
            "size_bytes": document.size_bytes,
        },
    )
    session.commit()
    return document


def list_documents(
    session: Session, application_id: uuid.UUID, user: User
) -> Sequence[DocumentReference]:
    load(session, application_id, user)
    return session.scalars(
        select(DocumentReference)
        .where(DocumentReference.application_id == application_id)
        .order_by(DocumentReference.uploaded_at, DocumentReference.id)
    ).all()


def review_document(
    session: Session,
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    new_status: DocumentStatus,
    ctx: Context,
) -> DocumentReference:
    load(session, application_id, ctx.user)
    document = session.scalar(
        select(DocumentReference)
        .where(
            DocumentReference.id == document_id,
            DocumentReference.application_id == application_id,
        )
        .with_for_update()
    )
    if document is None:
        raise NotFoundError("Document not found")
    if document.status is not DocumentStatus.RECEIVED:
        raise ConflictError(f"This document has already been {document.status.value.lower()}")
    document.status = new_status
    session.flush()
    audit.record(
        session,
        action="document.reviewed",
        entity_type="document_reference",
        entity_id=document.id,
        actor=ctx.user,
        occurred_at=ctx.now(),
        ip_address=ctx.ip_address,
        changes={"status": {"from": DocumentStatus.RECEIVED.value, "to": new_status.value}},
        context={"application_id": application_id},
    )
    session.commit()
    return document


# --- Queries -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApplicationFilters:
    statuses: tuple[ApplicationStatus, ...] = ()
    loan_product_id: uuid.UUID | None = None
    assigned_officer_id: uuid.UUID | None = None
    applicant_id: uuid.UUID | None = None
    submitted_from: datetime | None = None
    submitted_to: datetime | None = None


def list_applications(
    session: Session, user: User, params: PageParams, filters: ApplicationFilters
) -> tuple[list[LoanApplication], int]:
    query = _scoped(select(LoanApplication), user)
    if filters.statuses:
        query = query.where(LoanApplication.status.in_(filters.statuses))
    if filters.loan_product_id:
        query = query.where(LoanApplication.loan_product_id == filters.loan_product_id)
    if filters.assigned_officer_id:
        query = query.where(LoanApplication.assigned_officer_id == filters.assigned_officer_id)
    if filters.applicant_id:
        query = query.where(LoanApplication.applicant_id == filters.applicant_id)
    if filters.submitted_from:
        query = query.where(LoanApplication.submitted_at >= filters.submitted_from)
    if filters.submitted_to:
        query = query.where(LoanApplication.submitted_at < filters.submitted_to)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(
            LoanApplication.submitted_at.desc().nulls_last(),
            LoanApplication.created_at.desc(),
            LoanApplication.id.desc(),
        )
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    return list(rows), total


def profile_lock_reason(session: Session, applicant_id: uuid.UUID) -> str | None:
    """Why the applicant's profile is read-only right now, or None if it is editable."""
    reference = session.scalar(
        select(LoanApplication.reference).where(
            LoanApplication.applicant_id == applicant_id,
            LoanApplication.status.in_(PROFILE_LOCKED),
        )
    )
    if reference is None:
        return None
    return (
        f"Your profile is locked while application {reference} is being reviewed. "
        "You can update it if the lender asks for more information."
    )
