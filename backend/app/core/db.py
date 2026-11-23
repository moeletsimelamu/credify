"""Database engine, session factory and declarative base."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, Enum, MetaData, Numeric, create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.core.ids import uuid7

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Timestamps(CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


def enum_type(enum_cls: type[StrEnum], name: str, *, length: int = 32) -> Enum:
    """A StrEnum stored as VARCHAR + CHECK (DATABASE.md §1), never a native PG enum."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def money_type() -> Numeric[Decimal]:
    """NUMERIC(18,2) — amounts in a currency's minor-unit precision."""
    return Numeric(18, 2)


def rate_type() -> Numeric[Decimal]:
    """NUMERIC(9,6) — annual rates as fractions."""
    return Numeric(9, 6)


def ratio_type() -> Numeric[Decimal]:
    """NUMERIC(10,4) — ratios such as DTI thresholds."""
    return Numeric(10, 4)


def _json_default(value: Any) -> Any:
    # Money and ids are serialised as strings so JSONB snapshots never pass through floats.
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True)


def build_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        json_serializer=json_dumps,
        # Fail fast when the database is unreachable so readiness probes stay responsive.
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session per request.

    Use cases commit explicitly when they succeed; anything left uncommitted is rolled back
    here, so a failed request never persists partial changes.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
