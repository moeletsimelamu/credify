"""The demo seed runs through the real services and leaves a consistent, varied book."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.applications.domain.state_machine import ApplicationStatus
from app.modules.applications.models import LoanApplication
from app.modules.identity.models import Role, User
from app.modules.loans import service as loans
from app.modules.loans.engine.ledger import REPAYABLE, LoanStatus
from app.modules.loans.models import LoanAccount, RepaymentSchedule
from scripts import run_arrears_job, seed_demo_data
from tests.conftest import BACKEND_DIR, TEST_DATABASE_URL, TEST_JWT_SECRET

pytestmark = pytest.mark.integration


def test_seed_covers_every_status_and_reconciles(db_session: Session, tmp_path: Path) -> None:
    demo = seed_demo_data.seed(db_session, rng_seed=7, anchor=date(2026, 11, 24))

    statuses = set(db_session.scalars(select(LoanApplication.status)).all())
    expected = set(ApplicationStatus) - {ApplicationStatus.SUBMITTED}  # SUBMITTED is transient
    assert statuses == expected
    rows = db_session.execute(select(User.role, func.count()).group_by(User.role)).all()
    roles = {role: count for role, count in rows}
    assert roles == {
        Role.ADMIN: 1,
        Role.LOAN_OFFICER: 2,
        Role.CREDIT_ANALYST: 2,
        Role.APPLICANT: 40,
    }

    accounts = db_session.scalars(select(LoanAccount)).all()
    assert len(accounts) == 15
    loan_statuses = {a.status for a in accounts}
    assert {LoanStatus.ACTIVE, LoanStatus.IN_ARREARS, LoanStatus.SETTLED} <= loan_statuses
    assert all(a.position_as_of == date(2026, 11, 24) for a in accounts if a.status in REPAYABLE)
    assert db_session.scalar(
        select(func.count()).select_from(RepaymentSchedule).where(RepaymentSchedule.version > 1)
    )
    for account in accounts:
        assert loans.reconcile(db_session, account) == [], account.account_number

    assert len(demo.credentials) == 45
    assert len({c.email for c in demo.credentials}) == 45
    assert all(c.email.endswith("@credify-demo.example") for c in demo.credentials)
    target = tmp_path / "creds.txt"
    seed_demo_data._write_credentials(target, demo, 7)
    assert "SYNTHETIC, LOCAL USE ONLY" in target.read_text(encoding="utf-8")


def test_arrears_cli_reports_and_is_idempotent(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> None:
    seed_demo_data.seed(db_session, rng_seed=3, anchor=date(2026, 11, 24))
    db_session.commit()
    assert TEST_DATABASE_URL
    monkeypatch.setenv("CREDIFY_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("CREDIFY_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("CREDIFY_ENVIRONMENT", "test")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
    # First as a fresh process, exactly as an operator or scheduler would run it.
    first = subprocess.run(
        [sys.executable, "-m", "scripts.run_arrears_job", "--as-of", "2026-12-31"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert first.returncode == 0, first.stderr
    assert "Arrears job as of 2026-12-31" in first.stdout
    assert run_arrears_job.main(["--as-of", "2026-12-31"]) == 0
    second = capsys.readouterr().out
    assert "late fees charged:  0" in second
    assert "status changes:     0" in second
    # An earlier date than the accounts' current position is skipped, never rewound.
    assert run_arrears_job.main(["--as-of", "2026-12-01"]) == 0
    assert "skipped" in capsys.readouterr().out
