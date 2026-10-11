"""Scheduled post HTTP and persistence contracts (timestamps in milliseconds)."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StrictBool

from ..discussions.models import CreateDiscussionBody, DiscussionHttpModel


class ScheduleStatus(StrEnum):
    Pending = "pending"
    Publishing = "publishing"
    Published = "published"
    Cancelled = "cancelled"
    Failed = "failed"


class SchedulePostBody(CreateDiscussionBody):
    scheduled_at: Annotated[int, Field(strict=True, ge=1)]
    can_modify: StrictBool = True


class ReschedulePostBody(DiscussionHttpModel):
    scheduled_at: Annotated[int, Field(strict=True, ge=1)]


class ScheduledPostResponse(DiscussionHttpModel):
    id: int
    draft_id: int | None
    discussion_id: int | None
    board_slug: str
    title: str
    preview: str
    scheduled_at: int
    can_modify: bool
    status: ScheduleStatus
    last_error: str | None
    created_at: int
    updated_at: int


class ScheduledPostRecord(ScheduledPostResponse):
    author_id: int


class ScheduledPostListResponse(DiscussionHttpModel):
    items: list[ScheduledPostResponse]
    next_cursor: str | None


class CancelScheduleResponse(DiscussionHttpModel):
    draft_id: int


class ScheduledAuthor(DiscussionHttpModel):
    id: int
    role: str
    status: str
