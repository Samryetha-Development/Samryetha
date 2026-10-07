"""Typed records and HTTP contracts for users."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..ids import UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class AccountRole(StrEnum):
    Student = "student"
    Moderator = "moderator"
    Admin = "admin"


class AccountStatus(StrEnum):
    Pending = "pending"
    Active = "active"
    Banned = "banned"
    Deactivated = "deactivated"


class ProfileModerationStatus(StrEnum):
    Approved = "approved"
    Pending = "pending"
    Rejected = "rejected"


class UserRow(TypedDict):
    id: int
    username: str
    email: str
    recovery_email: str | None
    display_name: str
    bio: str
    profile_moderation_status: str
    pending_display_name: str | None
    pending_bio: str | None
    password_hash: str
    role: str
    status: str
    discriminator: int | None
    email_domain: str | None
    email_verified_at: int | None
    avatar_object_key: str | None
    last_seen_at: int | None
    settings: str
    created_at: int | None
    updated_at: int | None
    deleted_at: int | None


class ProfilePatch(TypedDict, total=False):
    displayName: str
    username: str
    recoveryEmail: str
    bio: str
    avatarObjectKey: str | None
    settings: dict[str, object]


class UserModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class ProfileBody(UserModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")
    display_name: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    username: Annotated[str, Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_]+$")] | None = None
    recovery_email: Annotated[str, Field(min_length=3, max_length=200)] | None = None
    bio: Annotated[str, Field(max_length=500)] | None = None
    avatar_object_key: str | None = None
    settings: dict[str, object] | None = None

    @field_validator("display_name", "username", "bio", mode="before")
    @classmethod
    def strip_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class UserResponse(UserModel):
    id: UserID
    username: str
    handle: str
    display_name: str
    email: str
    recovery_email: str | None
    role: AccountRole
    status: AccountStatus
    bio: str
    profile_pending: bool
    email_verified: bool
    avatar_object_key: str | None
    settings: dict[str, object]
    created_at: int | None
    last_seen_at: int | None


class UserEnvelopeResponse(UserModel):
    user: UserResponse


class ProfileStats(UserModel):
    discussions: int
    replies: int
    followers: int
    following: int


class PublicProfileResponse(UserModel):
    id: UserID
    username: str
    handle: str
    display_name: str
    bio: str
    avatar_object_key: str | None
    joined_at: int | None
    last_seen_at: int | None
    stats: ProfileStats
    is_following: bool
