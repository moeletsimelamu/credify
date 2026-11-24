"""Deterministic synthetic demo data (DATABASE.md §5).

    python -m scripts.seed_demo_data [--reset] [--seed 2026] [--as-of YYYY-MM-DD]
                                     [--credentials-file ../demo-credentials.txt]

Everything is created through the same service functions the API uses, with a simulated
clock that walks forward through time, so every record obeys the real business rules and
has a full audit trail: staff for each role, three loan products, 40 applicants with
applications in every status, and 15 loan accounts with on-time, late, in-arrears,
prepaid and settled repayment histories.

All people, employers and amounts are synthetic. The same ``--seed`` and ``--as-of``
always produce the same data. Passwords are generated fresh on every run and written only
to the (git-ignored) credentials file. The script refuses to run in production, and
refuses to touch a database that already has users unless ``--reset`` is given.
"""

from __future__ import annotations

import argparse
import random
import secrets
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import app.db_models  # noqa: F401 — register every model so foreign keys resolve
from app.core.clock import BUSINESS_TZ, FixedClock, SystemClock, business_date
from app.core.config import get_settings
from app.core.db import Base, build_engine, build_session_factory
from app.core.money import Currency
from app.modules.affordability import service as affordability
from app.modules.affordability.models import AffordabilityAssessment
from app.modules.applicants import service as applicants
from app.modules.applicants.domain.enums import (
    CommitmentType,
    EmploymentType,
    ExpenseCategory,
    IncomeFrequency,
    IncomeSourceType,
)
from app.modules.applicants.models import Applicant
from app.modules.applicants.schemas import (
    ApplicantCreate,
    CommitmentCreate,
    EmploymentCreate,
    ExpenseCreate,
    IncomeCreate,
)
from app.modules.applications import service as applications
from app.modules.applications.domain.enums import LoanPurpose
from app.modules.applications.models import LoanApplication
from app.modules.applications.schemas import ApplicationCreate
from app.modules.decisions.domain.reason_codes import DecisionOutcome, Recommendation
from app.modules.decisions.schemas import DecisionCreate, RecommendationCreate
from app.modules.identity import service as identity
from app.modules.identity.models import Role, User
from app.modules.identity.schemas import RegisterRequest, UserCreate
from app.modules.loans import service as loans
from app.modules.loans.engine import allocation, arrears
from app.modules.loans.models import LoanAccount
from app.modules.products import service as products
from app.modules.products.models import LoanProduct
from app.modules.products.schemas import ProductCreate

D = Decimal
EMAIL_DOMAIN = "credify-demo.example"  # reserved TLD: these addresses can never be real
DEFAULT_CREDENTIALS = Path("..") / "demo-credentials.txt"

FIRST_NAMES = [
    "Thandi", "Sipho", "Lerato", "Johan", "Ayesha", "Kagiso", "Naledi", "Pieter", "Zanele",
    "Ravi", "Busisiwe", "Themba", "Annika", "Mpho", "Fatima", "Lwazi", "Chloe", "Tshepo",
    "Nomsa", "Daniel",
]  # fmt: skip
LAST_NAMES = [
    "Mokoena", "Dlamini", "Naidoo", "van der Merwe", "Khumalo", "Botha", "Pillay", "Nkosi",
    "Smith", "Mahlangu", "Jacobs", "Ndlovu", "Pretorius", "Molefe", "Govender", "Zulu",
]  # fmt: skip
CITIES = [("Pretoria", "0002"), ("Johannesburg", "2001"), ("Cape Town", "8001"),
          ("Durban", "4001"), ("Gqeberha", "6001"), ("Bloemfontein", "9301")]  # fmt: skip
EMPLOYERS = ["Synthetic Mining Co", "Example Retail Group", "Demo Health Trust",
             "Placeholder Logistics", "Fictional Bank Services", "Sample Municipality"]  # fmt: skip

STAFF = [
    (Role.ADMIN, "Demo Administrator"),
    (Role.LOAN_OFFICER, "Lindiwe Officer"),
    (Role.LOAN_OFFICER, "Marco Officer"),
    (Role.CREDIT_ANALYST, "Priya Analyst"),
    (Role.CREDIT_ANALYST, "Sizwe Analyst"),
]

