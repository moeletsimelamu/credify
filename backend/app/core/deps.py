"""Small FastAPI dependencies for app-wide state."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.core.config import Settings


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
