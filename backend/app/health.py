from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.credit_risk import registry

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    """The process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """The API can serve traffic: the database is reachable and, if a credit-risk model is
    active, its artefact is present and matches its registered SHA-256.

    No active model is not a failure (scoring is advisory and records ``FAILED``); an active
    model whose file is missing or altered is, because every score would then fail.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    checks: dict[str, str] = {}
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = "ok"
            active = registry.active_version(session)
            if active is None:
                checks["model"] = "none active"
            else:
                model_dir = request.app.state.settings.model_dir
                try:
                    if model_dir is None:
                        raise registry.ModelIntegrityError("no model directory is configured")
                    # Re-hash the file on disk every time (not the cached model), so a file
                    # altered after it was loaded is reported.
                    registry.verified_bytes(model_dir, active)
                    checks["model"] = f"ok ({active.name} {active.version})"
                except registry.ModelIntegrityError:
                    checks["model"] = "artefact failed verification"
    except SQLAlchemyError:
        checks["database"] = "unavailable"
    healthy = checks.get("database") == "ok" and checks.get("model") != (
        "artefact failed verification"
    )
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
    )
