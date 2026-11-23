"""Illustrative minimum living-expense norm (FINANCIAL_CALCULATIONS.md §4.1).

This is configuration, not a regulatory table. The applied norm is written into every
assessment's input snapshot, so changing it never alters how an old assessment reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.core.money import FINANCIAL_CONTEXT, quantize_money


@dataclass(frozen=True, slots=True)
class NormBand:
    """Applies to gross monthly income above ``floor``: ``fixed + rate × (G − floor)``."""

    floor: Decimal
    fixed: Decimal
    rate: Decimal


@dataclass(frozen=True, slots=True)
class ExpenseNorm:
    name: str
    bands: tuple[NormBand, ...]

    def __post_init__(self) -> None:
        if not self.bands or self.bands[0].floor != 0:
            raise ValueError("the first expense-norm band must start at 0")
        floors = [band.floor for band in self.bands]
        if floors != sorted(set(floors)):
            raise ValueError("expense-norm band floors must be strictly increasing")
        for band in self.bands:
            if band.fixed < 0 or not Decimal(0) <= band.rate <= 1:
                raise ValueError("expense-norm amounts must be non-negative and rates 0–1")

    def band_for(self, gross_monthly_income: Decimal) -> NormBand:
        # Band 1 covers 0.00 up to and including the next floor, so a band applies to
        # incomes strictly above its floor (band 1 also takes exactly 0.00).
        chosen = self.bands[0]
        for band in self.bands[1:]:
            if gross_monthly_income > band.floor:
                chosen = band
        return chosen

    def minimum_for(self, gross_monthly_income: Decimal) -> Decimal:
        band = self.band_for(gross_monthly_income)
        with localcontext(FINANCIAL_CONTEXT):
            return quantize_money(band.fixed + band.rate * (gross_monthly_income - band.floor))

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "bands": [
                {"floor": band.floor, "fixed": band.fixed, "rate": band.rate} for band in self.bands
            ],
        }


DEFAULT_EXPENSE_NORM = ExpenseNorm(
    name="illustrative-2026",
    bands=(
        NormBand(floor=Decimal("0.00"), fixed=Decimal("2000.00"), rate=Decimal("0.00")),
        NormBand(floor=Decimal("6000.00"), fixed=Decimal("2000.00"), rate=Decimal("0.10")),
        NormBand(floor=Decimal("15000.00"), fixed=Decimal("2900.00"), rate=Decimal("0.08")),
        NormBand(floor=Decimal("30000.00"), fixed=Decimal("4100.00"), rate=Decimal("0.06")),
    ),
)
