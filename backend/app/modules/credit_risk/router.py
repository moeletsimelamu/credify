"""The model registry. There is deliberately no upload endpoint: models are registered from
the offline pipeline, and the API only loads files from its read-only model directory."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import Settings
from app.core.db import get_session
from app.core.deps import client_ip, get_app_settings
from app.modules.credit_risk import registry
from app.modules.credit_risk.models import ModelVersion
from app.modules.credit_risk.schemas import ModelVersionRead
from app.modules.identity.dependencies import require
from app.modules.identity.models import User
from app.modules.identity.permissions import Permission

router = APIRouter(prefix="/models", tags=["credit-risk models"])


@router.get("", response_model=list[ModelVersionRead])
def list_models(
    session: Session = Depends(get_session),
    _: User = Depends(require(Permission.ANALYTICS_MODEL)),
) -> Sequence[ModelVersion]:
    """Every registered version with its metrics, threshold, bands and provenance."""
    return registry.list_versions(session)


@router.get("/{model_id}", response_model=ModelVersionRead)
def get_model(
    model_id: uuid.UUID,
    session: Session = Depends(get_session),
    _: User = Depends(require(Permission.ANALYTICS_MODEL)),
) -> ModelVersion:
    return registry.get_version(session, model_id)


@router.post("/{model_id}/activate", response_model=ModelVersionRead)
def activate_model(
    model_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_app_settings),
    user: User = Depends(require(Permission.MODEL_MANAGE)),
) -> ModelVersion:
    """Make a CANDIDATE the active model (audited). Its artefact is verified first."""
    return registry.activate(
        session,
        model_id,
        model_dir=settings.model_dir,
        actor=user,
        now=clock.now(),
        ip_address=client_ip(request),
    )
