from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.modules.identity.models import Role

# NIST SP 800-63B: favour length over composition rules; cap length to bound hashing cost.
Password = Annotated[str, Field(min_length=12, max_length=128)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: Password
    full_name: FullName


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=128)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class CurrentUserRead(UserRead):
    permissions: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — OAuth2 token type, not a secret
    expires_at: datetime
    user: CurrentUserRead


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    full_name: FullName
    role: Role
    password: Password


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: FullName | None = None
    role: Role | None = None
    is_active: bool | None = None


class StaffMemberRead(BaseModel):
    """The directory entry other staff see: enough to show a name and assign work."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    full_name: str
    role: Role
