"""Import every ORM model so ``Base.metadata`` is complete (used by Alembic and tests)."""

from app.core.db import Base
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import RefreshToken, User

__all__ = ["AuditEvent", "Base", "RefreshToken", "User"]
