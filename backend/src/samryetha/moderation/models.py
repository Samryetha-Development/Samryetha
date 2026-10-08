"""Typed commands and results for reports, bans, and moderation actions."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.ids import DiscussionID, ModerationActionID, ReplyID, ReportID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ReportableType(StrEnum):
    Discussion = "discussion"
    Reply = "reply"
    User = "user"


class ReportStatus(StrEnum):
    Open = "open"
    InProgress = "in_progress"
    Resolved = "resolved"
    Dismissed = "dismissed"


class ReportReviewAction(StrEnum):
    Delete = "delete"
    Dismiss = "dismiss"
    Ban = "ban"


class RestoreTargetType(StrEnum):
    Discussion = "discussion"
    Reply = "reply"


class ModerationModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class ModerationRequest(ModerationModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class CreateReportBody(ModerationRequest):
    reportable_type: ReportableType
    reportable_id: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class ResolveReportBody(ModerationRequest):
    status: ReportStatus
    action: Annotated[str, Field(max_length=100)] | None = None
    reason: Annotated[str, Field(max_length=1000)] | None = None


class ReviewReportBody(ModerationRequest):
    action: ReportReviewAction
    reason: Annotated[str, Field(max_length=1000)] | None = None


class BanUserBody(ModerationRequest):
    username: Annotated[str, Field(min_length=1, max_length=30)]
    reason: Annotated[str, Field(max_length=1000)] | None = None
    duration_hours: Annotated[int, Field(ge=1, le=24 * 365)] | None = None


class UnbanUserBody(ModerationRequest):
    reason: Annotated[str, Field(max_length=1000)] | None = None


class RestoreBody(ModerationRequest):
    target_type: RestoreTargetType
    target_id: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(max_length=1000)] | None = None


class ModerationAuthorResponse(ModerationModel):
    id: UserID
    username: str
    handle: str
    display_name: str


class DiscussionReportTarget(ModerationModel):
    type: Literal["discussion"] = "discussion"
    id: DiscussionID
    title: str
    board_slug: str
    body_markdown: str
    author: ModerationAuthorResponse | None
    is_deleted: bool


class ReplyReportTarget(ModerationModel):
    type: Literal["reply"] = "reply"
    id: ReplyID
    discussion_id: DiscussionID
    body_markdown: str
    author: ModerationAuthorResponse | None
    is_deleted: bool


class UserReportTarget(ModerationModel):
    type: Literal["user"] = "user"
    id: UserID
    username: str
    handle: str
    display_name: str


type ReportTarget = DiscussionReportTarget | ReplyReportTarget | UserReportTarget


class ReportResponse(ModerationModel):
    id: ReportID
    reporter: ModerationAuthorResponse | None
    reportable_type: ReportableType
    reportable_id: int
    reason: str | None
    status: ReportStatus
    created_at: int
    target: ReportTarget | None = None


class ReportListResponse(ModerationModel):
    items: list[ReportResponse]
    next_cursor: ReportID | None


class ModerationActionResponse(ModerationModel):
    id: ModerationActionID
    actor: ModerationAuthorResponse | None
    action: str
    target_type: str
    target_id: int
    reason: str | None
    created_at: int


class ModerationActionListResponse(ModerationModel):
    items: list[ModerationActionResponse]
    next_cursor: ModerationActionID | None


class ModerationOperationOkResponse(ModerationModel):
    ok: bool = True
