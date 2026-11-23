"""Pure validation rules for products, applicant profiles, money and documents."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.clock import FixedClock, business_date
from app.core.money import quantize_money
from app.modules.applicants.domain.eligibility import age_on, date_of_birth_problems
from app.modules.applicants.schemas import EmploymentCreate, IncomeCreate
from app.modules.applications.schemas import DocumentCreate
from app.modules.products.schemas import ProductCreate
from tests.conftest import product_payload


@pytest.mark.parametrize(
    ("born", "on", "age"),
    [
        (date(2008, 11, 23), date(2026, 11, 23), 18),  # birthday today
        (date(2008, 11, 24), date(2026, 11, 23), 17),  # birthday tomorrow
        (date(2000, 2, 29), date(2018, 2, 28), 17),  # leap-day birthday, non-leap year
        (date(2000, 2, 29), date(2018, 3, 1), 18),
        (date(2000, 2, 29), date(2024, 2, 29), 24),
    ],
)
def test_age_on(born: date, on: date, age: int) -> None:
    assert age_on(born, on) == age


def test_date_of_birth_rules() -> None:
    today = date(2026, 11, 23)
    assert date_of_birth_problems(date(1990, 1, 1), today) == []
    assert "future" in date_of_birth_problems(date(2027, 1, 1), today)[0]
    assert "18" in date_of_birth_problems(date(2010, 1, 1), today)[0]
    assert "plausible" in date_of_birth_problems(date(1890, 1, 1), today)[0]


def test_business_date_uses_south_african_time() -> None:
    # 23:30 UTC on 30 Nov is already 1 Dec in Johannesburg (UTC+2).
    assert business_date(FixedClock(datetime(2026, 11, 30, 23, 30, tzinfo=UTC))) == date(
        2026, 12, 1
    )


def test_quantize_money_rounds_half_up() -> None:
    assert quantize_money(Decimal("1.005")) == Decimal("1.01")
    assert quantize_money(Decimal("-1.005")) == Decimal("-1.01")
    assert quantize_money(Decimal("2.004")) == Decimal("2.00")


def _income(**overrides: Any) -> dict[str, Any]:
    return {
        "source_type": "SALARY",
        "frequency": "MONTHLY",
        "gross_amount": "20000.00",
        "net_amount": "15000.00",
        **overrides,
    }


def test_money_must_be_sent_as_a_string_or_integer() -> None:
    assert IncomeCreate.model_validate(_income()).gross_amount == Decimal("20000.00")
    assert IncomeCreate.model_validate(_income(gross_amount=20000)).gross_amount == Decimal(20000)
    with pytest.raises(ValidationError, match="not JSON numbers"):
        IncomeCreate.model_validate(_income(gross_amount=20000.5))


@pytest.mark.parametrize(
    "overrides",
    [
        {"net_amount": "25000.00"},  # net above gross
        {"gross_amount": "0.00"},
        {"gross_amount": "100.001"},  # sub-cent precision
        {"currency": "BTC"},
        {"frequency": "DAILY"},
    ],
)
def test_invalid_income(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        IncomeCreate.model_validate(_income(**overrides))


def _job(**overrides: Any) -> dict[str, Any]:
    return {
        "employment_type": "PERMANENT",
        "employer_name": "Example Holdings",
        "start_date": "2020-01-01",
        "is_current": True,
        **overrides,
    }


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"employer_name": None}, "required"),
        ({"start_date": None}, "required"),
        ({"end_date": "2024-01-01"}, "current position"),
        ({"is_current": False}, "past position"),
        ({"is_current": False, "end_date": "2019-01-01"}, "before start_date"),
    ],
)
def test_invalid_employment(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        EmploymentCreate.model_validate(_job(**overrides))


def test_unemployed_needs_no_employer() -> None:
    record = EmploymentCreate.model_validate({"employment_type": "UNEMPLOYED", "is_current": True})
    assert record.employer_name is None


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"min_amount": "200000.00"}, "min_amount must not exceed max_amount"),
        ({"min_term_months": 72}, "min_term_months must not exceed max_term_months"),
        ({"warn_dti": "0.5000"}, "warn_dti must not exceed max_dti"),
        ({"warn_dti": "0.0000"}, "warn_dti must be greater than zero"),
        ({"annual_interest_rate": "1.000000"}, "less than 1"),
        ({"code": "lowercase"}, "pattern"),
        ({"annual_interest_rate": 0.215}, "not JSON numbers"),
    ],
)
def test_invalid_products(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        ProductCreate.model_validate(product_payload(**overrides))


def _document(**overrides: Any) -> dict[str, Any]:
    return {
        "document_type": "PAYSLIP",
        "file_name": "payslip-october.pdf",
        "content_type": "application/pdf",
        "size_bytes": 120_000,
        **overrides,
    }


def test_document_metadata_is_normalised() -> None:
    document = DocumentCreate.model_validate(_document(content_type=" Application/PDF "))
    assert document.content_type == "application/pdf"


@pytest.mark.parametrize(
    "overrides",
    [
        {"file_name": "../../etc/passwd"},
        {"file_name": "C:\\temp\\x.pdf"},
        {"file_name": ".hidden"},
        {"file_name": "bad\x00name.pdf"},
        {"content_type": "application/x-msdownload"},
        {"size_bytes": 0},
        {"size_bytes": 50 * 1024 * 1024},
    ],
)
def test_invalid_document_metadata(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DocumentCreate.model_validate(_document(**overrides))
