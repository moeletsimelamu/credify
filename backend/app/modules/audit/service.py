"""Audit trail writer.

Events are added to the caller's session, so they commit (or roll back) atomically with
the change they describe.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.pagination import PageParams
from app.modules.audit.models import AuditEvent

if TYPE_CHECKING:
    from app.modules.identity.models import User


def record(
    session: Session,
    *,
    action: str,
    entity_type: str,
    occurred_at: datetime,
    entity_id: uuid.UUID | str | None = None,
    actor: User | None = None,
    changes: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        occurred_at=occurred_at,
        actor_user_id=actor.id if actor else None,
        actor_role=actor.role.value if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        request_id=structlog.contextvars.get_contextvars().get("request_id"),
        ip_address=ip_address,
        changes=dict(changes) if changes else None,
        context=dict(context) if context else None,
    )
    session.add(event)
    return event


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Field-level changes between two snapshots: ``{field: {"from": a, "to": b}}``."""
    return {
        key: {"from": before.get(key), "to": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }


def list_events(
    session: Session,
    params: PageParams,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
) -> tuple[list[AuditEvent], int]:
    query = select(AuditEvent)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditEvent.entity_id == entity_id)
    if actor_user_id:
        query = query.where(AuditEvent.actor_user_id == actor_user_id)
    if action:
        query = query.where(AuditEvent.action == action)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    return list(rows), total
