"""Small FastAPI dependencies for app-wide state."""

from __future__ import annotations

import ipaddress

from fastapi import Header, Request

from app.core.config import Settings
from app.core.errors import DomainError


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def client_ip(request: Request) -> str | None:
    """The peer address, or None when it is not a valid IP (audit columns are INET)."""
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:300] if value else None


def if_match_version(if_match: str | None = Header(None)) -> int | None:
    """Optional optimistic-concurrency precondition: ``If-Match: "<version>"``.

    Clients send the ``version`` they last read; a mismatch yields 409 instead of silently
    overwriting someone else's change.
    """
    if if_match is None:
        return None
    raw = if_match.strip().removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise DomainError('If-Match must contain the resource version, e.g. "3"')
    return int(raw)
