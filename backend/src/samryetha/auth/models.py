"""Typed HTTP responses for authentication flows."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..core.ids import UserID
from ..users.models import UserResponse


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class AuthModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class AuthConfigResponse(AuthModel):
    oidc_enabled: bool
    password_auth_enabled: bool
    oidc_mode: Literal["json", "redirect"]
    lako_origin: str | None


class AuthSessionResponse(AuthModel):
    user: UserResponse
    session_expires_at: int


class RegisterResponse(AuthModel):
    user_id: UserID
    message: str


class AuthOperationOkResponse(AuthModel):
    ok: bool = True


class PasswordResetRequestResponse(AuthOperationOkResponse):
    message: str


class ClaimInfoResponse(AuthModel):
    email: str | None
    display_name: str | None
    expires_at: int


class QrStartResponse(BaseModel):
    ticket_id: str
    secret: str
    approve_url: str
    qr_data_uri: str
    expiresAt: int


class QrInfoResponse(AuthModel):
    created_at: int
    expires_at: int
    ip: str | None
    user_agent: str | None
    email_confirmation_required: bool
    email_hint: str | None


class QrConfirmationResponse(AuthModel):
    required: bool
    email_hint: str | None = None
