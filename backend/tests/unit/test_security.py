from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.errors import AuthenticationError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from tests.conftest import make_settings

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def test_password_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("a-long-enough-password")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a-long-enough-password")
    assert not verify_password(hashed, "wrong-password")


def test_password_hashes_are_salted() -> None:
    assert hash_password("same-password-twice") != hash_password("same-password-twice")


def test_verify_password_handles_missing_and_corrupt_hashes() -> None:
    assert not verify_password(None, "anything")
    assert not verify_password("not-a-hash", "anything")


def test_access_token_round_trip() -> None:
    settings = make_settings()
    user_id = uuid.uuid4()
    token, expires_at = create_access_token(
        user_id=user_id, role="ADMIN", settings=settings, now=NOW
    )
    claims = decode_access_token(token, settings, now=NOW + timedelta(minutes=5))
    assert claims.user_id == user_id
    assert claims.role == "ADMIN"
    assert expires_at == NOW + timedelta(minutes=settings.access_token_ttl_minutes)


def test_expired_access_token_is_rejected() -> None:
    settings = make_settings()
    token, expires_at = create_access_token(
        user_id=uuid.uuid4(), role="ADMIN", settings=settings, now=NOW
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings, now=expires_at)


def test_token_issued_in_the_future_is_rejected() -> None:
    settings = make_settings()
    token, _ = create_access_token(
        user_id=uuid.uuid4(), role="ADMIN", settings=settings, now=NOW + timedelta(hours=1)
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings, now=NOW)


def test_token_signed_with_another_secret_is_rejected() -> None:
    token, _ = create_access_token(
        user_id=uuid.uuid4(), role="ADMIN", settings=make_settings(), now=NOW
    )
    other = make_settings(jwt_secret="x" * 20 + "a-different-random-value-0123456789")
    with pytest.raises(AuthenticationError):
        decode_access_token(token, other, now=NOW)


def test_unsigned_alg_none_token_is_rejected() -> None:
    settings = make_settings()
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": "ADMIN",
            "iss": "credify",
            "typ": "access",
            "iat": int(NOW.timestamp()),
            "exp": int(NOW.timestamp()) + 600,
            "jti": "x",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(forged, settings, now=NOW)


def test_tampered_role_claim_is_rejected() -> None:
    settings = make_settings()
    token, _ = create_access_token(
        user_id=uuid.uuid4(), role="APPLICANT", settings=settings, now=NOW
    )
    header, payload, signature = token.split(".")
    tampered_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b"APPLICANT", b"ADMIN")
    ).decode()
    with pytest.raises(AuthenticationError):
        decode_access_token(f"{header}.{tampered_payload}.{signature}", settings, now=NOW)


def test_refresh_tokens_are_random_and_hashed() -> None:
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b
    assert len(a) >= 43  # 32 bytes, base64url
    assert hash_refresh_token(a) == hash_refresh_token(a)
    assert hash_refresh_token(a) != a
    assert len(hash_refresh_token(a)) == 64
