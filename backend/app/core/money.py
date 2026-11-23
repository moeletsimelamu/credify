"""Money primitives shared by every module.

Amounts are ``Decimal`` end to end: ``NUMERIC`` in PostgreSQL, ``Decimal`` in Python and a
*string* in JSON. JSON numbers are rejected for money, rates and ratios so a client can
never send a value that has already been through binary floating point.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Context, Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

CENT = Decimal("0.01")
RATIO_PLACES = Decimal("0.0001")

# The arithmetic context every financial calculation runs in (FINANCIAL_CALCULATIONS.md §1):
# 28 significant digits, half-up. Engines enter it with ``decimal.localcontext``, so results
# never depend on whatever context the calling thread happens to have.
FINANCIAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)


class CurrencyMismatchError(ValueError):
    """Amounts in different currencies were combined. Credify never converts currencies."""


class Currency(StrEnum):
    """ISO-4217 codes the platform accepts. ZAR is the default (approved decision D1)."""

    ZAR = "ZAR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


DEFAULT_CURRENCY = Currency.ZAR


def quantize_money(value: Decimal) -> Decimal:
    """Round to cents, half away from zero (FINANCIAL_CALCULATIONS.md §1)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def quantize_ratio(value: Decimal) -> Decimal:
    """Round a ratio to four decimal places, half up (FINANCIAL_CALCULATIONS.md §1)."""
    return value.quantize(RATIO_PLACES, rounding=ROUND_HALF_UP)


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError('send decimal values as strings, e.g. "1500.00", not JSON numbers')
    return value


_NoFloat = BeforeValidator(_reject_float)

# NUMERIC(18,2): amounts such as 12500.00.
MoneyAmount = Annotated[Decimal, _NoFloat, Field(max_digits=18, decimal_places=2)]
PositiveMoney = Annotated[Decimal, _NoFloat, Field(gt=0, max_digits=18, decimal_places=2)]
NonNegativeMoney = Annotated[Decimal, _NoFloat, Field(ge=0, max_digits=18, decimal_places=2)]

# NUMERIC(9,6) annual rates as fractions (0.215000 = 21.5 % p.a.), always below 100 %.
Rate = Annotated[Decimal, _NoFloat, Field(ge=0, lt=1, max_digits=9, decimal_places=6)]

# NUMERIC(10,4) ratios (0.3500 = 35 %).
Ratio = Annotated[Decimal, _NoFloat, Field(ge=0, le=1, max_digits=10, decimal_places=4)]
