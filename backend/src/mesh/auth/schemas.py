"""Pydantic request/response schemas for the auth module (auth.md §3.4).

Response bodies are wrapped by the API layer in the §6.14 ``{"data": ...}``
envelope; these models describe the ``data`` payload. Secrets (passwords,
refresh tokens, TOTP codes) are write-only — never echoed back.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# --- auth: register / login / refresh ----------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """Issued on login / successful refresh (auth.md §3.4)."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str


class MfaRequiredResponse(BaseModel):
    """Returned by login when the account has TOTP enabled (no tokens yet)."""

    mfa_required: bool = True
    mfa_ticket: str


class MfaVerifyRequest(BaseModel):
    mfa_ticket: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=16)


# --- password reset / email verification -------------------------------------


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=1, max_length=256)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


# --- MFA management ----------------------------------------------------------


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    backup_codes: list[str]


class MfaConfirmRequest(BaseModel):
    code: str = Field(min_length=1, max_length=16)


# --- user / session read models ----------------------------------------------


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = None
    theme: str | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    email_verified: bool
    display_name: str
    avatar_url: str | None
    status: str
    timezone: str | None
    settings: dict
    mfa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime


class SessionResponse(BaseModel):
    id: uuid.UUID
    type: str
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_active_at: datetime | None
    expires_at: datetime
    current: bool = False


class UpdateUserRequest(BaseModel):
    """PATCH /api/v1/users/me (auth.md §3.1 R3) — only the listed fields."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_url: str | None = None
    timezone: str | None = None
    settings: UserSettings | None = None
