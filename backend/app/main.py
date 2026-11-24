"""Application factory. Run with: ``uvicorn --factory app.main:create_app``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import health
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.db import build_engine, build_session_factory
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.modules.analytics.router import router as analytics_router
from app.modules.applicants.router import router as applicants_router
from app.modules.applications.router import router as applications_router
from app.modules.audit.router import router as audit_router
from app.modules.decisions.router import router as decisions_router
from app.modules.identity.router import auth_router, users_router
from app.modules.loans.router import accounts as loan_accounts_router
from app.modules.loans.router import router as loans_router
from app.modules.products.router import router as products_router

API_PREFIX = "/api/v1"


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth_router)
    router.include_router(users_router)
    router.include_router(products_router)
    router.include_router(applicants_router)
    router.include_router(applications_router)
    router.include_router(decisions_router)
    router.include_router(loans_router)
    router.include_router(loan_accounts_router)
    router.include_router(analytics_router)
    router.include_router(audit_router)
    return router


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    engine = build_engine(settings.database_url_str)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        structlog.get_logger("credify").info("startup", environment=settings.environment)
        yield
        engine.dispose()

    is_production = settings.environment == "production"
    app = FastAPI(
        title="Credify API",
        version="0.1.0",
        summary="Educational digital lending simulation — synthetic data only.",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.clock = clock or SystemClock()

    register_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)  # added last → outermost

    app.include_router(health.router)
    app.include_router(build_api_router(), prefix=API_PREFIX)
    return app
