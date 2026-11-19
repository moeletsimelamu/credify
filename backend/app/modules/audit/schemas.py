from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: str | None
    request_id: str | None
    ip_address: str | IPv4Address | IPv6Address | None
    changes: dict[str, Any] | None
    context: dict[str, Any] | None

    @field_serializer("ip_address")
    def _ip_as_text(self, value: object) -> str | None:
        return str(value) if value is not None else None
