"""A **simulated** credit bureau (approved Phase 7 decision).

Credify never contacts a real credit bureau. When an application is submitted, the system
"pulls" a report from this simulator instead: two credit-history figures the model uses and
the applicant cannot type in themselves.

The report is a pure, deterministic function of the applicant's synthetic reference, so the
same applicant always gets the same history and tests can predict it. The distributions
match the training data's (``ml/credify_ml/generate.py``) and use integer arithmetic only.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

SIMULATOR_VERSION = "bureau-sim-1.0.0"
PROVIDER = "SIMULATED"

_SCALE = 10_000
_P_RECENT_MISSES = 1_800  # 18 % have missed payments in the last 12 months
_P_OLD_DELINQUENCY = 1_800  # of the rest, 18 % had a delinquency more than 12 months ago
# 1 + Poisson(0.8) missed payments, as a cumulative distribution in parts per 10,000.
_EXTRA_MISSES_CDF = (4_493, 8_088, 9_526, 9_909, 9_986, _SCALE)


@dataclass(frozen=True, slots=True)
class BureauReport:
    missed_payments_12m: int
    months_since_delinquency: int | None  # None: no delinquency on record
    simulator_version: str = SIMULATOR_VERSION
    provider: str = PROVIDER


def _rng(synthetic_reference: str) -> random.Random:
    digest = hashlib.sha256(f"{SIMULATOR_VERSION}:{synthetic_reference}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))  # noqa: S311 — simulation, not security


def simulate(synthetic_reference: str) -> BureauReport:
    if not synthetic_reference:
        raise ValueError("a synthetic reference is required")
    rng = _rng(synthetic_reference)
    if rng.randrange(_SCALE) < _P_RECENT_MISSES:
        draw = rng.randrange(_SCALE)
        extra = next(k for k, edge in enumerate(_EXTRA_MISSES_CDF) if draw < edge)
        return BureauReport(
            missed_payments_12m=1 + extra, months_since_delinquency=rng.randrange(12)
        )
    if rng.randrange(_SCALE) < _P_OLD_DELINQUENCY:
        return BureauReport(missed_payments_12m=0, months_since_delinquency=rng.randrange(12, 97))
    return BureauReport(missed_payments_12m=0, months_since_delinquency=None)
