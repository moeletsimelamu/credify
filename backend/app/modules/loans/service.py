"""Loan accounts: origination, repayments, arrears, early settlement and schedule previews.

The pure engines (``loans.engine``) decide what should happen; this module loads the
account, hands the engines frozen copies of its rows, and persists the answer together
with the transaction log and an audit event, in one database transaction.

Every servicing use case locks the account row (``SELECT … FOR UPDATE``), so repayments,
settlements and the arrears job never interleave on the same loan.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, business_date
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.ids import uuid7
from app.core.money import Currency
from app.core.pagination import PageParams
from app.modules.applicants.models import Applicant
from app.modules.audit import service as audit
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission, has_permission
from app.modules.loans.engine import allocation, amortisation, arrears, settlement
from app.modules.loans.engine.ledger import (
    REPAYABLE,
    SERVICING_VERSION,
    ZERO,
    ChargeState,
    ChargeType,
    Component,
    InstalmentState,
    InstalmentStatus,
    LoanStatus,
    ScheduleReason,
    TransactionType,
)
from app.modules.loans.models import (
    ACCOUNT_NUMBER_SEQUENCE,
    LoanAccount,
    LoanCharge,
    LoanTransaction,
    PaymentAllocation,
    RepaymentSchedule,
    ScheduleInstalment,
)
from app.modules.loans.schemas import RepaymentSchedulePreview
from app.modules.products.models import LoanProduct

MAX_QUOTE_HORIZON_DAYS = 365
PRINCIPAL_COMPONENTS = (Component.PRINCIPAL, Component.PREPAYMENT)


class LoanNotRepayableError(ConflictError):
    code = "LOAN_NOT_REPAYABLE"
    title = "The loan is not open for repayments"


class IdempotencyKeyReusedError(ConflictError):
    code = "IDEMPOTENCY_KEY_REUSED"
    title = "The Idempotency-Key was already used for a different request"


# --- Previews (no loan exists yet) -------------------------------------------------------


def _build(terms: amortisation.LoanTerms, disbursement_date: date) -> amortisation.Schedule:
    try:
        return amortisation.build_schedule(terms, disbursement_date)
    except amortisation.ScheduleError as exc:
        raise BusinessRuleError(str(exc), extra={"violations": [str(exc)]}) from exc


def limit_problems(product: LoanProduct, amount: Decimal, term_months: int) -> list[str]:
    problems = []
    if not product.min_amount <= amount <= product.max_amount:
        problems.append(
            f"amount must be between {product.min_amount} and {product.max_amount} "
            f"{product.currency.value}"
        )
    if not product.min_term_months <= term_months <= product.max_term_months:
        problems.append(
            f"term_months must be between {product.min_term_months} and {product.max_term_months}"
        )
    return problems


def preview_for_product(
    product: LoanProduct, amount: Decimal, term_months: int, disbursement_date: date
) -> RepaymentSchedulePreview:
    """A schedule under a product's *current* terms, within its limits."""
    problems = limit_problems(product, amount, term_months)
    if problems:
        raise BusinessRuleError("; ".join(problems), extra={"violations": problems})
    terms = amortisation.LoanTerms(
        amount=amount,
        annual_interest_rate=product.annual_interest_rate,
        term_months=term_months,
        initiation_fee=product.initiation_fee,
        monthly_service_fee=product.monthly_service_fee,
    )
    return RepaymentSchedulePreview.from_schedule(
        _build(terms, disbursement_date), product.currency
    )


def terms_from_snapshot(
    terms_snapshot: Mapping[str, Any], amount: Decimal, term_months: int
) -> amortisation.LoanTerms:
    return amortisation.LoanTerms(
        amount=amount,
        annual_interest_rate=Decimal(str(terms_snapshot["annual_interest_rate"])),
        term_months=term_months,
        initiation_fee=Decimal(str(terms_snapshot["initiation_fee"])),
        monthly_service_fee=Decimal(str(terms_snapshot["monthly_service_fee"])),
    )


def schedule_from_snapshot(
    terms_snapshot: Mapping[str, Any], amount: Decimal, term_months: int, disbursement_date: date
) -> amortisation.Schedule:
    return _build(terms_from_snapshot(terms_snapshot, amount, term_months), disbursement_date)


def preview_from_snapshot(
    terms_snapshot: Mapping[str, Any],
    amount: Decimal,
    term_months: int,
    currency: Currency,
    disbursement_date: date,
) -> RepaymentSchedulePreview:
    """A schedule under the terms frozen on an application at submission."""
    return RepaymentSchedulePreview.from_schedule(
        schedule_from_snapshot(terms_snapshot, amount, term_months, disbursement_date), currency
    )


# --- Loading with object-level access control --------------------------------------------


def _scoped[*Ts](query: Select[*Ts], user: User) -> Select[*Ts]:
    """Applicants only ever see loans attached to their own profile."""
    if has_permission(user.role, Permission.LOAN_READ_ANY):
        return query
    return query.join(Applicant, Applicant.id == LoanAccount.applicant_id).where(
        Applicant.user_id == user.id
    )


