"""Exhaustive tests for the loan application state machine (ARCHITECTURE.md §5.3).

Phase 3 exit criterion: every valid and every invalid transition is covered.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import replace

import pytest

from app.modules.applications.domain.state_machine import (
    DECIDED,
    MIN_RATIONALE_LENGTH,
    OPEN,
    PROFILE_LOCKED,
    RULES,
    TERMINAL,
    Action,
    Actor,
    ActorNotPermittedError,
    ApplicationStatus,
    GuardViolationError,
    TransitionFacts,
    TransitionNotAllowedError,
    available_actions,
    ensure_allowed,
    transition,
)
from app.modules.identity.roles import Role

S = ApplicationStatus
A = Action

OWNER_ID = uuid.uuid4()
OFFICER_ID = uuid.uuid4()
OTHER_OFFICER_ID = uuid.uuid4()
ANALYST_ID = uuid.uuid4()
ADMIN_ID = uuid.uuid4()

OWNER = Actor.owner(OWNER_ID)
SYSTEM = Actor.system()
OFFICER = Actor.staff(OFFICER_ID, Role.LOAN_OFFICER)
ANALYST = Actor.staff(ANALYST_ID, Role.CREDIT_ANALYST)
ADMIN = Actor.staff(ADMIN_ID, Role.ADMIN)

# The approved transition table, written out independently of RULES so that an accidental
# edit to the implementation fails here rather than silently changing the workflow.
EXPECTED: dict[tuple[ApplicationStatus, Action], ApplicationStatus] = {
    (S.DRAFT, A.SUBMIT): S.SUBMITTED,
    (S.SUBMITTED, A.QUEUE_FOR_REVIEW): S.PENDING_REVIEW,
    (S.PENDING_REVIEW, A.START_REVIEW): S.UNDER_REVIEW,
    (S.UNDER_REVIEW, A.REQUEST_INFORMATION): S.ADDITIONAL_INFO_REQUESTED,
    (S.ADDITIONAL_INFO_REQUESTED, A.RESUME_REVIEW): S.PENDING_REVIEW,
    (S.UNDER_REVIEW, A.RECOMMEND_APPROVAL): S.RECOMMENDED_APPROVAL,
    (S.UNDER_REVIEW, A.RECOMMEND_REJECTION): S.RECOMMENDED_REJECTION,
    (S.RECOMMENDED_APPROVAL, A.SEND_BACK): S.UNDER_REVIEW,
    (S.RECOMMENDED_REJECTION, A.SEND_BACK): S.UNDER_REVIEW,
    (S.RECOMMENDED_APPROVAL, A.APPROVE): S.APPROVED,
    (S.RECOMMENDED_REJECTION, A.APPROVE): S.APPROVED,
    (S.RECOMMENDED_APPROVAL, A.DECLINE): S.DECLINED,
    (S.RECOMMENDED_REJECTION, A.DECLINE): S.DECLINED,
    **{(status, A.WITHDRAW): S.WITHDRAWN for status in S if status not in TERMINAL},
}

# An actor and facts that satisfy every guard of each action.
VALID_ACTOR: dict[Action, Actor] = {
    A.SUBMIT: OWNER,
    A.QUEUE_FOR_REVIEW: SYSTEM,
    A.START_REVIEW: OFFICER,
    A.REQUEST_INFORMATION: OFFICER,
    A.RESUME_REVIEW: OWNER,
    A.RECOMMEND_APPROVAL: OFFICER,
    A.RECOMMEND_REJECTION: OFFICER,
    A.SEND_BACK: ANALYST,
    A.APPROVE: ANALYST,
    A.DECLINE: ANALYST,
    A.WITHDRAW: OWNER,
}

PASSING = TransitionFacts(
    product_active=True,
    amount_within_limits=True,
    term_within_limits=True,
    currency_matches_product=True,
    applicant_is_adult=True,
    employment_records=1,
    income_records=1,
    expense_records=1,
    records_in_other_currencies=0,
    has_affordability_assessment=True,
    risk_assessment_attempted=True,
    assigned_officer_id=OFFICER_ID,
    message="Please upload your latest three payslips.",
    open_information_requests=0,
    assessments_refreshed=True,
    rationale="Residual income comfortably covers the instalment.",
    reviewed_affordability_version=1,
    latest_affordability_version=1,
    recommender_id=OFFICER_ID,
    reason_codes=("MEETS_CRITERIA",),
    explanation="Affordability and the advisory risk band both support approval.",
    model_output_acknowledged=True,
)


def _facts(action: Action) -> TransitionFacts:
    """PASSING, with reason codes that support the decision being made."""
    if action is A.DECLINE:
        return replace(PASSING, reason_codes=("HIGH_DTI",))
    return PASSING


def test_expected_table_matches_rules() -> None:
    from_rules = {
        (source, action): rule.target for action, rule in RULES.items() for source in rule.sources
    }
    assert from_rules == EXPECTED


@pytest.mark.parametrize(("status", "action"), sorted(itertools.product(S, A)))
def test_every_status_action_pair(status: ApplicationStatus, action: Action) -> None:
    expected = EXPECTED.get((status, action))
    if expected is None:
        with pytest.raises(TransitionNotAllowedError) as excinfo:
            transition(status, action, VALID_ACTOR[action], _facts(action))
        assert excinfo.value.status is status
        assert excinfo.value.action is action
    else:
        result = transition(status, action, VALID_ACTOR[action], _facts(action))
        assert (result.source, result.target, result.action) == (status, expected, action)


def test_status_check_precedes_actor_check() -> None:
    # A stranger trying an impossible move learns only that the move is impossible.
    with pytest.raises(TransitionNotAllowedError):
        transition(S.APPROVED, A.APPROVE, OWNER, PASSING)


def test_terminal_states_have_no_exits() -> None:
    for status in TERMINAL:
        for action in A:
            with pytest.raises(TransitionNotAllowedError):
                transition(status, action, VALID_ACTOR[action], PASSING)


def test_no_path_reaches_a_decision_without_a_human() -> None:
    # Invariant 4: the model (or any automated step) never decides.
    for action, rule in RULES.items():
        if rule.target in DECIDED:
            assert rule.staff_roles <= {Role.CREDIT_ANALYST, Role.ADMIN}, action
            assert not rule.permits(SYSTEM), action


def test_status_groups() -> None:
    assert set(S) == OPEN | TERMINAL
    assert not OPEN & TERMINAL
    assert S.DRAFT not in PROFILE_LOCKED
    assert S.ADDITIONAL_INFO_REQUESTED not in PROFILE_LOCKED
    assert PROFILE_LOCKED <= OPEN


# --- Who may act -----------------------------------------------------------------------------

ALL_ACTORS = {
    "owner": OWNER,
    "system": SYSTEM,
    "officer": OFFICER,
    "analyst": ANALYST,
    "admin": ADMIN,
    "applicant_role_as_staff": Actor.staff(uuid.uuid4(), Role.APPLICANT),
}
ALLOWED_ACTORS: dict[Action, set[str]] = {
    A.SUBMIT: {"owner"},
    A.QUEUE_FOR_REVIEW: {"system"},
    A.START_REVIEW: {"officer"},
    A.REQUEST_INFORMATION: {"officer", "analyst"},
    A.RESUME_REVIEW: {"owner"},
    A.RECOMMEND_APPROVAL: {"officer"},
    A.RECOMMEND_REJECTION: {"officer"},
    A.SEND_BACK: {"analyst", "admin"},
    A.APPROVE: {"analyst", "admin"},
    A.DECLINE: {"analyst", "admin"},
    A.WITHDRAW: {"owner"},
}


@pytest.mark.parametrize(("action", "actor_name"), sorted(itertools.product(A, ALL_ACTORS)))
def test_actor_permissions(action: Action, actor_name: str) -> None:
    status = next(iter(sorted(RULES[action].sources)))
    actor = ALL_ACTORS[actor_name]
    facts = _facts(action)
    if action in {A.APPROVE, A.DECLINE}:
        facts = replace(facts, recommender_id=OTHER_OFFICER_ID)  # not the decider
    if action is A.START_REVIEW and actor.user_id:
        facts = replace(PASSING, assigned_officer_id=actor.user_id)
    if actor_name in ALLOWED_ACTORS[action]:
        assert transition(status, action, actor, facts).target is RULES[action].target
    else:
        with pytest.raises(ActorNotPermittedError):
            transition(status, action, actor, facts)


def test_available_actions() -> None:
    assert available_actions(S.DRAFT, OWNER) == [A.SUBMIT, A.WITHDRAW]
    assert available_actions(S.DRAFT, OFFICER) == []
    assert available_actions(S.PENDING_REVIEW, OFFICER) == [A.START_REVIEW]
    assert available_actions(S.UNDER_REVIEW, OFFICER) == [
        A.REQUEST_INFORMATION,
        A.RECOMMEND_APPROVAL,
        A.RECOMMEND_REJECTION,
    ]
    assert available_actions(S.UNDER_REVIEW, ANALYST) == [A.REQUEST_INFORMATION]
    assert available_actions(S.RECOMMENDED_APPROVAL, ADMIN) == [A.SEND_BACK, A.APPROVE, A.DECLINE]
    assert available_actions(S.APPROVED, ADMIN) == []
    assert available_actions(S.SUBMITTED, SYSTEM) == [A.QUEUE_FOR_REVIEW]


# --- Guards ------------------------------------------------------------------------------


def _violations(
    status: ApplicationStatus, action: Action, actor: Actor, **changes: object
) -> list[str]:
    with pytest.raises(GuardViolationError) as excinfo:
        transition(status, action, actor, replace(_facts(action), **changes))  # type: ignore[arg-type]
    return excinfo.value.violations


def test_missing_facts_fail_closed() -> None:
    # Facts default to failing values: forgetting to supply one can never pass a guard.
    for action, actor in VALID_ACTOR.items():
        if action is A.WITHDRAW:
            continue
        status = next(iter(sorted(RULES[action].sources)))
        with pytest.raises(GuardViolationError):
            transition(status, action, actor, None)


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"product_active": False}, "no longer available"),
        ({"amount_within_limits": False}, "amount"),
        ({"term_within_limits": False}, "term"),
        ({"currency_matches_product": False}, "currency must match"),
        ({"applicant_is_adult": False}, "18"),
        ({"employment_records": 0}, "employment"),
        ({"income_records": 0}, "income"),
        ({"expense_records": 0}, "expenses"),
        ({"records_in_other_currencies": 2}, "loan currency"),
    ],
)
def test_submit_guards(changes: dict[str, object], fragment: str) -> None:
    violations = _violations(S.DRAFT, A.SUBMIT, OWNER, **changes)
    assert len(violations) == 1
    assert fragment in violations[0]


def test_submit_reports_every_problem_at_once() -> None:
    violations = _violations(
        S.DRAFT, A.SUBMIT, OWNER, employment_records=0, income_records=0, expense_records=0
    )
    assert len(violations) == 3


def test_queue_requires_a_risk_attempt() -> None:
    violations = _violations(
        S.SUBMITTED, A.QUEUE_FOR_REVIEW, SYSTEM, risk_assessment_attempted=False
    )
    assert violations == ["a credit-risk assessment must be attempted first"]


@pytest.mark.parametrize("action", [A.RECOMMEND_APPROVAL, A.APPROVE, A.DECLINE])
def test_latest_scored_risk_assessment_must_be_cited(action: Action) -> None:
    status = S.UNDER_REVIEW if action is A.RECOMMEND_APPROVAL else S.RECOMMENDED_APPROVAL
    actor = OFFICER if action is A.RECOMMEND_APPROVAL else ANALYST
    base = replace(_facts(action), recommender_id=OTHER_OFFICER_ID)
    stale = replace(base, latest_risk_version=2, reviewed_risk_version=1)
    with pytest.raises(GuardViolationError) as excinfo:
        transition(status, action, actor, stale)
    assert any("latest credit-risk assessment (version 2)" in v for v in excinfo.value.violations)
    # Citing it passes; with no scored assessment there is nothing to cite.
    assert transition(
        status, action, actor, replace(base, latest_risk_version=2, reviewed_risk_version=2)
    )
    assert transition(status, action, actor, base)


def test_queue_requires_affordability_assessment() -> None:
    assert _violations(S.SUBMITTED, A.QUEUE_FOR_REVIEW, SYSTEM, has_affordability_assessment=False)


def test_start_review_requires_assignment_to_the_actor() -> None:
    assert (
        "assigned to a loan officer"
        in _violations(S.PENDING_REVIEW, A.START_REVIEW, OFFICER, assigned_officer_id=None)[0]
    )
    assert (
        "another loan officer"
        in _violations(
            S.PENDING_REVIEW, A.START_REVIEW, OFFICER, assigned_officer_id=OTHER_OFFICER_ID
        )[0]
    )


@pytest.mark.parametrize("message", [None, "", "   "])
def test_request_information_requires_a_message(message: str | None) -> None:
    assert _violations(S.UNDER_REVIEW, A.REQUEST_INFORMATION, ANALYST, message=message)


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"open_information_requests": 1}, "answered"),
        ({"employment_records": 0}, "employment"),
        ({"income_records": 0}, "income"),
        ({"expense_records": 0}, "expenses"),
        ({"records_in_other_currencies": 1}, "loan currency"),
        ({"assessments_refreshed": False}, "re-run"),
    ],
)
def test_resume_review_guards(changes: dict[str, object], fragment: str) -> None:
    violations = _violations(S.ADDITIONAL_INFO_REQUESTED, A.RESUME_REVIEW, OWNER, **changes)
    assert len(violations) == 1
    assert fragment in violations[0]


def test_resume_review_reports_the_real_problem_before_a_stale_assessment() -> None:
    # With an answer still missing, "re-run the assessment" would be misleading noise.
    violations = _violations(
        S.ADDITIONAL_INFO_REQUESTED,
        A.RESUME_REVIEW,
        OWNER,
        open_information_requests=2,
        assessments_refreshed=False,
    )
    assert violations == ["all information requests must be answered first"]


def test_ensure_allowed_checks_status_and_actor_only() -> None:
    rule = ensure_allowed(S.ADDITIONAL_INFO_REQUESTED, A.RESUME_REVIEW, OWNER)
    assert rule.target is S.PENDING_REVIEW
    with pytest.raises(TransitionNotAllowedError):
        ensure_allowed(S.UNDER_REVIEW, A.RESUME_REVIEW, OWNER)
    with pytest.raises(ActorNotPermittedError):
        ensure_allowed(S.ADDITIONAL_INFO_REQUESTED, A.RESUME_REVIEW, OFFICER)


@pytest.mark.parametrize("action", [A.RECOMMEND_APPROVAL, A.RECOMMEND_REJECTION])
def test_recommend_guards(action: Action) -> None:
    assert _violations(S.UNDER_REVIEW, action, OFFICER, reviewed_affordability_version=None)
    stale = _violations(S.UNDER_REVIEW, action, OFFICER, latest_affordability_version=2)
    assert stale == ["review the latest affordability assessment (version 2) before recommending"]
    missing = _violations(S.UNDER_REVIEW, action, OFFICER, latest_affordability_version=None)
    assert missing == ["an affordability assessment must exist before recommending"]
    short = "x" * (MIN_RATIONALE_LENGTH - 1)
    assert _violations(S.UNDER_REVIEW, action, OFFICER, rationale=short)
    padded = " " * 30 + "short" + " " * 30  # whitespace does not count towards the minimum
    assert _violations(S.UNDER_REVIEW, action, OFFICER, rationale=padded)
    exact = "x" * MIN_RATIONALE_LENGTH
    assert transition(S.UNDER_REVIEW, action, OFFICER, replace(PASSING, rationale=exact))


def test_send_back_requires_a_reason() -> None:
    assert _violations(S.RECOMMENDED_APPROVAL, A.SEND_BACK, ADMIN, message=" ")


@pytest.mark.parametrize("action", [A.APPROVE, A.DECLINE])
def test_maker_checker(action: Action) -> None:
    # Approved decision D2: the decider must not be the recommender.
    same_person = Actor.staff(OFFICER_ID, Role.CREDIT_ANALYST)
    violations = _violations(S.RECOMMENDED_APPROVAL, action, same_person)
    assert any("other than the recommender" in v for v in violations)
    assert transition(S.RECOMMENDED_APPROVAL, action, ANALYST, _facts(action))


@pytest.mark.parametrize("action", [A.APPROVE, A.DECLINE])
@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"recommender_id": None}, "recommendation must be recorded"),
        ({"reason_codes": ()}, "reason code"),
        ({"explanation": ""}, "explanation"),
        ({"model_output_acknowledged": False}, "advisory"),
        ({"latest_affordability_version": 3}, "latest affordability assessment (version 3)"),
        ({"reason_codes": ("NOPE",)}, "NOPE is not a recognised reason code"),
        ({"reason_codes": ("MEETS_CRITERIA", "MEETS_CRITERIA")}, "must not repeat"),
    ],
)
def test_decision_guards(action: Action, changes: dict[str, object], fragment: str) -> None:
    violations = _violations(S.RECOMMENDED_REJECTION, action, ADMIN, **changes)
    assert any(fragment in v for v in violations)


@pytest.mark.parametrize(
    ("action", "code", "allowed"),
    [
        (A.APPROVE, "MEETS_CRITERIA", True),
        (A.APPROVE, "COUNTER_OFFER", True),
        (A.APPROVE, "HIGH_DTI", False),
        (A.DECLINE, "AFFORDABILITY_SHORTFALL", True),
        (A.DECLINE, "MEETS_CRITERIA", False),
    ],
)
def test_reason_codes_must_support_the_decision(action: Action, code: str, allowed: bool) -> None:
    facts = replace(PASSING, recommender_id=OTHER_OFFICER_ID, reason_codes=(code,))
    if allowed:
        assert transition(S.RECOMMENDED_APPROVAL, action, ANALYST, facts)
    else:
        with pytest.raises(GuardViolationError) as excinfo:
            transition(S.RECOMMENDED_APPROVAL, action, ANALYST, facts)
        assert excinfo.value.violations == [
            f"{code} cannot support a decision to {action.value.lower()}"
        ]
