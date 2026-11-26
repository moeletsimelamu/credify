"""Identity use cases: registration, login, token rotation, logout, staff management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AuthenticationError, BusinessRuleError, ConflictError, NotFoundError
from app.core.ids import uuid7
from app.core.pagination import PageParams
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.modules.audit import service as audit
from app.modules.identity.models import RefreshToken, Role, User
from app.modules.identity.schemas import RegisterRequest, UserCreate, UserUpdate

INVALID_CREDENTIALS = "Invalid email or password"


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    user: User
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RequestMeta:
    ip_address: str | None
    user_agent: str | None


def _email_exists(session: Session, email: str) -> bool:
    return session.scalar(select(func.count()).where(User.email == email)) != 0


def create_user_record(
    session: Session, *, email: str, password: str, full_name: str, role: Role
) -> User:
    if _email_exists(session, email):
        raise ConflictError("An account with this email address already exists")
    user = User(
        id=uuid7(),
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role=role,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:  # concurrent registration with the same email
        raise ConflictError("An account with this email address already exists") from exc
    return user


def register_applicant(
    session: Session, data: RegisterRequest, *, clock: Clock, meta: RequestMeta
) -> User:
    user = create_user_record(
        session,
        email=data.email,
        password=data.password,
        full_name=data.full_name,
        role=Role.APPLICANT,
    )
    audit.record(
        session,
        action="user.registered",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        occurred_at=clock.now(),
        ip_address=meta.ip_address,
        changes={"role": {"from": None, "to": user.role.value}},
    )
    session.commit()
    return user


def _issue_tokens(
    session: Session,
    user: User,
    *,
    settings: Settings,
    now: datetime,
    meta: RequestMeta,
    family_id: uuid.UUID | None = None,
) -> tuple[IssuedTokens, RefreshToken]:
    access_token, access_expires_at = create_access_token(
        user_id=user.id, role=user.role.value, settings=settings, now=now
    )
    raw_refresh = generate_refresh_token()
    refresh_expires_at = now + timedelta(days=settings.refresh_token_ttl_days)
    refresh = RefreshToken(
        id=uuid7(),
        user_id=user.id,
        family_id=family_id or uuid7(),
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=refresh_expires_at,
        user_agent=meta.user_agent,
    )
    session.add(refresh)
    issued = IssuedTokens(
        user=user,
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=raw_refresh,
        refresh_expires_at=refresh_expires_at,
    )
    return issued, refresh


def _fail_login(
    session: Session, *, user: User | None, reason: str, now: datetime, meta: RequestMeta
) -> AuthenticationError:
    audit.record(
        session,
        action="auth.login_failed",
        entity_type="user",
        entity_id=user.id if user else None,
        actor=None,
        occurred_at=now,
        ip_address=meta.ip_address,
        context={"reason": reason},
    )
    session.commit()  # failed attempts must be recorded even though the request fails
    return AuthenticationError(INVALID_CREDENTIALS)


def authenticate(
    session: Session,
    *,
    email: str,
    password: str,
    settings: Settings,
    clock: Clock,
    meta: RequestMeta,
) -> IssuedTokens:
    now = clock.now()
    user = session.scalar(select(User).where(User.email == email).with_for_update())

    # Always run one Argon2 verification so timing does not reveal whether the account exists.
    password_ok = verify_password(user.password_hash if user else None, password)

    if user is None:
        raise _fail_login(session, user=None, reason="unknown_account", now=now, meta=meta)
    if user.locked_until is not None and user.locked_until > now:
        raise _fail_login(session, user=user, reason="account_locked", now=now, meta=meta)
    if not password_ok:
        user.failed_login_count += 1
        reason = "bad_password"
        if user.failed_login_count >= settings.login_max_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
            reason = "bad_password_lockout_started"
        raise _fail_login(session, user=user, reason=reason, now=now, meta=meta)
    if not user.is_active:
        raise _fail_login(session, user=user, reason="inactive_account", now=now, meta=meta)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    issued, _ = _issue_tokens(session, user, settings=settings, now=now, meta=meta)
    audit.record(
        session,
        action="auth.login_succeeded",
        entity_type="user",
        entity_id=user.id,
        actor=user,
        occurred_at=now,
        ip_address=meta.ip_address,
    )
    session.commit()
    return issued


def _revoke_family(session: Session, family_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def rotate_refresh_token(
    session: Session, *, raw_token: str | None, settings: Settings, clock: Clock, meta: RequestMeta
) -> IssuedTokens:
    if not raw_token:
        raise AuthenticationError("Missing refresh token")
    now = clock.now()
    token = session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if token is None:
        raise AuthenticationError("Invalid refresh token")

    if token.revoked_at is not None:
        # A rotated token was presented again: assume theft and end every session in the family.
        _revoke_family(session, token.family_id, now)
        audit.record(
            session,
            action="auth.refresh_token_reuse_detected",
            entity_type="user",
            entity_id=token.user_id,
            occurred_at=now,
            ip_address=meta.ip_address,
            context={"family_id": str(token.family_id)},
        )
        session.commit()
        raise AuthenticationError("Invalid refresh token")

    if token.expires_at <= now:
        raise AuthenticationError("Refresh token expired")

    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        _revoke_family(session, token.family_id, now)
        session.commit()
        raise AuthenticationError("Invalid refresh token")

    issued, new_token = _issue_tokens(
        session, user, settings=settings, now=now, meta=meta, family_id=token.family_id
    )
    session.flush()  # the replacement must exist before it is referenced
    token.revoked_at = now
    token.replaced_by_id = new_token.id
    session.commit()
    return issued


def logout(session: Session, *, raw_token: str | None, clock: Clock, meta: RequestMeta) -> None:
    if not raw_token:
        return
    token = session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if token is None or token.revoked_at is not None:
        return
    now = clock.now()
    token.revoked_at = now
    audit.record(
        session,
        action="auth.logout",
        entity_type="user",
        entity_id=token.user_id,
        occurred_at=now,
        ip_address=meta.ip_address,
    )
    session.commit()


# --- Staff / user administration -------------------------------------------------------


def _user_snapshot(user: User) -> dict[str, object]:
    return {"full_name": user.full_name, "role": user.role.value, "is_active": user.is_active}


def create_user(
    session: Session, data: UserCreate, *, actor: User, clock: Clock, meta: RequestMeta
) -> User:
    user = create_user_record(
        session, email=data.email, password=data.password, full_name=data.full_name, role=data.role
    )
    audit.record(
        session,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        actor=actor,
        occurred_at=clock.now(),
        ip_address=meta.ip_address,
        changes=audit.diff({}, _user_snapshot(user)),
    )
    session.commit()
    return user


def get_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


def list_users(
    session: Session, params: PageParams, *, role: Role | None = None
) -> tuple[list[User], int]:
    query = select(User)
    if role is not None:
        query = query.where(User.role == role)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(User.created_at.desc(), User.id)
        .offset(params.offset)
        .limit(params.page_size)
    ).all()
    return list(rows), total


def update_user(
    session: Session,
    user_id: uuid.UUID,
    data: UserUpdate,
    *,
    actor: User,
    clock: Clock,
    meta: RequestMeta,
) -> User:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFoundError("User not found")
    if user.id == actor.id and (
        (data.role is not None and data.role != user.role) or data.is_active is False
    ):
        raise BusinessRuleError(
            "Administrators cannot change their own role or deactivate themselves"
        )

    before = _user_snapshot(user)
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    changes = audit.diff(before, _user_snapshot(user))
    if not changes:
        return user

    now = clock.now()
    if user.is_active is False:
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    audit.record(
        session,
        action="user.updated",
        entity_type="user",
        entity_id=user.id,
        actor=actor,
        occurred_at=now,
        ip_address=meta.ip_address,
        changes=changes,
    )
    session.commit()
    return user


def list_staff(session: Session) -> list[User]:
    """Active back-office users, for name lookups and the assignment picker."""
    return list(
        session.scalars(
            select(User)
            .where(User.is_active.is_(True), User.role != Role.APPLICANT)
            .order_by(User.full_name, User.id)
        ).all()
    )