def load_account(
    session: Session, account_id: uuid.UUID, user: User, *, for_update: bool = False
) -> LoanAccount:
    query = _scoped(select(LoanAccount), user).where(LoanAccount.id == account_id)
    if for_update:
        query = query.with_for_update(of=LoanAccount)
    account = session.scalar(query)
    if account is None:
        # Same answer whether it does not exist or belongs to someone else (no enumeration).
        raise NotFoundError("Loan account not found")
    return account


def account_for_application(session: Session, application_id: uuid.UUID) -> LoanAccount | None:
    return session.scalar(select(LoanAccount).where(LoanAccount.application_id == application_id))


@dataclass(frozen=True, slots=True)
class AccountFilters:
    statuses: tuple[LoanStatus, ...] = ()
    application_id: uuid.UUID | None = None
    applicant_id: uuid.UUID | None = None
    min_days_past_due: int | None = None


def list_accounts(
    session: Session, user: User, params: PageParams, filters: AccountFilters
) -> tuple[list[LoanAccount], int]:
    query = _scoped(select(LoanAccount), user)
    if filters.statuses:
        query = query.where(LoanAccount.status.in_(filters.statuses))
    if filters.application_id:
        query = query.where(LoanAccount.application_id == filters.application_id)
    if filters.applicant_id:
        query = query.where(LoanAccount.applicant_id == filters.applicant_id)
    if filters.min_days_past_due is not None:
        query = query.where(LoanAccount.days_past_due >= filters.min_days_past_due)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(LoanAccount.disbursed_on.desc(), LoanAccount.account_number.desc())
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    return list(rows), total


# --- The account's book: effective schedule + charges -------------------------------------


@dataclass(slots=True)
class Book:
    """Everything the engines need about one account, loaded once per use case.

    ``instalments`` is the *effective* schedule. Each schedule version replaces every
    instalment from its ``first_instalment_number`` on, so the effective schedule is the
    earlier versions' rows before that number plus the newest version's rows. A recast only
    replaces instalments that nothing had been paid against, so earlier rows stay valid.
    """

    account: LoanAccount
    schedules: list[RepaymentSchedule]
    instalments: dict[int, ScheduleInstalment]
    charges: list[LoanCharge]
    numbers_by_row: dict[uuid.UUID, int] = field(default_factory=dict)

    @property
    def current_schedule(self) -> RepaymentSchedule:
        return next(s for s in self.schedules if s.is_current)

    def instalment_states(self) -> tuple[InstalmentState, ...]:
        return tuple(_instalment_state(row) for _, row in sorted(self.instalments.items()))

    def charge_states(self) -> tuple[ChargeState, ...]:
        return tuple(
            ChargeState(
                key=str(c.id),
                charge_type=c.charge_type,
                amount=c.amount,
                amount_paid=c.amount_paid,
                charged_on=c.charged_on,
                instalment_number=(
                    self.numbers_by_row.get(c.schedule_instalment_id)
                    if c.schedule_instalment_id
                    else None
                ),
            )
            for c in self.charges
        )


def _instalment_state(row: ScheduleInstalment) -> InstalmentState:
    return InstalmentState(
        number=row.instalment_number,
        due_date=row.due_date,
        interest_due=row.interest_due,
        principal_due=row.principal_due,
        service_fee_due=row.service_fee_due,
        interest_paid=row.interest_paid,
        principal_paid=row.principal_paid,
        service_fee_paid=row.service_fee_paid,
        cancelled=row.status is InstalmentStatus.CANCELLED,
    )


def load_book(session: Session, account: LoanAccount) -> Book:
    schedules = list(
        session.scalars(
            select(RepaymentSchedule)
            .where(RepaymentSchedule.loan_account_id == account.id)
            .order_by(RepaymentSchedule.version)
        ).all()
    )
    rows = session.scalars(
        select(ScheduleInstalment)
        .join(RepaymentSchedule, RepaymentSchedule.id == ScheduleInstalment.schedule_id)
        .where(RepaymentSchedule.loan_account_id == account.id)
        .order_by(RepaymentSchedule.version, ScheduleInstalment.instalment_number)
    ).all()
    first_numbers = {s.id: s.first_instalment_number for s in schedules}
    effective: dict[int, ScheduleInstalment] = {}
    current_schedule: uuid.UUID | None = None
    for row in rows:  # versions in order: each replaces everything from its first number on
        if row.schedule_id != current_schedule:
            current_schedule = row.schedule_id
            start = first_numbers[row.schedule_id]
            for number in [n for n in effective if n >= start]:
                del effective[number]
        effective[row.instalment_number] = row
    charges = list(
        session.scalars(
            select(LoanCharge)
            .where(LoanCharge.loan_account_id == account.id)
            .order_by(LoanCharge.charged_on, LoanCharge.id)
        ).all()
    )
    return Book(
        account=account,
        schedules=schedules,
        instalments=effective,
        charges=charges,
        numbers_by_row={row.id: row.instalment_number for row in rows},
    )


# --- Writing helpers ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is acting (None for the system, e.g. the arrears job), when, and from where."""

    user: User | None
    now: datetime
    ip_address: str | None = None


