"""Authentication and authorisation dependencies."""

from __future__ import annotations

from collections.abc import Callable

import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import Settings
from app.core.db import get_session
from app.core.deps import get_app_settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission, has_permission

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> User:
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    claims = decode_access_token(credentials.credentials, settings, now=clock.now())
    # The role is read from the database, not the token, so role changes and
    # deactivation take effect immediately rather than when the token expires.
    user = session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired access token")
    structlog.contextvars.bind_contextvars(user_id=str(user.id), role=user.role.value)
    return user


def require(*permissions: Permission) -> Callable[..., User]:
    """Dependency factory: the current user must hold *all* listed permissions."""

    def dependency(user: User = Depends(get_current_user)) -> User:
        missing = [p for p in permissions if not has_permission(user.role, p)]
        if missing:
            raise PermissionDeniedError(extra={"missing_permissions": [p.value for p in missing]})
        return user

    return dependency
