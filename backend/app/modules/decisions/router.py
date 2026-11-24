"""The controlled reason-code list. Decisions themselves are recorded through
``/applications/{id}/decision`` because they are status transitions."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.decisions.domain.reason_codes import CATALOGUE
from app.modules.decisions.schemas import ReasonRead
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.models import User

router = APIRouter(prefix="/decision-reason-codes", tags=["decisions"])


@router.get("", response_model=list[ReasonRead])
def list_reason_codes(_: User = Depends(get_current_user)) -> list[ReasonRead]:
    return [ReasonRead.for_code(code) for code in CATALOGUE]