def _instalment_rows(
    schedule_id: uuid.UUID, rows: Sequence[amortisation.Instalment]
) -> list[ScheduleInstalment]:
    return [
        ScheduleInstalment(
            id=uuid7(),
            schedule_id=schedule_id,
            instalment_number=row.number,
            due_date=row.due_date,
            opening_balance=row.opening_balance,
            interest_due=row.interest,
            principal_due=row.principal,
            service_fee_due=row.service_fee,
            total_due=row.total_due,
            closing_balance=row.closing_balance,
            interest_paid=ZERO,
            principal_paid=ZERO,
            service_fee_paid=ZERO,
            status=InstalmentStatus.SCHEDULED,
        )
        for row in rows
    ]


def _transaction(
    account: LoanAccount,
    kind: TransactionType,
    amount: Decimal,
    value_date: date,
    *,
    reference: str,
    actor: Actor,
    balance_after: Decimal,
    idempotency_key: str | None = None,
) -> LoanTransaction:
    return LoanTransaction(
        id=uuid7(),
        loan_account_id=account.id,
        transaction_type=kind,
        amount=amount,
        currency=account.currency,
        value_date=value_date,
        idempotency_key=idempotency_key,
        channel="SIMULATED",
        reference=reference,
        recorded_by=actor.user.id if actor.user else None,
        balance_after=balance_after,
        created_at=actor.now,
    )


def _apply_allocations(
    session: Session,
    book: Book,
    transaction: LoanTransaction,
    allocations: Sequence[allocation.Allocation],
) -> None:
    """Add the allocation rows and move the paid amounts on instalments and charges."""
    charges = {str(c.id): c for c in book.charges}
    for item in allocations:
        row = PaymentAllocation(
            id=uuid7(),
            transaction_id=transaction.id,
            component=item.component,
            amount=item.amount,
        )
        if item.component is Component.CHARGE:
            assert item.charge_key is not None  # noqa: S101 — engine invariant
            charge = charges[item.charge_key]
            charge.amount_paid += item.amount
            row.loan_charge_id = charge.id
        elif item.component is not Component.PREPAYMENT:
            assert item.instalment_number is not None  # noqa: S101 — engine invariant
            inst = book.instalments[item.instalment_number]
            if item.component is Component.SERVICE_FEE:
                inst.service_fee_paid += item.amount
            elif item.component is Component.INTEREST:
                inst.interest_paid += item.amount
            else:
                inst.principal_paid += item.amount
            row.schedule_instalment_id = inst.id
        session.add(row)


