"""The controlled list of decision reason codes (DATABASE.md §3.5).

Every final decision carries at least one code from this list, and each code says which
outcome it can support, so an approval can never cite "high debt-to-income" as its reason.
The applicant-facing text is plain language; the reviewer's own explanation stays internal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Recommendation(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class DecisionOutcome(StrEnum):
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class ReasonCode(StrEnum):
    # Supporting an approval
    MEETS_CRITERIA = "MEETS_CRITERIA"
    AFFORDABLE_WITH_BUFFER = "AFFORDABLE_WITH_BUFFER"
    STABLE_EMPLOYMENT = "STABLE_EMPLOYMENT"
    VERIFIED_INCOME = "VERIFIED_INCOME"
    COUNTER_OFFER = "COUNTER_OFFER"
    # Supporting a decline
    AFFORDABILITY_SHORTFALL = "AFFORDABILITY_SHORTFALL"
    HIGH_DTI = "HIGH_DTI"
    INSUFFICIENT_BUFFER = "INSUFFICIENT_BUFFER"
    INSUFFICIENT_INCOME_EVIDENCE = "INSUFFICIENT_INCOME_EVIDENCE"
    UNSTABLE_EMPLOYMENT = "UNSTABLE_EMPLOYMENT"
    ADVERSE_REPAYMENT_HISTORY = "ADVERSE_REPAYMENT_HISTORY"
    INCOMPLETE_INFORMATION = "INCOMPLETE_INFORMATION"
    POLICY_EXCLUSION = "POLICY_EXCLUSION"


@dataclass(frozen=True, slots=True)
class ReasonCodeInfo:
    code: ReasonCode
    outcome: DecisionOutcome
    label: str
    applicant_text: str


_A = DecisionOutcome.APPROVED
_D = DecisionOutcome.DECLINED

CATALOGUE: dict[ReasonCode, ReasonCodeInfo] = {
    info.code: info
    for info in (
        ReasonCodeInfo(
            ReasonCode.MEETS_CRITERIA,
            _A,
            "Meets lending criteria",
            "Your application met the lending criteria for this product.",
        ),
        ReasonCodeInfo(
            ReasonCode.AFFORDABLE_WITH_BUFFER,
            _A,
            "Affordable with a healthy buffer",
            "The repayments leave a comfortable margin in your monthly budget.",
        ),
        ReasonCodeInfo(
            ReasonCode.STABLE_EMPLOYMENT,
            _A,
            "Stable employment",
            "Your employment history supports regular repayments.",
        ),
        ReasonCodeInfo(
            ReasonCode.VERIFIED_INCOME,
            _A,
            "Income verified",
            "The income you declared was supported by the evidence provided.",
        ),
        ReasonCodeInfo(
            ReasonCode.COUNTER_OFFER,
            _A,
            "Approved on different terms",
            "We approved a different amount or term from the one you asked for.",
        ),
        ReasonCodeInfo(
            ReasonCode.AFFORDABILITY_SHORTFALL,
            _D,
            "Affordability shortfall",
            "The repayments would exceed what your monthly budget can support.",
        ),
        ReasonCodeInfo(
            ReasonCode.HIGH_DTI,
            _D,
            "Debt-to-income too high",
            "Your total monthly debt repayments would be too high relative to your income.",
        ),
        ReasonCodeInfo(
            ReasonCode.INSUFFICIENT_BUFFER,
            _D,
            "Insufficient residual buffer",
            "Too little would remain each month after repayments and living expenses.",
        ),
        ReasonCodeInfo(
            ReasonCode.INSUFFICIENT_INCOME_EVIDENCE,
            _D,
            "Insufficient income evidence",
            "We could not confirm your income from the information provided.",
        ),
        ReasonCodeInfo(
            ReasonCode.UNSTABLE_EMPLOYMENT,
            _D,
            "Employment not stable enough",
            "Your current employment situation does not yet support this loan.",
        ),
        ReasonCodeInfo(
            ReasonCode.ADVERSE_REPAYMENT_HISTORY,
            _D,
            "Adverse repayment history",
            "Your existing credit commitments indicate repayment difficulties.",
        ),
        ReasonCodeInfo(
            ReasonCode.INCOMPLETE_INFORMATION,
            _D,
            "Incomplete information",
            "We did not receive enough information to approve the application.",
        ),
        ReasonCodeInfo(
            ReasonCode.POLICY_EXCLUSION,
            _D,
            "Outside lending policy",
            "The application falls outside this product's lending policy.",
        ),
    )
}


def problems_for(outcome: DecisionOutcome, codes: tuple[str, ...]) -> list[str]:
    """Codes that are unknown, repeated or do not support ``outcome``."""
    problems = []
    if len(set(codes)) != len(codes):
        problems.append("reason codes must not repeat")
    for code in codes:
        try:
            info = CATALOGUE[ReasonCode(code)]
        except ValueError:
            problems.append(f"{code} is not a recognised reason code")
            continue
        if info.outcome is not outcome:
            problems.append(f"{code} cannot support a decision to {outcome.value.lower()[:-1]}")
    return problems
