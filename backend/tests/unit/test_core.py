from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.clock import FixedClock
from app.core.db import json_dumps
from app.core.ids import uuid7
from app.core.logging import REDACTED, redact_sensitive
from app.modules.audit.service import diff
from tests.conftest import make_settings


class TestSettings:
    def test_short_jwt_secret_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 32 characters"):
            make_settings(jwt_secret="too-short")

    def test_placeholder_jwt_secret_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="placeholder"):
            make_settings(jwt_secret="<generate-a-random-48-byte-value>-change-me-please")

    def test_invalid_database_url_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_settings(database_url="mysql://user@host/db")

    def test_production_requires_secure_cookies(self) -> None:
        with pytest.raises(ValidationError, match="cookie_secure"):
            make_settings(environment="production", cookie_secure=False)

    def test_cors_origins_are_split(self) -> None:
        settings = make_settings(cors_origins="http://a.test, http://b.test")
        assert settings.cors_origin_list == ["http://a.test", "http://b.test"]

    def test_secret_is_not_exposed_in_repr(self) -> None:
        settings = make_settings()
        assert settings.jwt_secret.get_secret_value() not in repr(settings)


class TestUuid7:
    def test_version_and_variant(self) -> None:
        value = uuid7()
        assert value.version == 7
        assert value.variant == uuid.RFC_4122

    def test_time_ordered(self) -> None:
        first = uuid7()
        time.sleep(0.002)
        second = uuid7()
        assert first < second

    def test_unique(self) -> None:
        assert len({uuid7() for _ in range(5_000)}) == 5_000


def test_fixed_clock_requires_aware_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 1, 1))  # noqa: DTZ001 — deliberately naive


def test_json_dumps_never_emits_floats_for_money() -> None:
    payload = {"amount": Decimal("1234.50"), "id": uuid.UUID(int=1), "on": date(2026, 1, 31)}
    encoded = json_dumps(payload)
    assert '"amount": "1234.50"' in encoded
    assert '"on": "2026-01-31"' in encoded


def test_log_redaction_hides_secrets() -> None:
    event = redact_sensitive(None, "info", {"password": "x", "Authorization": "y", "user": "z"})
    assert event["password"] == REDACTED
    assert event["Authorization"] == REDACTED
    assert event["user"] == "z"


def test_audit_diff_reports_only_changed_fields() -> None:
    assert diff({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {
        "b": {"from": 2, "to": 3},
        "c": {"from": None, "to": 4},
    }


def test_fixed_clock_normalises_to_utc() -> None:
    from datetime import timedelta, timezone

    sast = timezone(timedelta(hours=2))
    clock = FixedClock(datetime(2026, 11, 18, 11, 0, tzinfo=sast))
    assert clock.now() == datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
    assert clock.now().tzinfo == UTC
