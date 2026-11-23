"""Applicant eligibility rules.

Date of birth is used for exactly one thing: confirming the applicant is an adult. It is
never a model feature (see docs/ML_STRATEGY.md).
"""

from __future__ import annotations

from datetime import date

MINIMUM_AGE = 18
MAXIMUM_AGE = 120


def age_on(date_of_birth: date, on: date) -> int:
    """Completed years on ``on``. A 29 February birthday turns over on 1 March in other years."""
    had_birthday = (on.month, on.day) >= (date_of_birth.month, date_of_birth.day)
    return on.year - date_of_birth.year - (0 if had_birthday else 1)


def date_of_birth_problems(date_of_birth: date, today: date) -> list[str]:
    if date_of_birth > today:
        return ["date_of_birth cannot be in the future"]
    age = age_on(date_of_birth, today)
    if age < MINIMUM_AGE:
        return [f"applicants must be at least {MINIMUM_AGE} years old"]
    if age > MAXIMUM_AGE:
        return ["date_of_birth is not plausible"]
    return []
