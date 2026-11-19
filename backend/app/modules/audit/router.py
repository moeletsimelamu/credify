from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.pagination import Page, PageParams, page_params
from app.modules.audit import service
from app.modules.audit.schemas import AuditEventRead
from app.modules.identity.dependencies import require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission

router = APIRouter(prefix="/audit-events", tags=["audit"])


@router.get("", response_model=Page[AuditEventRead])
def list_audit_events(
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    params: PageParams = Depends(page_params),
    session: Session = Depends(get_session),
    _: User = Depends(require(Permission.AUDIT_READ)),
) -> Page[AuditEventRead]:
    rows, total = service.list_events(
        session,
        params,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
    )
    return Page[AuditEventRead](
        items=[AuditEventRead.model_validate(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )
