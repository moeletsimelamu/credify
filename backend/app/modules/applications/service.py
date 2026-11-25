"""Loan application use cases.

Every status change goes through ``domain.state_machine.transition`` and is persisted with a
status-history row and an audit event in the same transaction (ARCHITECTURE.md §5.2 #5).

Submitting records affordability assessment v1 and queues the application for review in the
same transaction (SUBMITTED → PENDING_REVIEW). Resuming review after an information request
records a fresh assessment the same way. Credit-risk scoring joins both steps in Phase 7.

A loan officer's recommendation and an analyst's or administrator's final decision are
transitions too (maker–checker, approved decision D2). Disbursing an approved application
opens the (simulated) loan account.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
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
from app.modules.affordability import service as affordability
from app.modules.affordability.models import AffordabilityAssessment, RunReason
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
    ensure_allowed,
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
from app.modules.credit_risk import service as credit_risk
from app.modules.credit_risk.models import (
    AssessmentStatus,
    CreditBureauReport,
    CreditRiskAssessment,
    RiskRunReason,
)
from app.modules.decisions import service as decisions
from app.modules.decisions.domain.reason_codes import DecisionOutcome, ReasonCode, Recommendation
from app.modules.decisions.models import LendingDecision, LoanRecommendation
from app.modules.decisions.schemas import DecisionCreate, RecommendationCreate
from app.modules.identity.models import Role, User
from app.modules.identity.permissions import Permission, has_permission
from app.modules.loans import service as loans
from app.modules.loans.models import LoanAccount
from app.modules.loans.schemas import RepaymentSchedulePreview
from app.modules.products import service as products
from app.modules.products.models import LoanProduct

S = ApplicationStatus
ASSIGNABLE = frozenset({S.PENDING_REVIEW, S.UNDER_REVIEW, S.ADDITIONAL_INFO_REQUESTED})
# Staff may re-run affordability while the application is with the lender and undecided.
# While a recommendation awaits a decision (RECOMMENDED_*), the evidence it cites must stay
# the latest, so re-runs are blocked; sending it back to review allows them again.
ASSESSABLE = ASSIGNABLE
RECOMMEND_ACTIONS = {
    Recommendation.APPROVE: Action.RECOMMEND_APPROVAL,
    Recommendation.REJECT: Action.RECOMMEND_REJECTION,
}
DECISION_ACTIONS = {
    DecisionOutcome.APPROVED: Action.APPROVE,
    DecisionOutcome.DECLINED: Action.DECLINE,
}
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
    model_dir: Path | None = None  # read-only model artefacts (credit-risk scoring)

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


@contextmanager
def _transition_errors(application: LoanApplication, action: Action) -> Iterator[None]:
    """Translate state-machine errors into problem responses."""
    try:
        yield
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


def _apply(
    application: LoanApplication,
    action: Action,
    actor: Actor,
    facts: TransitionFacts | None = None,
) -> Transition:
    with _transition_errors(application, action):
        return transition(application.status, action, actor, facts)


def _ensure(application: LoanApplication, action: Action, actor: Actor) -> None:
    """Status and actor checks only, before doing work the guard facts depend on."""
    with _transition_errors(application, action):
        ensure_allowed(application.status, action, actor)


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
    facts = _with_profile(
        TransitionFacts(
            product_active=product.is_active,
            amount_within_limits=product.min_amount
            <= application.requested_amount
            <= product.max_amount,
            term_within_limits=product.min_term_months
            <= application.requested_term_months
            <= product.max_term_months,
            currency_matches_product=application.currency == product.currency,
            applicant_is_adult=profile.is_adult,
        ),
        profile,
    )
    change = _apply(application, Action.SUBMIT, loaded.actor_for(ctx.user), facts)
    now = ctx.now()
    application.submitted_at = now
    application.product_terms_snapshot = products.terms_snapshot(product, captured_at=now)
    _record_transition(session, application, change, ctx)

    # Automatic steps: assess affordability, attempt risk scoring, then queue. Same
    # transaction, so the review queue never holds an unassessed application. A failed risk
    # score is recorded and does not stop the application reaching a human.
    assessment = _record_affordability(session, application, ctx, RunReason.SUBMISSION)
    risk = _record_risk(session, application, ctx, RiskRunReason.SUBMISSION)
    queued = _apply(
        application,
        Action.QUEUE_FOR_REVIEW,
        Actor.system(),
        TransitionFacts(has_affordability_assessment=True, risk_assessment_attempted=True),
    )
    _record_transition(
        session,
        application,
        queued,
        ctx,
        reason=f"{_assessed(assessment)}; {_risk_summary(risk)}",
        by_system=True,
    )
    session.commit()
    session.refresh(application)
    return application


def _with_profile(facts: TransitionFacts, profile: applicants.ProfileSummary) -> TransitionFacts:
    return replace(
        facts,
        employment_records=profile.employment_records,
        income_records=profile.income_records,
        expense_records=profile.expense_records,
        records_in_other_currencies=profile.records_in_other_currencies,
    )


def _record_affordability(
    session: Session,
    application: LoanApplication,
    ctx: Context,
    run_reason: RunReason,
    *,
    requested_by: User | None = None,
) -> AffordabilityAssessment:
    return affordability.record_assessment(
        session,
        application,
        run_reason=run_reason,
        requested_by=requested_by,
        now=ctx.now(),
        as_of=business_date(ctx.clock),
        ip_address=ctx.ip_address if requested_by else None,
    )


def _record_risk(
    session: Session,
    application: LoanApplication,
    ctx: Context,
    run_reason: RiskRunReason,
    *,
    requested_by: User | None = None,
) -> CreditRiskAssessment:
    return credit_risk.score(
        session,
        application,
        run_reason=run_reason,
        requested_by=requested_by,
        now=ctx.now(),
        today=business_date(ctx.clock),
        model_dir=ctx.model_dir,
        ip_address=ctx.ip_address if requested_by else None,
    )


def _risk_summary(risk: CreditRiskAssessment) -> str:
    if risk.status is AssessmentStatus.SCORED and risk.risk_band is not None:
        return f"credit-risk v{risk.version}: band {risk.risk_band.value} (advisory)"
    return f"credit-risk v{risk.version}: not scored ({risk.failure_reason})"


def _assessed(assessment: AffordabilityAssessment) -> str:
    return f"Affordability assessment v{assessment.version}: {assessment.outcome.value}"


def resume_review(
    session: Session,
    application_id: uuid.UUID,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanApplication:
    """ADDITIONAL_INFO_REQUESTED → PENDING_REVIEW, with a fresh assessment of the profile.

    The applicant could edit their profile while information was outstanding, so it is
    checked for completeness again and re-assessed before review resumes.
    """
    loaded = _load_owned(session, application_id, ctx, expected_version)
    application = loaded.application
    actor = loaded.actor_for(ctx.user)
    _ensure(application, Action.RESUME_REVIEW, actor)
    applicant = session.get(Applicant, application.applicant_id, with_for_update=True)
    if applicant is None:  # FK makes this unreachable
        raise NotFoundError("Application not found")
    profile = applicants.profile_summary(
        session, applicant, today=business_date(ctx.clock), currency=application.currency
    )
    open_requests = (
        session.scalar(
            select(func.count()).where(
                InformationRequest.application_id == application.id,
                InformationRequest.status == InformationRequestStatus.OPEN,
            )
        )
        or 0
    )
    facts = _with_profile(TransitionFacts(open_information_requests=open_requests), profile)
    # The refreshed assessment is the one precondition this use case satisfies itself: it is
    # recorded just below, in the same transaction, before the status changes. Checking the
    # rest first means missing answers are reported without assessing a stale profile.
    change = _apply(
        application, Action.RESUME_REVIEW, actor, replace(facts, assessments_refreshed=True)
    )
    assessment = _record_affordability(session, application, ctx, RunReason.RESUBMISSION)
    risk = _record_risk(session, application, ctx, RiskRunReason.RESUBMISSION)
    _record_transition(
        session, application, change, ctx, reason=f"{_assessed(assessment)}; {_risk_summary(risk)}"
    )
    session.commit()
    session.refresh(application)
    return application


# --- Affordability assessments -------------------------------------------------------------


def run_affordability(
    session: Session, application_id: uuid.UUID, ctx: Context
) -> AffordabilityAssessment:
    """A staff re-run on the applicant's current profile. Creates a new version."""
    application = load(session, application_id, ctx.user, for_update=True).application
    if application.status not in ASSESSABLE:
        raise InvalidTransitionError(
            f"Affordability cannot be re-run while the application is {application.status.value}",
            extra={"status": application.status.value},
        )
    assessment = _record_affordability(
        session, application, ctx, RunReason.STAFF_REQUEST, requested_by=ctx.user
    )
    session.commit()
    return assessment


