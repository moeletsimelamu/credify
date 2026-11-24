"""Daily arrears job: late fees, instalment statuses and each loan's cached position.

    python -m scripts.run_arrears_job                  # as of today's business date (SAST)
    python -m scripts.run_arrears_job --as-of 2027-03-01   # simulate a later date

Idempotent: running it twice for the same date changes nothing the second time. Exits with
status 1 if any account's cached figures disagree with its transaction log.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import app.db_models  # noqa: F401 — register every model so foreign keys resolve
from app.core.clock import SystemClock, business_date
from app.core.config import get_settings
from app.core.db import build_engine, build_session_factory
from app.modules.loans import service as loans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="Evaluate as of this date (YYYY-MM-DD). Defaults to today's business date.",
    )
    args = parser.parse_args(argv)
    clock = SystemClock()
    as_of: date = args.as_of or business_date(clock)

    engine = build_engine(get_settings().database_url_str)
    try:
        with build_session_factory(engine)() as session:
            report = loans.run_arrears(session, as_of=as_of, now=clock.now())
    finally:
        engine.dispose()

    print(f"Arrears job as of {report.as_of}")
    print(f"  accounts evaluated: {report.evaluated}")
    print(f"  late fees charged:  {report.late_fees_charged}")
    print(f"  status changes:     {report.status_changes}")
    print(f"  in arrears:         {report.in_arrears}")
    if report.skipped:
        print(f"  skipped (already evaluated later): {', '.join(report.skipped)}")
    for account, problems in report.discrepancies.items():
        print(f"  RECONCILIATION {account}: {'; '.join(problems)}")
    return 1 if report.discrepancies else 0


if __name__ == "__main__":
    sys.exit(main())