def _audit(
    session: Session,
    actor: Actor,
    action: str,
    account: LoanAccount,
    *,
    entity_type: str = "loan_account",
    entity_id: uuid.UUID | None = None,
    changes: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> None:
    audit.record(
        session,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id or account.id,
        actor=actor.user,
        occurred_at=actor.now,
        ip_address=actor.ip_address,
        changes=changes,
        context={"account_number": account.account_number, **(context or {})},
    )


def _refresh_position(
    session: Session, book: Book, as_of: date, actor: Actor
) -> arrears.ArrearsPosition:
    """Recompute instalment statuses and the cached position as of ``as_of``."""
    account = book.account
    for row in book.instalments.values():
        row.status = arrears.instalment_status(_instalment_state(row), as_of)
    instalments, charges = book.instalment_states(), book.charge_states()
    position = arrears.position(instalments, charges, as_of)
    before = account.status
    if all(i.is_settled for i in instalments) and position.arrears_amount == 0:
        account.status = LoanStatus.SETTLED
        account.settled_on = account.settled_on or as_of
    elif position.days_past_due > 0:
        account.status = LoanStatus.IN_ARREARS
    else:
        account.status = LoanStatus.ACTIVE
    account.outstanding_principal = position.outstanding_principal
    account.arrears_amount = position.arrears_amount
    account.days_past_due = position.days_past_due
    account.position_as_of = as_of
    session.flush()
    if account.status is not before:
        _audit(
            session,
            actor,
            "loan_account.status_changed",
            account,
            changes={"status": {"from": before.value, "to": account.status.value}},
            context={"as_of": as_of, "days_past_due": position.days_past_due},
        )
    return position


def _charge_late_fees(session: Session, book: Book, as_of: date, actor: Actor) -> list[LoanCharge]:
    """Charge every late fee that has become due by ``as_of`` (once per instalment)."""
    account = book.account
    fees = arrears.late_fees_due(
        book.instalment_states(),
        book.charge_states(),
        as_of,
        late_fee=account.late_fee,
        grace_period_days=account.grace_period_days,
    )
    created = []
    for fee in sorted(fees, key=lambda f: (f.charged_on, f.instalment_number)):
        inst = book.instalments[fee.instalment_number]
        charge = LoanCharge(
            id=uuid7(),
            loan_account_id=account.id,
            schedule_instalment_id=inst.id,
            charge_type=ChargeType.LATE_FEE,
            amount=fee.amount,
            amount_paid=ZERO,
            charged_on=fee.charged_on,
            created_at=actor.now,
        )
        book.charges.append(charge)
        owed = arrears.position(book.instalment_states(), book.charge_states(), fee.charged_on)
        session.add(charge)
        session.add(
            _transaction(
                account,
                TransactionType.CHARGE,
                fee.amount,
                fee.charged_on,
                reference=f"Late fee: instalment {fee.instalment_number}",
                actor=actor,
                balance_after=owed.amount_owed,
            )
        )
        session.flush()
        _audit(
            session,
            actor,
            "loan_charge.created",
            account,
            entity_type="loan_charge",
            entity_id=charge.id,
            context={
                "charge_type": ChargeType.LATE_FEE.value,
                "amount": fee.amount,
                "instalment_number": fee.instalment_number,
                "charged_on": fee.charged_on,
            },
        )
        created.append(charge)
    return created


def _bring_up_to_date(
    session: Session, book: Book, as_of: date, actor: Actor
) -> arrears.ArrearsPosition:
    _charge_late_fees(session, book, as_of, actor)
    return _refresh_position(session, book, as_of, actor)


def _last_value_date(session: Session, account: LoanAccount) -> date:
    latest = session.scalar(
        select(func.max(LoanTransaction.value_date)).where(
            LoanTransaction.loan_account_id == account.id
        )
    )
    return max(latest or account.disbursed_on, account.position_as_of)


def _check_value_date(
    session: Session, account: LoanAccount, value_date: date, today: date
) -> None:
    if value_date > today:
        raise BusinessRuleError("value_date cannot be in the future")
    earliest = _last_value_date(session, account)
    if value_date < earliest:
        raise BusinessRuleError(
            f"value_date cannot be before {earliest}, the date the account was last updated",
            extra={"earliest_value_date": earliest.isoformat()},
        )


def _check_repayable(account: LoanAccount) -> None:
    if account.status not in REPAYABLE:
        raise LoanNotRepayableError(
            f"Loan {account.account_number} is {account.status.value}",
            extra={"status": account.status.value},
        )


def _check_may_pay(user: User) -> None:
    if not (
        has_permission(user.role, Permission.REPAYMENT_RECORD)
        or has_permission(user.role, Permission.REPAYMENT_RECORD_OWN)
    ):
        raise PermissionDeniedError(
            extra={"missing_permissions": [Permission.REPAYMENT_RECORD.value]}
        )


def _replayed(
    session: Session,
    account: LoanAccount,
    key: str,
    kind: TransactionType,
    amount: Decimal,
    value_date: date | None,
) -> LoanTransaction | None:
    """The original transaction for a replayed Idempotency-Key, if this is a replay."""
    existing = session.scalar(select(LoanTransaction).where(LoanTransaction.idempotency_key == key))
    if existing is None:
        return None
    same = (
        existing.loan_account_id == account.id
        and existing.transaction_type is kind
        and existing.amount == amount
        and (value_date is None or existing.value_date == value_date)
    )
    if not same:
        raise IdempotencyKeyReusedError(
            "This Idempotency-Key was already used for a different request. "
            "Use a new key for a new payment."
        )
    return existing


# --- Origination -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovedLoan:
    """What the decision approved, with the product terms frozen at submission."""

    application_id: uuid.UUID
    applicant_id: uuid.UUID
    decision_id: uuid.UUID
    reference: str
    currency: Currency
    amount: Decimal
    term_months: int
    terms_snapshot: Mapping[str, Any]


def _next_account_number(session: Session) -> str:
    number = session.scalar(select(ACCOUNT_NUMBER_SEQUENCE.next_value()))
    return f"LA-{number:06d}"


def open_account(
    session: Session, approved: ApprovedLoan, *, disbursed_on: date, actor: Actor
) -> LoanAccount:
    """Create the account, schedule v1 and the disbursement (no commit: caller's transaction).

    Disbursement is simulated: no money moves anywhere.
    """
    terms = approved.terms_snapshot
    schedule = schedule_from_snapshot(terms, approved.amount, approved.term_months, disbursed_on)
    account = LoanAccount(
        id=uuid7(),
        account_number=_next_account_number(session),
        application_id=approved.application_id,
        applicant_id=approved.applicant_id,
        decision_id=approved.decision_id,
        currency=approved.currency,
        approved_amount=approved.amount,
        initiation_fee=schedule.terms.initiation_fee,
        principal=schedule.principal,
        annual_interest_rate=schedule.terms.annual_interest_rate,
        term_months=approved.term_months,
        instalment_amount=schedule.level_instalment,
        monthly_service_fee=schedule.terms.monthly_service_fee,
        late_fee=Decimal(str(terms["late_fee"])),
        grace_period_days=int(terms["grace_period_days"]),
        early_settlement_fee_rate=Decimal(str(terms["early_settlement_fee_rate"])),
        disbursed_on=disbursed_on,
        first_due_date=schedule.instalments[0].due_date,
        maturity_date=schedule.instalments[-1].due_date,
        status=LoanStatus.ACTIVE,
        outstanding_principal=schedule.principal,
        arrears_amount=ZERO,
        days_past_due=0,
        position_as_of=disbursed_on,
    )
    session.add(account)
    session.flush()
    version = RepaymentSchedule(
        id=uuid7(),
        loan_account_id=account.id,
        version=1,
        reason=ScheduleReason.ORIGINATION,
        engine_version=schedule.version,
        first_instalment_number=1,
        is_current=True,
        created_at=actor.now,
    )
    session.add(version)
    session.flush()
    session.add_all(_instalment_rows(version.id, schedule.instalments))
    fee_note = (
        f" (includes capitalised initiation fee {schedule.terms.initiation_fee})"
        if schedule.terms.initiation_fee
        else ""
    )
    disbursement = _transaction(
        account,
        TransactionType.DISBURSEMENT,
        schedule.principal,
        disbursed_on,
        reference=f"Simulated disbursement for {approved.reference}{fee_note}",
        actor=actor,
        balance_after=schedule.principal,
    )
    session.add(disbursement)
    session.flush()
    _audit(
        session,
        actor,
        "loan_account.opened",
        account,
        changes=audit.diff(
            {},
            {
                "status": account.status.value,
                "principal": account.principal,
                "annual_interest_rate": account.annual_interest_rate,
                "term_months": account.term_months,
                "instalment_amount": account.instalment_amount,
            },
        ),
        context={
            "application_id": approved.application_id,
            "decision_id": approved.decision_id,
            "disbursed_on": disbursed_on,
            "engine_version": schedule.version,
        },
    )
    return account


# --- Repayments --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaymentResult:
    account: LoanAccount
    transaction: LoanTransaction
    replayed: bool


def _future_principal(instalments: Sequence[InstalmentState], value_date: date) -> Decimal:
    return sum(
        (
            i.principal_outstanding
            for i in instalments
            if i.due_date > value_date and not i.cancelled
        ),
        ZERO,
    )


def _recast(
    session: Session,
    book: Book,
    prepayment: Decimal,
    value_date: date,
    transaction: LoanTransaction,
) -> RepaymentSchedule:
    """A new schedule version from the next instalment: same instalment, shorter term."""
    account = book.account
    future = sorted(
        (row for row in book.instalments.values() if row.due_date > value_date),
        key=lambda row: row.instalment_number,
    )
    balance = sum((row.principal_due - row.principal_paid for row in future), ZERO) - prepayment
    rows = amortisation.recast(
        balance,
        annual_interest_rate=account.annual_interest_rate,
        level=account.instalment_amount,
        first_number=future[0].instalment_number,
        disbursement_date=account.disbursed_on,
        monthly_service_fee=account.monthly_service_fee,
    )
    current = book.current_schedule
    current.is_current = False
    session.flush()  # release the one-current-schedule index before adding the new version
    version = RepaymentSchedule(
        id=uuid7(),
        loan_account_id=account.id,
        version=current.version + 1,
        reason=ScheduleReason.PREPAYMENT_RECAST,
        engine_version=amortisation.VERSION,
        first_instalment_number=rows[0].number,
        is_current=True,
        transaction_id=transaction.id,
        created_at=transaction.created_at,
    )
    session.add(version)
    session.flush()
    new_rows = _instalment_rows(version.id, rows)
    session.add_all(new_rows)
    session.flush()
    for row in future:
        del book.instalments[row.instalment_number]
    for row in new_rows:
        book.instalments[row.instalment_number] = row
        book.numbers_by_row[row.id] = row.instalment_number
    book.schedules.append(version)
    account.maturity_date = rows[-1].due_date
    return version


def record_repayment(
    session: Session,
    account_id: uuid.UUID,
    *,
    amount: Decimal,
    value_date: date | None,
    idempotency_key: str,
    user: User,
    clock: Clock,
    ip_address: str | None,
) -> PaymentResult:
    """Record a simulated repayment and allocate it through the waterfall."""
    _check_may_pay(user)
    account = load_account(session, account_id, user, for_update=True)
    replay = _replayed(
        session, account, idempotency_key, TransactionType.REPAYMENT, amount, value_date
    )
    if replay is not None:
        return PaymentResult(account, replay, replayed=True)
    _check_repayable(account)
    today = business_date(clock)
    value_date = value_date or today
    _check_value_date(session, account, value_date, today)

    actor = Actor(user, clock.now(), ip_address)
    book = load_book(session, account)
    _bring_up_to_date(session, book, value_date, actor)
    instalments, charges = book.instalment_states(), book.charge_states()
    try:
        result = allocation.allocate(amount, value_date, instalments, charges)
    except allocation.AllocationError as exc:
        raise BusinessRuleError(str(exc)) from exc
    if result.prepayment:
        future = _future_principal(instalments, value_date)
        if result.prepayment >= future:
            owed = allocation.amount_due(value_date, instalments, charges)
            raise BusinessRuleError(
                "This payment would repay the whole loan. Request a settlement quote and pay "
                "the quoted amount to settle it.",
                extra={"amount_due": str(owed), "principal_not_yet_due": str(future)},
            )

    # The log is append-only, so work out the balance after the payment before writing it.
    owed_after = _owed_after(instalments, charges, result, value_date)

    transaction = _transaction(
        account,
        TransactionType.REPAYMENT,
        amount,
        value_date,
        reference="Simulated repayment",
        actor=actor,
        balance_after=owed_after,
        idempotency_key=idempotency_key,
    )
    session.add(transaction)
    session.flush()
    _apply_allocations(session, book, transaction, result.allocations)
    if result.prepayment:
        _apply_allocations(
            session,
            book,
            transaction,
            [allocation.Allocation(Component.PREPAYMENT, result.prepayment)],
        )
        _recast(session, book, result.prepayment, value_date, transaction)
    session.flush()
    position = _refresh_position(session, book, value_date, actor)
    _audit(
        session,
        actor,
        "loan_transaction.recorded",
        account,
        entity_type="loan_transaction",
        entity_id=transaction.id,
        context={
            "loan_account_id": account.id,
            "transaction_type": TransactionType.REPAYMENT.value,
            "amount": amount,
            "value_date": value_date,
            "prepayment": result.prepayment,
            "engine_version": SERVICING_VERSION,
            "status_after": account.status.value,
            "days_past_due_after": position.days_past_due,
        },
    )
    session.commit()
    return PaymentResult(account, transaction, replayed=False)


_PAID_FIELD = {
    Component.SERVICE_FEE: "service_fee_paid",
    Component.INTEREST: "interest_paid",
    Component.PRINCIPAL: "principal_paid",
}


def _owed_after(
    instalments: tuple[InstalmentState, ...],
    charges: tuple[ChargeState, ...],
    result: allocation.AllocationResult,
    value_date: date,
) -> Decimal:
    """What the borrower owes once ``result`` is applied (the prepayment reduces principal)."""
    by_number = {i.number: i for i in instalments}
    by_key = {c.key: c for c in charges}
    for item in result.allocations:
        if item.charge_key is not None:
            charge = by_key[item.charge_key]
            by_key[item.charge_key] = replace(charge, amount_paid=charge.amount_paid + item.amount)
        elif item.instalment_number is not None:
            inst = by_number[item.instalment_number]
            name = _PAID_FIELD[item.component]
            by_number[inst.number] = replace(inst, **{name: getattr(inst, name) + item.amount})
    position = arrears.position(tuple(by_number.values()), tuple(by_key.values()), value_date)
    return position.amount_owed - result.prepayment


# --- Early settlement --------------------------------------------------------------------


def _quote(book: Book, as_of: date) -> settlement.SettlementQuote:
    account = book.account
    try:
        return settlement.quote(
            book.instalment_states(),
            book.charge_states(),
            as_of,
            disbursed_on=account.disbursed_on,
            annual_interest_rate=account.annual_interest_rate,
            early_settlement_fee_rate=account.early_settlement_fee_rate,
            late_fee=account.late_fee,
            grace_period_days=account.grace_period_days,
        )
    except settlement.SettlementError as exc:
        raise BusinessRuleError(str(exc)) from exc


def settlement_quote(
    session: Session, account_id: uuid.UUID, user: User, *, as_of: date | None, clock: Clock
) -> tuple[LoanAccount, settlement.SettlementQuote]:
    """What it would cost to settle on ``as_of`` (default today). Nothing is written."""
    account = load_account(session, account_id, user)
    _check_repayable(account)
    today = business_date(clock)
    as_of = as_of or today
    earliest = max(today, _last_value_date(session, account))
    if as_of < earliest:
        raise BusinessRuleError(
            f"A settlement quote can only be produced for {earliest} or later",
            extra={"earliest_date": earliest.isoformat()},
        )
    if as_of > today + timedelta(days=MAX_QUOTE_HORIZON_DAYS):
        raise BusinessRuleError(
            f"A settlement quote can be at most {MAX_QUOTE_HORIZON_DAYS} days ahead"
        )
    return account, _quote(load_book(session, account), as_of)


def settle(
    session: Session,
    account_id: uuid.UUID,
    *,
    amount: Decimal,
    idempotency_key: str,
    user: User,
    clock: Clock,
    ip_address: str | None,
) -> PaymentResult:
    """Pay today's settlement quote in full: the loan becomes SETTLED."""
    _check_may_pay(user)
    account = load_account(session, account_id, user, for_update=True)
    replay = _replayed(session, account, idempotency_key, TransactionType.SETTLEMENT, amount, None)
    if replay is not None:
        return PaymentResult(account, replay, replayed=True)
    _check_repayable(account)
    today = business_date(clock)
    _check_value_date(session, account, today, today)

    actor = Actor(user, clock.now(), ip_address)
    book = load_book(session, account)
    _bring_up_to_date(session, book, today, actor)
    offer = _quote(book, today)
    if amount != offer.total:
        raise BusinessRuleError(
            f"The settlement amount on {today} is {offer.total} {account.currency.value}",
            extra={"settlement_amount": str(offer.total), "as_of": today.isoformat()},
        )
    if offer.settlement_fee > 0:
        fee = LoanCharge(
            id=uuid7(),
            loan_account_id=account.id,
            schedule_instalment_id=None,
            charge_type=ChargeType.EARLY_SETTLEMENT_FEE,
            amount=offer.settlement_fee,
            amount_paid=ZERO,
            charged_on=today,
            created_at=actor.now,
        )
        book.charges.append(fee)
        owed = arrears.position(book.instalment_states(), book.charge_states(), today)
        session.add(fee)
        session.add(
            _transaction(
                account,
                TransactionType.CHARGE,
                offer.settlement_fee,
                today,
                reference="Early settlement fee",
                actor=actor,
                balance_after=owed.amount_owed,
            )
        )
        session.flush()
    try:
        allocations = settlement.allocations(offer, book.instalment_states(), book.charge_states())
    except settlement.SettlementError as exc:  # pragma: no cover - guarded by the quote above
        raise BusinessRuleError(str(exc)) from exc

    transaction = _transaction(
        account,
        TransactionType.SETTLEMENT,
        amount,
        today,
        reference=f"Early settlement (quote for {today})",
        actor=actor,
        balance_after=ZERO,
        idempotency_key=idempotency_key,
    )
    session.add(transaction)
    session.flush()
    _apply_allocations(session, book, transaction, allocations)
    for row in book.instalments.values():
        if row.due_date > today:
            row.status = InstalmentStatus.CANCELLED  # remaining instalments are cancelled
    session.flush()
    account.settled_on = today
    position = _refresh_position(session, book, today, actor)
    _audit(
        session,
        actor,
        "loan_transaction.recorded",
        account,
        entity_type="loan_transaction",
        entity_id=transaction.id,
        context={
            "loan_account_id": account.id,
            "transaction_type": TransactionType.SETTLEMENT.value,
            "amount": amount,
            "value_date": today,
            "accrued_interest": offer.accrued_interest,
            "settlement_fee": offer.settlement_fee,
            "engine_version": SERVICING_VERSION,
            "status_after": account.status.value,
            "outstanding_principal_after": position.outstanding_principal,
        },
    )
    session.commit()
    return PaymentResult(account, transaction, replayed=False)


# --- Arrears job -------------------------------------------------------------------------


@dataclass(slots=True)
class ArrearsRunReport:
    as_of: date
    evaluated: int = 0
    skipped: list[str] = field(default_factory=list)
    late_fees_charged: int = 0
    in_arrears: int = 0
    status_changes: int = 0
    discrepancies: dict[str, list[str]] = field(default_factory=dict)


def run_arrears(session: Session, *, as_of: date, now: datetime) -> ArrearsRunReport:
    """Bring every open loan up to date as of ``as_of``: late fees, statuses, cached figures.

    Idempotent: running it twice for the same date changes nothing the second time. Each
    loan is committed on its own, so one problem account never blocks the rest.
    """
    report = ArrearsRunReport(as_of=as_of)
    actor = Actor(None, now)
    ids = session.scalars(
        select(LoanAccount.id)
        .where(LoanAccount.status.in_(REPAYABLE))
        .order_by(LoanAccount.account_number)
    ).all()
    for account_id in ids:
        account = session.scalar(
            select(LoanAccount).where(LoanAccount.id == account_id).with_for_update()
        )
        if account is None or account.status not in REPAYABLE:  # changed since listing
            session.rollback()
            continue
        if account.position_as_of > as_of:
            report.skipped.append(account.account_number)
            session.rollback()
            continue
        before = account.status
        book = load_book(session, account)
        report.late_fees_charged += len(_charge_late_fees(session, book, as_of, actor))
        _refresh_position(session, book, as_of, actor)
        report.evaluated += 1
        report.in_arrears += account.status is LoanStatus.IN_ARREARS
        report.status_changes += account.status is not before
        problems = reconcile(session, account)
        if problems:
            report.discrepancies[account.account_number] = problems
        session.commit()
    audit.record(
        session,
        action="arrears_job.completed",
        entity_type="job",
        entity_id="arrears",
        occurred_at=now,
        context={
            "as_of": as_of,
            "evaluated": report.evaluated,
            "skipped": report.skipped,
            "late_fees_charged": report.late_fees_charged,
            "in_arrears": report.in_arrears,
            "status_changes": report.status_changes,
            "discrepancies": report.discrepancies,
        },
    )
    session.commit()
    return report


# --- Reconciliation ----------------------------------------------------------------------


def reconcile(session: Session, account: LoanAccount) -> list[str]:
    """Check the cached figures and paid amounts against the transaction log."""
    problems = []
    book = load_book(session, account)
    rows = session.execute(
        select(PaymentAllocation, LoanTransaction.transaction_type)
        .join(LoanTransaction, LoanTransaction.id == PaymentAllocation.transaction_id)
        .where(LoanTransaction.loan_account_id == account.id)
    ).all()
    allocations = [row[0] for row in rows]
    principal_repaid = sum(
        (
            a.amount
            for a in allocations
            if a.component in (Component.PRINCIPAL, Component.PREPAYMENT)
        ),
        ZERO,
    )
    from_log = account.principal - principal_repaid
    from_schedule = sum(
        (row.principal_due - row.principal_paid for row in book.instalments.values()), ZERO
    )
    if from_log != account.outstanding_principal:
        problems.append(
            f"cached outstanding principal {account.outstanding_principal} != {from_log} "
            "from the transaction log"
        )
    if from_schedule != from_log:
        problems.append(f"schedule principal {from_schedule} != {from_log} from the log")

    by_instalment: dict[tuple[uuid.UUID, Component], Decimal] = {}
    by_charge: dict[uuid.UUID, Decimal] = {}
    for a in allocations:
        if a.schedule_instalment_id is not None:
            key = (a.schedule_instalment_id, a.component)
            by_instalment[key] = by_instalment.get(key, ZERO) + a.amount
        if a.loan_charge_id is not None:
            by_charge[a.loan_charge_id] = by_charge.get(a.loan_charge_id, ZERO) + a.amount
    for row in book.instalments.values():
        for component, paid in (
            (Component.SERVICE_FEE, row.service_fee_paid),
            (Component.INTEREST, row.interest_paid),
            (Component.PRINCIPAL, row.principal_paid),
        ):
            logged = by_instalment.get((row.id, component), ZERO)
            if logged != paid:
                problems.append(
                    f"instalment {row.instalment_number} {component.value.lower()} paid {paid} "
                    f"!= {logged} allocated"
                )
    for charge in book.charges:
        logged = by_charge.get(charge.id, ZERO)
        if logged != charge.amount_paid:
            problems.append(f"charge {charge.id} paid {charge.amount_paid} != {logged} allocated")

    totals = session.execute(
        select(
            LoanTransaction.id,
            LoanTransaction.amount,
            func.coalesce(func.sum(PaymentAllocation.amount), 0),
        )
        .outerjoin(PaymentAllocation, PaymentAllocation.transaction_id == LoanTransaction.id)
        .where(
            LoanTransaction.loan_account_id == account.id,
            LoanTransaction.transaction_type.in_(
                [TransactionType.REPAYMENT, TransactionType.SETTLEMENT]
            ),
        )
        .group_by(LoanTransaction.id, LoanTransaction.amount)
    ).all()
    for transaction_id, amount, allocated in totals:
        if allocated != amount:
            problems.append(f"transaction {transaction_id} of {amount} allocated {allocated}")
    return problems


# --- Reads -------------------------------------------------------------------------------


def effective_schedule(
    session: Session, account_id: uuid.UUID, user: User
) -> tuple[LoanAccount, Book]:
    account = load_account(session, account_id, user)
    return account, load_book(session, account)


def schedule_version(
    session: Session, account_id: uuid.UUID, version: int, user: User
) -> tuple[RepaymentSchedule, Sequence[ScheduleInstalment]]:
    account = load_account(session, account_id, user)
    schedule = session.scalar(
        select(RepaymentSchedule).where(
            RepaymentSchedule.loan_account_id == account.id, RepaymentSchedule.version == version
        )
    )
    if schedule is None:
        raise NotFoundError("Schedule version not found")
    rows = session.scalars(
        select(ScheduleInstalment)
        .where(ScheduleInstalment.schedule_id == schedule.id)
        .order_by(ScheduleInstalment.instalment_number)
    ).all()
    return schedule, rows


def list_schedules(
    session: Session, account_id: uuid.UUID, user: User
) -> Sequence[RepaymentSchedule]:
    account = load_account(session, account_id, user)
    return session.scalars(
        select(RepaymentSchedule)
        .where(RepaymentSchedule.loan_account_id == account.id)
        .order_by(RepaymentSchedule.version)
    ).all()


def list_transactions(
    session: Session, account_id: uuid.UUID, user: User
) -> tuple[Sequence[LoanTransaction], dict[uuid.UUID, list[PaymentAllocation]]]:
    account = load_account(session, account_id, user)
    transactions = session.scalars(
        select(LoanTransaction)
        .where(LoanTransaction.loan_account_id == account.id)
        .order_by(LoanTransaction.value_date, LoanTransaction.created_at, LoanTransaction.id)
    ).all()
    grouped: dict[uuid.UUID, list[PaymentAllocation]] = {t.id: [] for t in transactions}
    if transactions:
        for row in session.scalars(
            select(PaymentAllocation)
            .where(PaymentAllocation.transaction_id.in_(list(grouped)))
            .order_by(PaymentAllocation.id)
        ).all():
            grouped[row.transaction_id].append(row)
    return transactions, grouped


def allocations_for(session: Session, transaction_id: uuid.UUID) -> Sequence[PaymentAllocation]:
    return session.scalars(
        select(PaymentAllocation)
        .where(PaymentAllocation.transaction_id == transaction_id)
        .order_by(PaymentAllocation.id)
    ).all()


def list_charges(session: Session, account_id: uuid.UUID, user: User) -> Sequence[LoanCharge]:
    account = load_account(session, account_id, user)
    return session.scalars(
        select(LoanCharge)
        .where(LoanCharge.loan_account_id == account.id)
        .order_by(LoanCharge.charged_on, LoanCharge.id)
    ).all()