def run_risk(session: Session, application_id: uuid.UUID, ctx: Context) -> CreditRiskAssessment:
    """A credit analyst's re-run on the current profile. Creates a new version."""
    application = load(session, application_id, ctx.user, for_update=True).application
    if application.status not in ASSESSABLE:
        raise InvalidTransitionError(
            f"Credit risk cannot be re-run while the application is {application.status.value}",
            extra={"status": application.status.value},
        )
    assessment = _record_risk(
        session, application, ctx, RiskRunReason.STAFF_REQUEST, requested_by=ctx.user
    )
    session.commit()
    return assessment


def list_risk(
    session: Session, application_id: uuid.UUID, user: User
) -> Sequence[CreditRiskAssessment]:
    load(session, application_id, user)
    return credit_risk.list_assessments(session, application_id)


def get_risk(
    session: Session, application_id: uuid.UUID, assessment_id: uuid.UUID, user: User
) -> CreditRiskAssessment:
    load(session, application_id, user)
    assessment = credit_risk.get_assessment(session, application_id, assessment_id)
    if assessment is None:
        raise NotFoundError("Credit-risk assessment not found")
    return assessment


def get_bureau_report(
    session: Session, application_id: uuid.UUID, user: User
) -> CreditBureauReport:
    load(session, application_id, user)
    report = credit_risk.bureau_report(session, application_id)
    if report is None:
        raise NotFoundError("No simulated bureau report has been pulled for this application")
    return report


