"""Loan application state machine (ARCHITECTURE.md §5.3).

Pure Python: no database, no HTTP. The service layer gathers the facts a transition needs,
asks :func:`transition` whether it is allowed, and only then persists the new status
together with a status-history row and an audit event.

Three kinds of failure are distinguished so callers can respond precisely:

* :class:`TransitionNotAllowedError` — the action is never valid from the current status.
* :class:`ActorNotPermittedError` — the action is valid, but not for this kind of actor.
* :class:`GuardViolationError` — the action and actor are valid, but a precondition fails.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.decisions.domain.reason_codes import DecisionOutcome, problems_for
from app.modules.identity.roles import Role

MIN_RATIONALE_LENGTH = 20


class ApplicationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    UNDER_REVIEW = "UNDER_REVIEW"
    ADDITIONAL_INFO_REQUESTED = "ADDITIONAL_INFO_REQUESTED"
    RECOMMENDED_APPROVAL = "RECOMMENDED_APPROVAL"
    RECOMMENDED_REJECTION = "RECOMMENDED_REJECTION"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


S = ApplicationStatus

DECIDED = frozenset({S.APPROVED, S.DECLINED})
TERMINAL = DECIDED | {S.WITHDRAWN}
OPEN = frozenset(S) - TERMINAL
RECOMMENDED = frozenset({S.RECOMMENDED_APPROVAL, S.RECOMMENDED_REJECTION})
# While an application is with the lender, the applicant's financial profile is frozen so
# reviewers assess what was submitted. DRAFT and ADDITIONAL_INFO_REQUESTED stay editable.
PROFILE_LOCKED = frozenset({S.SUBMITTED, S.PENDING_REVIEW, S.UNDER_REVIEW}) | RECOMMENDED


class Action(StrEnum):
    SUBMIT = "SUBMIT"
    QUEUE_FOR_REVIEW = "QUEUE_FOR_REVIEW"
    START_REVIEW = "START_REVIEW"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    RESUME_REVIEW = "RESUME_REVIEW"
    RECOMMEND_APPROVAL = "RECOMMEND_APPROVAL"
    RECOMMEND_REJECTION = "RECOMMEND_REJECTION"
    SEND_BACK = "SEND_BACK"
    APPROVE = "APPROVE"
    DECLINE = "DECLINE"
    WITHDRAW = "WITHDRAW"


class ActorKind(StrEnum):
    SYSTEM = "SYSTEM"
    OWNER = "OWNER"  # the applicant who owns the application
    STAFF = "STAFF"


@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    user_id: uuid.UUID | None = None
    role: Role | None = None

    @classmethod
    def system(cls) -> Actor:
        return cls(ActorKind.SYSTEM)

    @classmethod
    def owner(cls, user_id: uuid.UUID) -> Actor:
        return cls(ActorKind.OWNER, user_id, Role.APPLICANT)

    @classmethod
    def staff(cls, user_id: uuid.UUID, role: Role) -> Actor:
        return cls(ActorKind.STAFF, user_id, role)


@dataclass(frozen=True, slots=True)
class Rule:
    sources: frozenset[ApplicationStatus]
    target: ApplicationStatus
    actor_kind: ActorKind
    staff_roles: frozenset[Role] = frozenset()

    def permits(self, actor: Actor) -> bool:
        if actor.kind is not self.actor_kind:
            return False
        return actor.kind is not ActorKind.STAFF or actor.role in self.staff_roles


_OFFICER = frozenset({Role.LOAN_OFFICER})
_REVIEWERS = frozenset({Role.LOAN_OFFICER, Role.CREDIT_ANALYST})
_DECIDERS = frozenset({Role.CREDIT_ANALYST, Role.ADMIN})

RULES: dict[Action, Rule] = {
    Action.SUBMIT: Rule(frozenset({S.DRAFT}), S.SUBMITTED, ActorKind.OWNER),
    Action.QUEUE_FOR_REVIEW: Rule(frozenset({S.SUBMITTED}), S.PENDING_REVIEW, ActorKind.SYSTEM),
    Action.START_REVIEW: Rule(
        frozenset({S.PENDING_REVIEW}), S.UNDER_REVIEW, ActorKind.STAFF, _OFFICER
    ),
    Action.REQUEST_INFORMATION: Rule(
        frozenset({S.UNDER_REVIEW}), S.ADDITIONAL_INFO_REQUESTED, ActorKind.STAFF, _REVIEWERS
    ),
    Action.RESUME_REVIEW: Rule(
        frozenset({S.ADDITIONAL_INFO_REQUESTED}), S.PENDING_REVIEW, ActorKind.OWNER
    ),
    Action.RECOMMEND_APPROVAL: Rule(
        frozenset({S.UNDER_REVIEW}), S.RECOMMENDED_APPROVAL, ActorKind.STAFF, _OFFICER
    ),
    Action.RECOMMEND_REJECTION: Rule(
        frozenset({S.UNDER_REVIEW}), S.RECOMMENDED_REJECTION, ActorKind.STAFF, _OFFICER
    ),
    Action.SEND_BACK: Rule(RECOMMENDED, S.UNDER_REVIEW, ActorKind.STAFF, _DECIDERS),
    Action.APPROVE: Rule(
        frozenset({S.RECOMMENDED_APPROVAL, S.RECOMMENDED_REJECTION}),
        S.APPROVED,
        ActorKind.STAFF,
        _DECIDERS,
    ),
    Action.DECLINE: Rule(
        frozenset({S.RECOMMENDED_APPROVAL, S.RECOMMENDED_REJECTION}),
        S.DECLINED,
        ActorKind.STAFF,
        _DECIDERS,
    ),
    Action.WITHDRAW: Rule(OPEN, S.WITHDRAWN, ActorKind.OWNER),
}


@dataclass(frozen=True, slots=True)
class TransitionFacts:
    """Facts gathered by the service. Each action's guard reads only the fields it needs.

    Defaults are deliberately the *failing* values, so a fact the caller forgot to supply
    can never make a guard pass.
    """

    # SUBMIT (the profile counts also apply to RESUME_REVIEW)
    product_active: bool = False
    amount_within_limits: bool = False
    term_within_limits: bool = False
    currency_matches_product: bool = False
    applicant_is_adult: bool = False
    employment_records: int = 0
    income_records: int = 0
    expense_records: int = 0
    records_in_other_currencies: int = 0
    # QUEUE_FOR_REVIEW
    has_affordability_assessment: bool = False
    # START_REVIEW
    assigned_officer_id: uuid.UUID | None = None
    # REQUEST_INFORMATION / SEND_BACK
    message: str | None = None
    # RESUME_REVIEW
    open_information_requests: int = 0
    assessments_refreshed: bool = False
    # RECOMMEND_* and APPROVE / DECLINE: the evidence cited must be the latest assessment
    rationale: str | None = None
    reviewed_affordability_version: int | None = None
    latest_affordability_version: int | None = None
    # APPROVE / DECLINE
    recommender_id: uuid.UUID | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    explanation: str | None = None
    model_output_acknowledged: bool = False


class TransitionError(Exception):
    def __init__(self, message: str, *, action: Action, status: ApplicationStatus) -> None:
        super().__init__(message)
        self.message = message
        self.action = action
        self.status = status


class TransitionNotAllowedError(TransitionError):
    pass


class ActorNotPermittedError(TransitionError):
    pass


class GuardViolationError(TransitionError):
    def __init__(self, violations: list[str], *, action: Action, status: ApplicationStatus) -> None:
        super().__init__("; ".join(violations), action=action, status=status)
        self.violations = violations


@dataclass(frozen=True, slots=True)
class Transition:
    action: Action
    source: ApplicationStatus
    target: ApplicationStatus


type Guard = Callable[[TransitionFacts, Actor], list[str]]


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def _profile_problems(facts: TransitionFacts) -> list[str]:
    """A financial profile complete enough to assess (submission and resumed review)."""
    problems = []
    if facts.employment_records < 1:
        problems.append("capture your employment details before submitting")
    if facts.income_records < 1:
        problems.append("capture at least one income source before submitting")
    if facts.expense_records < 1:
        problems.append("capture your monthly expenses before submitting")
    if facts.records_in_other_currencies:
        problems.append("all income, expenses and commitments must be in the loan currency")
    return problems


def _guard_submit(facts: TransitionFacts, _: Actor) -> list[str]:
    problems = []
    if not facts.product_active:
        problems.append("the loan product is no longer available")
    if not facts.amount_within_limits:
        problems.append("the requested amount is outside the product's limits")
    if not facts.term_within_limits:
        problems.append("the requested term is outside the product's limits")
    if not facts.currency_matches_product:
        problems.append("the application currency must match the product currency")
    if not facts.applicant_is_adult:
        problems.append("the applicant must be at least 18 years old")
    return problems + _profile_problems(facts)


def _guard_queue(facts: TransitionFacts, _: Actor) -> list[str]:
    if not facts.has_affordability_assessment:
        return ["an affordability assessment must be recorded first"]
    return []


def _guard_start_review(facts: TransitionFacts, actor: Actor) -> list[str]:
    if facts.assigned_officer_id is None:
        return ["the application must be assigned to a loan officer"]
    if facts.assigned_officer_id != actor.user_id:
        return ["the application is assigned to another loan officer"]
    return []


def _guard_message(facts: TransitionFacts, _: Actor) -> list[str]:
    return ["a message explaining the request is required"] if _blank(facts.message) else []


def _guard_resume(facts: TransitionFacts, _: Actor) -> list[str]:
    problems = []
    if facts.open_information_requests:
        problems.append("all information requests must be answered first")
    # The profile was editable while information was outstanding, so check it again.
    problems += _profile_problems(facts)
    if not problems and not facts.assessments_refreshed:
        problems.append("assessments must be re-run on the updated information")
    return problems


def _evidence_problems(facts: TransitionFacts, step: str) -> list[str]:
    latest = facts.latest_affordability_version
    if latest is None:
        return [f"an affordability assessment must exist before {step}"]
    if facts.reviewed_affordability_version != latest:
        return [f"review the latest affordability assessment (version {latest}) before {step}"]
    return []


def _guard_recommend(facts: TransitionFacts, _: Actor) -> list[str]:
    problems = _evidence_problems(facts, "recommending")
    if _blank(facts.rationale) or len((facts.rationale or "").strip()) < MIN_RATIONALE_LENGTH:
        problems.append(f"a rationale of at least {MIN_RATIONALE_LENGTH} characters is required")
    return problems


def _guard_send_back(facts: TransitionFacts, _: Actor) -> list[str]:
    return (
        ["a reason for sending the application back is required"] if _blank(facts.message) else []
    )


def _decide_guard(outcome: DecisionOutcome) -> Guard:
    def guard(facts: TransitionFacts, actor: Actor) -> list[str]:
        problems = []
        # Maker–checker (approved decision D2): whoever recommended may not decide.
        if facts.recommender_id is None:
            problems.append("a recommendation must be recorded before a decision")
        elif facts.recommender_id == actor.user_id:
            problems.append("the decision must be made by someone other than the recommender")
        problems += _evidence_problems(facts, "deciding")
        if not facts.reason_codes:
            problems.append("at least one reason code is required")
        else:
            problems += problems_for(outcome, facts.reason_codes)
        if _blank(facts.explanation):
            problems.append("an explanation is required")
        if not facts.model_output_acknowledged:
            problems.append("acknowledge that the model output is advisory only")
        return problems

    return guard


def _no_guard(_: TransitionFacts, __: Actor) -> list[str]:
    return []


GUARDS: dict[Action, Guard] = {
    Action.SUBMIT: _guard_submit,
    Action.QUEUE_FOR_REVIEW: _guard_queue,
    Action.START_REVIEW: _guard_start_review,
    Action.REQUEST_INFORMATION: _guard_message,
    Action.RESUME_REVIEW: _guard_resume,
    Action.RECOMMEND_APPROVAL: _guard_recommend,
    Action.RECOMMEND_REJECTION: _guard_recommend,
    Action.SEND_BACK: _guard_send_back,
    Action.APPROVE: _decide_guard(DecisionOutcome.APPROVED),
    Action.DECLINE: _decide_guard(DecisionOutcome.DECLINED),
    Action.WITHDRAW: _no_guard,
}


def transition(
    status: ApplicationStatus,
    action: Action,
    actor: Actor,
    facts: TransitionFacts | None = None,
) -> Transition:
    """Validate ``action`` from ``status`` and return the resulting transition."""
    rule = ensure_allowed(status, action, actor)
    violations = GUARDS[action](facts or TransitionFacts(), actor)
    if violations:
        raise GuardViolationError(violations, action=action, status=status)
    return Transition(action, status, rule.target)


def ensure_allowed(status: ApplicationStatus, action: Action, actor: Actor) -> Rule:
    """Check status and actor only (no guards).

    For use cases that must do work, such as running an assessment, before the guard facts
    exist; they still call :func:`transition` afterwards.
    """
    rule = RULES[action]
    if status not in rule.sources:
        raise TransitionNotAllowedError(
            f"cannot {action.value.lower().replace('_', ' ')} an application that is "
            f"{status.value}",
            action=action,
            status=status,
        )
    if not rule.permits(actor):
        raise ActorNotPermittedError(
            f"{action.value} is not available to this user", action=action, status=status
        )
    return rule


def available_actions(status: ApplicationStatus, actor: Actor) -> list[Action]:
    """Actions whose status and actor rules pass (guards are not evaluated). For UI hints."""
    return [
        action for action, rule in RULES.items() if status in rule.sources and rule.permits(actor)
    ]
