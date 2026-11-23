"""Domain errors and RFC 9457 problem-details responses."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"
logger = structlog.get_logger("credify.errors")


class DomainError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"
    title: str = "The request could not be processed"

    def __init__(self, detail: str | None = None, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"
    title = "Resource not found"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"
    title = "The request conflicts with the current state"


class AuthenticationError(DomainError):
    status_code = 401
    code = "AUTHENTICATION_FAILED"
    title = "Authentication failed"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "PERMISSION_DENIED"
    title = "You do not have permission to perform this action"


class BusinessRuleError(DomainError):
    status_code = 422
    code = "BUSINESS_RULE_VIOLATION"
    title = "The request violates a business rule"


def business_rule_error(exc: ValidationError) -> BusinessRuleError:
    """Turn a service-side re-validation failure (e.g. a merged PATCH) into a 422."""
    violations = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err.get("loc", ()))
        message = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        violations.append(f"{field}: {message}" if field else message)
    return BusinessRuleError("; ".join(violations), extra={"violations": violations})


def _problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:credify:problem:{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "instance": request.url.path,
        "request_id": structlog.contextvars.get_contextvars().get("request_id"),
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):  # handlers are registered per type
        raise exc
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return _problem(
        request,
        status=exc.status_code,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        extra=exc.extra,
        headers=headers,
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # handlers are registered per type
        raise exc
    # Deliberately omit the submitted input: it may contain passwords or financial data.
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return _problem(
        request,
        status=422,
        code="VALIDATION_FAILED",
        title="Request validation failed",
        detail="One or more fields are invalid.",
        extra={"errors": errors},
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # handlers are registered per type
        raise exc
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "AUTHENTICATION_FAILED"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    return _problem(
        request,
        status=exc.status_code,
        code=code,
        title=str(exc.detail) if exc.detail else "HTTP error",
        detail=str(exc.detail) if exc.detail else "HTTP error",
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _stale_data_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StaleDataError):  # handlers are registered per type
        raise exc
    # Optimistic locking (version_id) detected a concurrent write to the same aggregate.
    return _problem(
        request,
        status=409,
        code="CONCURRENT_MODIFICATION",
        title="The resource was modified by another request",
        detail="Reload the resource and try again.",
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", error_type=type(exc).__name__)
    return _problem(
        request,
        status=500,
        code="INTERNAL_ERROR",
        title="Internal server error",
        detail="An unexpected error occurred. Quote the request_id when reporting it.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(StaleDataError, _stale_data_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
