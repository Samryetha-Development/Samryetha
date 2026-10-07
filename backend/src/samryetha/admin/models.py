"""Typed admin commands and HTTP responses."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..core.ids import DiscussionID, ReplyID, UserID
from ..moderation.models import ModerationAuthorResponse
from ..users.models import AccountRole, AccountStatus


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class AdminAssignableRole(StrEnum):
    Student = "student"
    Admin = "admin"


class AdminMutableStatus(StrEnum):
    Active = "active"
    Deactivated = "deactivated"


class AdminModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class AdminRequest(AdminModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class ChangeRoleBody(AdminRequest):
    role: AdminAssignableRole
    reason: Annotated[str, Field(max_length=1000)] | None = None


class ChangeStatusBody(AdminRequest):
    status: AdminMutableStatus
    reason: Annotated[str, Field(max_length=1000)] | None = None


class UserDistribution(AdminModel):
    total: int
    pending: int
    active: int
    banned: int
    deactivated: int


class ContentStats(AdminModel):
    discussions: int
    replies: int
    boards: int


class ModerationStats(AdminModel):
    open_reports: int
    active_bans: int


class ActivityStats(AdminModel):
    active_today: int
    new_users_today: int
    new_discussions_today: int
    new_replies_today: int
    online_now: int


class AdminStatsResponse(AdminModel):
    users: UserDistribution
    content: ContentStats
    moderation: ModerationStats
    activity: ActivityStats


class AdminUserResponse(AdminModel):
    id: UserID
    username: str
    handle: str
    display_name: str
    email: str
    role: AccountRole
    status: AccountStatus
    email_verified: bool
    created_at: int | None
    last_seen_at: int | None
    ban_active: bool
    report_count: int


class AdminUserListResponse(AdminModel):
    items: list[AdminUserResponse]
    next_cursor: UserID | None


class TemporaryPasswordResponse(AdminModel):
    temporary_password: str


class DeletedDiscussionResponse(AdminModel):
    id: DiscussionID
    board_slug: str
    title: str
    preview: str
    deleted_by: ModerationAuthorResponse | None
    deleted_at: int
    reason: str | None


class DeletedReplyResponse(AdminModel):
    id: ReplyID
    discussion_id: DiscussionID
    discussion_title: str
    preview: str
    deleted_by: ModerationAuthorResponse | None
    deleted_at: int
    reason: str | None


class DeletedContentResponse(AdminModel):
    discussions: list[DeletedDiscussionResponse]
    replies: list[DeletedReplyResponse]
    next_discussion_cursor: DiscussionID | None
    next_reply_cursor: ReplyID | None


class AdminOperationOkResponse(AdminModel):
    ok: bool = True