def list_affordability(
    session: Session, application_id: uuid.UUID, user: User
) -> Sequence[AffordabilityAssessment]:
    load(session, application_id, user)
    return affordability.list_assessments(session, application_id)


def get_affordability(
    session: Session, application_id: uuid.UUID, assessment_id: uuid.UUID, user: User
) -> AffordabilityAssessment:
    load(session, application_id, user)
    assessment = affordability.get_assessment(session, application_id, assessment_id)
    if assessment is None:
        raise NotFoundError("Affordability assessment not found")
    return assessment


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


# --- Recommendation, decision and disbursement --------------------------------------------


def _evidence_versions(
    session: Session, application: LoanApplication, cited_id: uuid.UUID
) -> tuple[AffordabilityAssessment | None, int | None]:
    """(the cited assessment, the latest version) for an application."""
    cited = affordability.get_assessment(session, application.id, cited_id)
    return cited, affordability.latest_version(session, application.id)


@dataclass(frozen=True, slots=True)
class RiskEvidence:
    """The scored risk assessment cited (if any) and the latest scored one (if any)."""

    cited: CreditRiskAssessment | None
    latest: CreditRiskAssessment | None

    @property
    def reviewed_version(self) -> int | None:
        return self.cited.version if self.cited else None

    @property
    def latest_version(self) -> int | None:
        return self.latest.version if self.latest else None


def _risk_evidence(
    session: Session, application: LoanApplication, cited_id: uuid.UUID | None
) -> RiskEvidence:
    cited = None
    if cited_id is not None:
        found = credit_risk.get_assessment(session, application.id, cited_id)
        cited = found if found is not None and found.status is AssessmentStatus.SCORED else None
    return RiskEvidence(cited, credit_risk.latest_scored(session, application.id))