PRODUCTS = [
    ProductCreate(
        code="PERSONAL_UNSECURED",
        name="Personal unsecured loan",
        description="Synthetic demo product: general-purpose personal loan.",
        currency=Currency.ZAR,
        min_amount=D("5000.00"), max_amount=D("150000.00"),
        min_term_months=6, max_term_months=60,
        annual_interest_rate=D("0.215000"),
        initiation_fee=D("0.00"), monthly_service_fee=D("69.00"),
        late_fee=D("100.00"), grace_period_days=5, early_settlement_fee_rate=D("0.000000"),
        max_dti=D("0.4500"), warn_dti=D("0.3500"),
        min_residual_amount=D("1500.00"), min_residual_rate=D("0.1000"),
    ),
    ProductCreate(
        code="DEBT_CONSOLIDATION",
        name="Debt consolidation loan",
        description="Synthetic demo product: lower rate, longer terms, settlement fee.",
        currency=Currency.ZAR,
        min_amount=D("20000.00"), max_amount=D("250000.00"),
        min_term_months=12, max_term_months=72,
        annual_interest_rate=D("0.180000"),
        initiation_fee=D("1207.50"), monthly_service_fee=D("69.00"),
        late_fee=D("150.00"), grace_period_days=7, early_settlement_fee_rate=D("0.010000"),
        max_dti=D("0.5000"), warn_dti=D("0.4000"),
        min_residual_amount=D("2000.00"), min_residual_rate=D("0.1000"),
    ),
    ProductCreate(
        code="SMALL_SHORT_TERM",
        name="Small short-term loan",
        description="Synthetic demo product: small amounts over short terms.",
        currency=Currency.ZAR,
        min_amount=D("1000.00"), max_amount=D("8000.00"),
        min_term_months=3, max_term_months=6,
        annual_interest_rate=D("0.275000"),
        initiation_fee=D("165.00"), monthly_service_fee=D("60.00"),
        late_fee=D("60.00"), grace_period_days=3, early_settlement_fee_rate=D("0.000000"),
        max_dti=D("0.4000"), warn_dti=D("0.3000"),
        min_residual_amount=D("1000.00"), min_residual_rate=D("0.1000"),
    ),
]  # fmt: skip

# Applications per final status (40 in all). Repayment behaviours for the 15 loans follow.
STORIES = (
    ["loan"] * 15
    + ["approved"] * 2
    + ["declined"] * 5
    + ["recommended_approval"] * 2
    + ["recommended_rejection"] * 2
    + ["under_review"] * 3
    + ["info_requested"] * 2
    + ["pending_review"] * 4
    + ["draft"] * 3
    + ["withdrawn"] * 2
)
BEHAVIOURS = (
    ["on_time"] * 6
    + ["late"] * 3
    + ["arrears_short", "arrears_mid", "arrears_long"]
    + ["settled_early"] * 2
    + ["prepaid"]
)


@dataclass
class Credential:
    role: Role
    name: str
    email: str
    password: str


