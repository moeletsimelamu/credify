"""Request context, access logging and baseline security headers (pure ASGI)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")
access_logger = structlog.get_logger("credify.access")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
# Swagger UI needs to load its own assets; relax CSP only for the docs pages.
_DOCS_PATHS = ("/docs", "/redoc")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_PATTERN.match(incoming) else str(uuid.uuid4())
        path: str = scope["path"]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=scope["method"], path=path
        )
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for name, value in SECURITY_HEADERS.items():
                    if name == "Content-Security-Policy" and path.startswith(_DOCS_PATHS):
                        continue
                    headers.setdefault(name, value)
            await send(message)

        # Context is cleared at the *start* of each request, not here, so the outermost
        # 500 handler can still report the request_id after this middleware unwinds.
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_logger.info(
                "request_completed",
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
