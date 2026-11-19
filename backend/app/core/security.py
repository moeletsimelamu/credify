"""Password hashing (Argon2id), access tokens (JWT) and refresh-token helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.errors import AuthenticationError

_hasher = PasswordHasher()  # argon2-cffi defaults follow RFC 9106 recommendations
# Verified against when an account does not exist, so response time does not reveal it.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    role: str
    expires_at: datetime
    token_id: str


def create_access_token(
    *, user_id: uuid.UUID, role: str, settings: Settings, now: datetime
) -> tuple[str, datetime]:
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_hex(16),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)
    return token, expires_at


_CLOCK_SKEW_SECONDS = 30


def decode_access_token(token: str, settings: Settings, *, now: datetime) -> AccessTokenClaims:
    """Verify signature, issuer and type; check expiry against the injected clock.

    PyJWT's own time checks use the system clock, so they are disabled and done here
    against ``now`` instead — keeping token expiry consistent with the rest of the
    clock-driven domain logic and testable.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            issuer=settings.jwt_issuer,
            options={
                "require": ["sub", "exp", "iat", "iss", "jti"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
        if payload.get("typ") != "access":
            raise AuthenticationError("Invalid token type")
        current = now.timestamp()
        if int(payload["exp"]) <= current:
            raise AuthenticationError("Invalid or expired access token")
        if int(payload["iat"]) > current + _CLOCK_SKEW_SECONDS:
            raise AuthenticationError("Invalid or expired access token")
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            role=str(payload["role"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            token_id=str(payload["jti"]),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise AuthenticationError("Invalid or expired access token") from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits of entropy


def hash_refresh_token(token: str) -> str:
    # Refresh tokens are high-entropy random values, so a fast hash is appropriate here.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