@dataclass
class Demo:
    session: Session
    clock: FixedClock
    rng: random.Random
    anchor: date
    credentials: list[Credential] = field(default_factory=list)
    staff: dict[Role, list[User]] = field(default_factory=dict)
    products: list[LoanProduct] = field(default_factory=list)
    keys: int = 0

    # --- time -------------------------------------------------------------------------

    def at(self, day: date, hour: int = 9, minute: int = 0) -> None:
        self.clock.set(datetime.combine(day, time(hour, minute), tzinfo=BUSINESS_TZ))

    def ctx(self, user: User) -> applications.Context:
        return applications.Context(user=user, clock=self.clock, ip_address=None)

    def key(self) -> str:
        self.keys += 1
        return f"seed-{self.keys:06d}"

    def pick(self, role: Role, index: int) -> User:
        people = self.staff[role]
        return people[index % len(people)]

    # --- people -----------------------------------------------------------------------

    def password(self) -> str:
        return secrets.token_urlsafe(15)

    def create_staff(self) -> None:
        meta = identity.RequestMeta(ip_address=None, user_agent="seed")
        admin_role, admin_name = STAFF[0]
        password = self.password()
        email = f"admin@{EMAIL_DOMAIN}"
        admin = identity.create_user_record(
            self.session, email=email, password=password, full_name=admin_name, role=admin_role
        )
        self.session.commit()
        self.staff[admin_role] = [admin]
        self.credentials.append(Credential(admin_role, admin_name, email, password))
        for index, (role, name) in enumerate(STAFF[1:], start=1):
            password = self.password()
            email = f"{role.value.lower().replace('_', '.')}{index}@{EMAIL_DOMAIN}"
            user = identity.create_user(
                self.session,
                UserCreate(email=email, password=password, full_name=name, role=role),
                actor=admin,
                clock=self.clock,
                meta=meta,
            )
            self.staff.setdefault(role, []).append(user)
            self.credentials.append(Credential(role, name, email, password))

    def create_products(self) -> None:
        admin = self.staff[Role.ADMIN][0]
        for data in PRODUCTS:
            self.products.append(
                products.create_product(
                    self.session, data, actor=admin, clock=self.clock, ip_address=None
                )
            )

    def create_borrower(self, index: int, *, burdened: bool) -> tuple[User, D]:
        """An applicant with a complete profile. Returns the user and monthly net income."""
        rng = self.rng
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 7) % len(LAST_NAMES)]
        name = f"{first} {last}"
        email = f"applicant{index + 1:02d}@{EMAIL_DOMAIN}"
        password = self.password()
        user = identity.register_applicant(
            self.session,
            RegisterRequest(email=email, password=password, full_name=name),
            clock=self.clock,
            meta=identity.RequestMeta(ip_address=None, user_agent="seed"),
        )
        self.credentials.append(Credential(Role.APPLICANT, name, email, password))
        city, postal = CITIES[index % len(CITIES)]
        today = business_date(self.clock)
        applicant = applicants.create_own_applicant(
            self.session,
            ApplicantCreate(
                first_name=first,
                last_name=last,
                date_of_birth=date(today.year - rng.randint(23, 58), rng.randint(1, 12), 15),
                phone=f"+27 82 555 {1000 + index:04d}",
                address_line1=f"{10 + index} Synthetic Street",
                city=city,
                postal_code=postal,
            ),
            actor=user,
            clock=self.clock,
            ip_address=None,
        )
        gross = D(rng.randrange(18_000, 68_000, 500))
        net = (gross * D("0.78")).quantize(D("1"))
        records: list[tuple[applicants.RecordKind, BaseModel]] = [
            (
                applicants.EMPLOYMENT,
                EmploymentCreate(
                    employment_type=EmploymentType.PERMANENT,
                    employer_name=EMPLOYERS[index % len(EMPLOYERS)],
                    job_title="Synthetic role",
                    start_date=today - timedelta(days=rng.randint(400, 4000)),
                    is_current=True,
                ),
            ),
            (
                applicants.INCOME,
                IncomeCreate(
                    source_type=IncomeSourceType.SALARY,
                    frequency=IncomeFrequency.MONTHLY,
                    gross_amount=gross,
                    net_amount=net,
                ),
            ),
            (
                applicants.EXPENSE,
                ExpenseCreate(
                    category=ExpenseCategory.HOUSING,
                    monthly_amount=(net * D("0.25")).quantize(D(1)),
                ),
            ),
            (
                applicants.EXPENSE,
                ExpenseCreate(
                    category=ExpenseCategory.FOOD, monthly_amount=(net * D("0.10")).quantize(D(1))
                ),
            ),
            (
                applicants.EXPENSE,
                ExpenseCreate(
                    category=ExpenseCategory.TRANSPORT,
                    monthly_amount=(net * D("0.07")).quantize(D(1)),
                ),
            ),
        ]
        if burdened:  # existing debt heavy enough to fail affordability
            records.append(
                (
                    applicants.COMMITMENT,
                    CommitmentCreate(
                        commitment_type=CommitmentType.VEHICLE_FINANCE,
                        lender_name="Example Motor Finance",
                        monthly_instalment=(net * D("0.60")).quantize(D(1)),
                        outstanding_balance=D("240000.00"),
                    ),
                )
            )
        elif rng.random() < 0.5:
            records.append(
                (
                    applicants.COMMITMENT,
                    CommitmentCreate(
                        commitment_type=CommitmentType.CREDIT_CARD,
                        lender_name="Demo Card Issuer",
                        monthly_instalment=D(rng.randrange(300, 1500, 50)),
                        outstanding_balance=D(rng.randrange(5000, 30000, 500)),
                        credit_limit=D("40000.00"),
                    ),
                )
            )
        for kind, data in records:
            applicants.create_record(
                self.session,
                kind,
                applicant,
                data,
                actor=user,
                clock=self.clock,
                ip_address=None,
            )
        return user, net

    # --- applications -----------------------------------------------------------------

    def request(self, product: LoanProduct, net: D) -> tuple[D, int]:
        """An amount and term within the product whose instalment fits the income."""
        rng = self.rng
        terms = [t for t in (3, 6, 12, 18, 24, 36, 48, 60) if
                 product.min_term_months <= t <= product.max_term_months]  # fmt: skip
        term = rng.choice(terms)
        budget = net * D("0.18") * term  # roughly what an affordable loan could repay
        ceiling = min(product.max_amount, budget * D("0.75"), net * 2)
        step = D(500) if product.max_amount > 10_000 else D(100)
        top = max(product.min_amount, (ceiling / step).quantize(D(1)) * step)
        amount = D(rng.randrange(int(product.min_amount), int(top) + 1, int(step)))
        return max(amount, product.min_amount), term

    def open_application(self, user: User, product: LoanProduct, net: D) -> LoanApplication:
        amount, term = self.request(product, net)
        return applications.create_application(
            self.session,
            ApplicationCreate(
                loan_product_id=product.id,
                requested_amount=amount,
                requested_term_months=term,
                purpose=self.rng.choice(
                    [
                        LoanPurpose.DEBT_CONSOLIDATION,
                        LoanPurpose.HOME_IMPROVEMENT,
                        LoanPurpose.EDUCATION,
                        LoanPurpose.PERSONAL,
                        LoanPurpose.VEHICLE,
                    ]
                ),
            ),
            self.ctx(user),
        )

    def latest_assessment(self, application_id: uuid.UUID) -> AffordabilityAssessment:
        return affordability.list_assessments(self.session, application_id)[0]

    def recommend(self, application: LoanApplication, officer: User, approve: bool) -> None:
        assessment = self.latest_assessment(application.id)
        rationale = (
            "Residual income and debt-to-income are within policy; employment is stable."
            if approve
            else "Existing debt leaves too little residual income for this instalment."
        )
        applications.recommend(
            self.session,
            application.id,
            RecommendationCreate(
                recommendation=Recommendation.APPROVE if approve else Recommendation.REJECT,
                rationale=rationale,
                affordability_assessment_id=assessment.id,
            ),
            self.ctx(officer),
        )

    def decide(self, application: LoanApplication, decider: User, approve: bool) -> None:
        assessment = self.latest_assessment(application.id)
        applications.decide(
            self.session,
            application.id,
            DecisionCreate(
                decision=DecisionOutcome.APPROVED if approve else DecisionOutcome.DECLINED,
                reason_codes=(
                    ["MEETS_CRITERIA", "STABLE_EMPLOYMENT"]
                    if approve
                    else ["AFFORDABILITY_SHORTFALL", "HIGH_DTI"]
                ),
                explanation=(
                    "Affordability supports the loan; the recommendation is accepted."
                    if approve
                    else "Residual income is negative once existing debt is included."
                ),
                affordability_assessment_id=assessment.id,
                model_output_acknowledged=True,
            ),
            self.ctx(decider),
        )

    def run_story(self, index: int, story: str, start: date) -> LoanAccount | None:
        """Take one applicant's application as far as ``story`` says, starting on ``start``."""
        burdened = story in {"declined", "recommended_rejection"}
        officer = self.pick(Role.LOAN_OFFICER, index)
        decider = self.pick(Role.CREDIT_ANALYST, index) if index % 4 else self.staff[Role.ADMIN][0]
        self.at(start - timedelta(days=3))
        user, net = self.create_borrower(index, burdened=burdened)
        product = self.products[0 if index % 5 else (1 if index % 10 else 2)]
        if product.code == "DEBT_CONSOLIDATION" and net < 20_000:
            product = self.products[0]

        self.at(start, 10)
        application = self.open_application(user, product, net)
        app_id = application.id
        if story == "draft":
            return None
        if story == "withdrawn":
            self.at(start + timedelta(days=1), 11)
            applications.withdraw(self.session, app_id, self.ctx(user), reason="Changed my mind")
            return None
        self.at(start + timedelta(days=1), 9)
        applications.submit(self.session, app_id, self.ctx(user))
        if story == "pending_review":
            return None
        self.at(start + timedelta(days=2), 9, 30)
        applications.start_review(self.session, app_id, self.ctx(officer))
        if story == "under_review":
            return None
        if story == "info_requested":
            applications.request_information(
                self.session,
                app_id,
                "Please upload your three most recent payslips.",
                self.ctx(officer),
            )
            return None
        approve = story not in {"declined", "recommended_rejection"}
        self.at(start + timedelta(days=3), 14)
        self.recommend(application, officer, approve)
        if story.startswith("recommended_"):
            return None
        self.at(start + timedelta(days=4), 11)
        self.decide(application, decider, approve)
        if story in {"declined", "approved"}:
            return None
        self.at(start + timedelta(days=5), 10)
        account, _ = applications.disburse(self.session, app_id, self.ctx(officer))
        return account

    # --- repayments -------------------------------------------------------------------

    def pay(self, account: LoanAccount, payer: User, amount: D, day: date) -> None:
        self.at(day, 12)
        loans.record_repayment(
            self.session,
            account.id,
            amount=amount,
            value_date=day,
            idempotency_key=self.key(),
            user=payer,
            clock=self.clock,
            ip_address=None,
        )

    def due_amount(self, account: LoanAccount, day: date) -> D:
        book = loans.load_book(self.session, account)
        late = arrears.late_fees_due(
            book.instalment_states(),
            book.charge_states(),
            day,
            late_fee=account.late_fee,
            grace_period_days=account.grace_period_days,
        )
        due = allocation.amount_due(day, book.instalment_states(), book.charge_states())
        return due + sum((fee.amount for fee in late), D("0.00"))

    def prepayment(self, account: LoanAccount, day: date) -> D:
        """A lump sum of up to 5,000.00, never more than a quarter of the principal not yet due."""
        book = loans.load_book(self.session, account)
        future = sum(
            (s.principal_outstanding for s in book.instalment_states() if s.due_date > day),
            D("0.00"),
        )
        return min(D("5000.00"), (future / 4).quantize(D("1")))

    def simulate(self, account: LoanAccount, behaviour: str, owner: User, index: int) -> None:
        officer = self.pick(Role.LOAN_OFFICER, index)
        book = loans.load_book(self.session, account)
        due_dates = [row.due_date for _, row in sorted(book.instalments.items())]
        past = [d for d in due_dates if d <= self.anchor]
        stop_after = {
            "arrears_short": len(past) - 1,  # last instalment missed: a few days to weeks
            "arrears_mid": len(past) - 2,
            "arrears_long": len(past) - 4,
        }.get(behaviour, len(past))
        settle_after = max(2, len(past) // 2) if behaviour == "settled_early" else None
        for number, due in enumerate(past, start=1):
            if number > stop_after:
                break
            payer = owner if number % 2 else officer
            days_late = 0
            if behaviour == "late" and number % 3 == 0:
                days_late = self.rng.randint(3, 20)
            day = min(due + timedelta(days=days_late), self.anchor)
            amount = self.due_amount(account, day)
            if behaviour == "prepaid" and number == 2:
                amount += self.prepayment(account, day)
            if amount > 0:
                self.pay(account, payer, amount, day)
            if settle_after is not None and number == settle_after:
                settle_on = min(due + timedelta(days=12), self.anchor)
                self.at(settle_on, 15)
                _, quote = loans.settlement_quote(
                    self.session, account.id, owner, as_of=settle_on, clock=self.clock
                )
                loans.settle(
                    self.session,
                    account.id,
                    amount=quote.total,
                    idempotency_key=self.key(),
                    user=owner,
                    clock=self.clock,
                    ip_address=None,
                )
                return


def _reset(session: Session) -> None:
    tables = [t.name for t in reversed(Base.metadata.sorted_tables)]
    session.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    for sequence in ("loan_application_reference_seq", "loan_account_number_seq"):
        session.execute(text(f"ALTER SEQUENCE {sequence} RESTART WITH 1"))
    session.commit()


def _write_credentials(path: Path, demo: Demo, seed: int) -> None:
    lines = [
        "# Credify demo accounts — SYNTHETIC, LOCAL USE ONLY. Never commit this file.",
        f"# Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC with --seed {seed} "
        f"--as-of {demo.anchor}. Re-running the seed replaces these passwords.",
        "",
        f"{'role':<16} {'name':<28} {'email':<40} password",
    ]
    lines += [
        f"{c.role.value:<16} {c.name:<28} {c.email:<40} {c.password}" for c in demo.credentials
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def seed(session: Session, *, rng_seed: int, anchor: date) -> Demo:
    rng = random.Random(rng_seed)  # noqa: S311 — synthetic demo data, not security
    clock = FixedClock(datetime.combine(anchor, time(9), tzinfo=BUSINESS_TZ))
    demo = Demo(session=session, clock=clock, rng=rng, anchor=anchor)
    demo.at(anchor - timedelta(days=400), 8)
    demo.create_staff()
    demo.create_products()

    stories = list(STORIES)
    behaviours = iter(BEHAVIOURS)
    for index, story in enumerate(stories):
        if story == "loan":
            # Disbursed between ~13 and ~2 months ago, so histories have some length.
            start = anchor - timedelta(days=400 - index * 22)
        elif story in {"approved", "declined"}:
            start = anchor - timedelta(days=rng.randint(20, 120))
        else:
            start = anchor - timedelta(days=rng.randint(6, 30))
        account = demo.run_story(index, story, start)
        if account is not None:
            owner = session.get(User, _owner_id(session, account))
            assert owner is not None  # noqa: S101 — the borrower was just created
            demo.simulate(account, next(behaviours), owner, index)

    demo.at(anchor, 23, 30)
    loans.run_arrears(session, as_of=anchor, now=demo.clock.now())
    return demo


def _owner_id(session: Session, account: LoanAccount) -> uuid.UUID | None:
    return session.scalar(select(Applicant.user_id).where(Applicant.id == account.applicant_id))


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic synthetic demo data.")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="The demo's 'today' (default: today's business date). Histories end here.",
    )
    parser.add_argument("--reset", action="store_true", help="Empty every table first.")
    parser.add_argument("--credentials-file", type=Path, default=DEFAULT_CREDENTIALS)
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.environment == "production":
        out("Refusing to seed demo data into a production environment.")
        return 2
    anchor: date = args.as_of or business_date(SystemClock())
    engine = build_engine(settings.database_url_str)
    try:
        with build_session_factory(engine)() as session:
            if args.reset:
                _reset(session)
            elif session.scalar(select(func.count()).select_from(User)):
                out("The database already has users. Re-run with --reset to replace them.")
                return 2
            demo = seed(session, rng_seed=args.seed, anchor=anchor)
            counts = {
                "users": session.scalar(select(func.count()).select_from(User)),
                "loan accounts": session.scalar(select(func.count()).select_from(LoanAccount)),
            }
    finally:
        engine.dispose()
    _write_credentials(args.credentials_file, demo, args.seed)
    out(f"Seeded demo data as of {anchor}: {counts['users']} users, "
        f"{counts['loan accounts']} loan accounts.")  # fmt: skip
    out(f"Demo credentials written to {args.credentials_file.resolve()} (git-ignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