def recommend(
    session: Session,
    application_id: uuid.UUID,
    data: RecommendationCreate,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanRecommendation:
    loaded = _load_for_staff(session, application_id, ctx, expected_version)
    application = loaded.application
    cited, latest = _evidence_versions(session, application, data.affordability_assessment_id)
    risk = _risk_evidence(session, application, data.credit_risk_assessment_id)
    change = _apply(
        application,
        RECOMMEND_ACTIONS[data.recommendation],
        loaded.actor_for(ctx.user),
        TransitionFacts(
            rationale=data.rationale,
            reviewed_affordability_version=cited.version if cited else None,
            latest_affordability_version=latest,
            reviewed_risk_version=risk.reviewed_version,
            latest_risk_version=risk.latest_version,
        ),
    )
    assert cited is not None  # noqa: S101 — the guard rejects a missing or stale citation
    recommendation = decisions.record_recommendation(
        session,
        application_id=application.id,
        recommendation=data.recommendation,
        rationale=data.rationale,
        affordability_assessment_id=cited.id,
        credit_risk_assessment_id=risk.cited.id if risk.cited else None,
        actor=ctx.user,
        now=ctx.now(),
        ip_address=ctx.ip_address,
    )
    _record_transition(
        session,
        application,
        change,
        ctx,
        reason=f"Recommended {data.recommendation.value.lower()} "
        f"(affordability v{cited.version}: {cited.outcome.value})",
    )
    session.commit()
    return recommendation


def send_back(
    session: Session,
    application_id: uuid.UUID,
    reason: str,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LoanApplication:
    """RECOMMENDED_* → UNDER_REVIEW: the decider wants more work before deciding."""
    loaded = _load_for_staff(session, application_id, ctx, expected_version)
    application = loaded.application
    change = _apply(
        application, Action.SEND_BACK, loaded.actor_for(ctx.user), TransitionFacts(message=reason)
    )
    _record_transition(session, application, change, ctx, reason=reason)
    session.commit()
    session.refresh(application)
    return application


def _offer_problems(
    application: LoanApplication,
    assessment: AffordabilityAssessment | None,
    amount: Decimal,
    term: int,
    reason_codes: tuple[str, ...],
    today: date,
) -> list[str]:
    """Rules for the terms an approval grants (a counter-offer may differ from the request)."""
    terms = application.product_terms_snapshot or {}
    problems = []
    if amount > application.requested_amount:
        problems.append("the approved amount cannot exceed the requested amount")
    if amount < Decimal(str(terms["min_amount"])):
        problems.append(f"the approved amount must be at least {terms['min_amount']}")
    if not int(terms["min_term_months"]) <= term <= int(terms["max_term_months"]):
        problems.append(
            f"the approved term must be between {terms['min_term_months']} and "
            f"{terms['max_term_months']} months"
        )
    requested = (application.requested_amount, application.requested_term_months)
    if (amount, term) == requested or problems:
        return problems
    if ReasonCode.COUNTER_OFFER.value not in reason_codes:
        problems.append("a counter-offer must cite the COUNTER_OFFER reason code")
    try:
        schedule = loans.schedule_from_snapshot(terms, amount, term, today)
    except BusinessRuleError as exc:
        return [*problems, exc.detail]
    if assessment is not None and schedule.first_total_due > assessment.proposed_instalment:
        problems.append(
            f"a counter-offer may not cost more per month ({schedule.first_total_due}) than the "
            f"instalment assessed for affordability ({assessment.proposed_instalment})"
        )
    return problems


def decide(
    session: Session,
    application_id: uuid.UUID,
    data: DecisionCreate,
    ctx: Context,
    *,
    expected_version: int | None = None,
) -> LendingDecision:
    """The final, human decision. The recommendation (and, from Phase 7, the model) advise."""
    loaded = _load_for_staff(session, application_id, ctx, expected_version)
    application = loaded.application
    action = DECISION_ACTIONS[data.decision]
    recommendation = decisions.latest_recommendation(session, application.id)
    cited, latest = _evidence_versions(session, application, data.affordability_assessment_id)
    risk = _risk_evidence(session, application, data.credit_risk_assessment_id)
    codes = tuple(data.reason_codes)

    approved_amount: Decimal | None = None
    approved_term: int | None = None
    offer_problems: list[str] = []
    if data.decision is DecisionOutcome.APPROVED:
        approved_amount = data.approved_amount or application.requested_amount
        approved_term = data.approved_term_months or application.requested_term_months
        if application.product_terms_snapshot:
            offer_problems = _offer_problems(
                application, cited, approved_amount, approved_term, codes, business_date(ctx.clock)
            )
    elif data.approved_amount is not None or data.approved_term_months is not None:
        offer_problems = ["only an approval can set an approved amount or term"]

    facts = TransitionFacts(
        recommender_id=recommendation.recommended_by if recommendation else None,
        reason_codes=codes,
        explanation=data.explanation,
        model_output_acknowledged=data.model_output_acknowledged,
        reviewed_affordability_version=cited.version if cited else None,
        latest_affordability_version=latest,
        reviewed_risk_version=risk.reviewed_version,
        latest_risk_version=risk.latest_version,
    )
    try:
        change = _apply(application, action, loaded.actor_for(ctx.user), facts)
    except BusinessRuleError as exc:
        if offer_problems:  # report every problem at once
            violations = [*exc.extra.get("violations", []), *offer_problems]
            raise BusinessRuleError(
                "; ".join(violations), extra={**exc.extra, "violations": violations}
            ) from exc
        raise
    if offer_problems:
        raise BusinessRuleError(
            "; ".join(offer_problems),
            extra={"action": action.value, "violations": offer_problems},
        )
    assert recommendation is not None  # noqa: S101 — guarded above
    assert cited is not None  # noqa: S101 — guarded above

    decision = decisions.record_decision(
        session,
        application_id=application.id,
        decision=data.decision,
        recommendation=recommendation,
        affordability_assessment_id=cited.id,
        credit_risk_assessment=risk.cited,
        reason_codes=codes,
        explanation=data.explanation,
        approved_amount=approved_amount,
        approved_term_months=approved_term,
        actor=ctx.user,
        now=ctx.now(),
        ip_address=ctx.ip_address,
    )
    application.decided_at = ctx.now()
    _record_transition(
        session, application, change, ctx, reason=f"{data.decision.value}: {', '.join(codes)}"
    )
    session.commit()
    return decision


def disburse(session: Session, application_id: uuid.UUID, ctx: Context) -> tuple[LoanAccount, bool]:
    """Open the loan account for an approved application. Idempotent.

    Returns ``(account, created)``: a repeated call returns the existing account.
    """
    application = load(session, application_id, ctx.user, for_update=True).application
    existing = loans.account_for_application(session, application.id)
    if existing is not None:
        return existing, False
    decision = decisions.get_decision(session, application.id)
    if (
        application.status is not S.APPROVED
        or decision is None
        or decision.approved_amount is None
        or decision.approved_term_months is None
        or application.product_terms_snapshot is None
    ):
        raise InvalidTransitionError(
            f"Only approved applications can be disbursed; this one is {application.status.value}",
            extra={"status": application.status.value},
        )
    account = loans.open_account(
        session,
        loans.ApprovedLoan(
            application_id=application.id,
            applicant_id=application.applicant_id,
            decision_id=decision.id,
            reference=application.reference,
            currency=application.currency,
            amount=decision.approved_amount,
            term_months=decision.approved_term_months,
            terms_snapshot=application.product_terms_snapshot,
        ),
        disbursed_on=business_date(ctx.clock),
        actor=loans.Actor(ctx.user, ctx.now(), ctx.ip_address),
    )
    session.commit()
    return account, True


def list_recommendations(
    session: Session, application_id: uuid.UUID, user: User
) -> Sequence[LoanRecommendation]:
    load(session, application_id, user)
    return decisions.list_recommendations(session, application_id)


def get_decision(session: Session, application_id: uuid.UUID, user: User) -> LendingDecision:
    load(session, application_id, user)
    decision = decisions.get_decision(session, application_id)
    if decision is None:
        raise NotFoundError("This application has not been decided")
    return decision


def get_loan_account(session: Session, application_id: uuid.UUID, user: User) -> LoanAccount:
    load(session, application_id, user)
    account = loans.account_for_application(session, application_id)
    if account is None:
        raise NotFoundError("No loan account has been opened for this application")
    return account


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


def repayment_schedule(
    session: Session, application_id: uuid.UUID, user: User, *, disbursement_date: date
) -> RepaymentSchedulePreview:
    """The requested loan's schedule: under the frozen terms once submitted, else the
    product's current terms. Illustrative only; no loan exists yet."""
    application = load(session, application_id, user).application
    if application.product_terms_snapshot:
        return loans.preview_from_snapshot(
            application.product_terms_snapshot,
            application.requested_amount,
            application.requested_term_months,
            application.currency,
            disbursement_date,
        )
    product = products.get_product(session, application.loan_product_id, include_inactive=True)
    return loans.preview_for_product(
        product,
        application.requested_amount,
        application.requested_term_months,
        disbursement_date,
    )
