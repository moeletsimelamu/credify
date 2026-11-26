from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import Settings
from app.core.db import get_session
from app.core.deps import client_ip, get_app_settings, user_agent
from app.core.pagination import Page, PageParams, page_params
from app.modules.identity import service
from app.modules.identity.dependencies import get_current_user, require
from app.modules.identity.models import Role, User
from app.modules.identity.permissions import Permission, permissions_for
from app.modules.identity.schemas import (
    CurrentUserRead,
    LoginRequest,
    RegisterRequest,
    StaffMemberRead,
    TokenResponse,
    UserCreate,
    UserRead,
    UserUpdate,
)

REFRESH_COOKIE = "credify_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"

auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])
staff_router = APIRouter(prefix="/staff", tags=["users"])


def _meta(request: Request) -> service.RequestMeta:
    return service.RequestMeta(ip_address=client_ip(request), user_agent=user_agent(request))


def _current_user_read(user: User) -> CurrentUserRead:
    return CurrentUserRead.model_validate(
        {**UserRead.model_validate(user).model_dump(), "permissions": permissions_for(user.role)}
    )


def _token_response(
    response: Response, issued: service.IssuedTokens, settings: Settings
) -> TokenResponse:
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    return TokenResponse(
        access_token=issued.access_token,
        expires_at=issued.access_expires_at,
        user=_current_user_read(issued.user),
    )


@auth_router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(
    data: RegisterRequest,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
) -> User:
    """Self-service registration. Always creates an APPLICANT account."""
    return service.register_applicant(session, data, clock=clock, meta=_meta(request))


@auth_router.post("/login", response_model=TokenResponse)
def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> TokenResponse:
    issued = service.authenticate(
        session,
        email=data.email,
        password=data.password,
        settings=settings,
        clock=clock,
        meta=_meta(request),
    )
    return _token_response(response, issued, settings)


@auth_router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> TokenResponse:
    issued = service.rotate_refresh_token(
        session,
        raw_token=request.cookies.get(REFRESH_COOKIE),
        settings=settings,
        clock=clock,
        meta=_meta(request),
    )
    return _token_response(response, issued, settings)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    clock: Clock = Depends(get_clock),
) -> Response:
    service.logout(
        session, raw_token=request.cookies.get(REFRESH_COOKIE), clock=clock, meta=_meta(request)
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    return response


@auth_router.get("/me", response_model=CurrentUserRead)
def me(user: User = Depends(get_current_user)) -> CurrentUserRead:
    return _current_user_read(user)


# --- Administration ---------------------------------------------------------------------

_manage_users = require(Permission.USER_MANAGE)


@users_router.get("", response_model=Page[UserRead])
def list_users(
    role: Role | None = None,
    params: PageParams = Depends(page_params),
    session: Session = Depends(get_session),
    _: User = Depends(_manage_users),
) -> Page[UserRead]:
    rows, total = service.list_users(session, params, role=role)
    return Page[UserRead](
        items=[UserRead.model_validate(u) for u in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@users_router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    data: UserCreate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    actor: User = Depends(_manage_users),
) -> User:
    return service.create_user(session, data, actor=actor, clock=clock, meta=_meta(request))


@users_router.get("/{user_id}", response_model=UserRead)
def get_user(
    user_id: uuid.UUID,
    session: Session = Depends(get_session),
    _: User = Depends(_manage_users),
) -> User:
    return service.get_user(session, user_id)


@users_router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    request: Request,
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
    actor: User = Depends(_manage_users),
) -> User:
    return service.update_user(
        session, user_id, data, actor=actor, clock=clock, meta=_meta(request)
    )


@staff_router.get("", response_model=list[StaffMemberRead])
def list_staff(
    session: Session = Depends(get_session),
    _: User = Depends(require(Permission.APPLICATION_READ_ANY)),
) -> list[User]:
    """Active staff names and roles (no emails), for any back-office user."""
    return service.list_staff(session)
