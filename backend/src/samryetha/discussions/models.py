"""Typed HTTP contracts for discussions and replies."""

from __future__ import annotations

from enum import StrEnum
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from ..polls.models import PollInput, PollResponse


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class BodyFormat(StrEnum):
    Markdown = "markdown"
    Text = "text"


class DiscussionSort(StrEnum):
    Date = "date"
    Replies = "replies"


class DiscussionFeed(StrEnum):
    Latest = "latest"
    Followed = "followed"


@dataclass(frozen=True, slots=True)
class PageQuery:
    cursor: str | None = None
    limit: int = 20


@dataclass(frozen=True, slots=True)
class DiscussionFeedQuery(PageQuery):
    feed: DiscussionFeed = DiscussionFeed.Latest
    sort: DiscussionSort = DiscussionSort.Date
    board_slug: str | None = None


class LegacyPageOptions(TypedDict, total=False):
    cursor: str | None
    limit: int


class LegacyDiscussionFeedOptions(LegacyPageOptions, total=False):
    feed: Literal["latest", "followed"]
    sort: Literal["date", "replies"]
    boardSlug: str | None


class DiscussionHttpModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    def __getitem__(self, key: str) -> object:
        """Read-only compatibility for legacy service callers during migration."""
        return self.model_dump(by_alias=True)[key]


class DiscussionRequestModel(DiscussionHttpModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class CreateDiscussionBody(DiscussionRequestModel):
    board_slug: Annotated[str, Field(min_length=1, max_length=50)]
    title: Annotated[str, Field(max_length=100)] | None = None
    body_markdown: Annotated[str, Field(min_length=1, max_length=40000)]
    body_format: BodyFormat = BodyFormat.Markdown
    attachment_ids: list[Annotated[int, Field(ge=1)]] | None = Field(default=None, max_length=10)
    draft_id: Annotated[int, Field(ge=1)] | None = None
    poll: PollInput | None = None


class UpdateDiscussionBody(DiscussionRequestModel):
    poll: PollInput | None = None
    title: Annotated[str, Field(max_length=100)] | None = None
    body_markdown: Annotated[str, Field(min_length=1, max_length=40000)] | None = None
    body_format: BodyFormat | None = None


class PreviewBody(DiscussionRequestModel):
    body_markdown: Annotated[str, Field(max_length=40000)]
    body_format: BodyFormat = BodyFormat.Markdown


class DeleteDiscussionBody(DiscussionRequestModel):
    reason: Annotated[str, Field(max_length=500)] | None = None


class CreateReplyBody(DiscussionRequestModel):
    body_markdown: Annotated[str, Field(min_length=1, max_length=5000)]
    body_format: BodyFormat = BodyFormat.Markdown
    parent_reply_id: Annotated[int, Field(ge=1)] | None = None


class UpdateReplyBody(DiscussionRequestModel):
    body_markdown: Annotated[str, Field(min_length=1, max_length=5000)]
    body_format: BodyFormat | None = None


class AuthorResponse(DiscussionHttpModel):
    id: int
    username: str
    handle: str
    display_name: str


class BoardRefResponse(DiscussionHttpModel):
    id: int
    slug: str
    name: str


class ThreadSummaryResponse(DiscussionHttpModel):
    id: int
    title: str
    preview: str
    board: BoardRefResponse
    author: AuthorResponse
    reply_count: int
    is_pinned: bool
    is_locked: bool
    created_at: int
    last_activity_at: int


class DiscussionPermissionsResponse(DiscussionHttpModel):
    update: bool
    delete: bool


class DiscussionAttachmentResponse(DiscussionHttpModel):
    id: int
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_image: bool
    download_url: str


class DiscussionDetailResponse(ThreadSummaryResponse):
    body_markdown: str
    body_html: str | None
    body_format: BodyFormat
    save_count: int
    is_saved: bool
    is_following: bool
    can: DiscussionPermissionsResponse
    attachments: list[DiscussionAttachmentResponse] | None = None
    poll: PollResponse | None = None


class ReplyResponse(DiscussionHttpModel):
    id: int
    discussion_id: int
    parent_reply_id: int | None
    author: AuthorResponse
    body_markdown: str
    body_html: str | None
    body_format: BodyFormat
    is_deleted: bool
    created_at: int
    updated_at: int


class DiscussionListResponse(DiscussionHttpModel):
    items: list[ThreadSummaryResponse]
    next_cursor: str | None


class ReplyListResponse(DiscussionHttpModel):
    items: list[ReplyResponse]


class AuthoredReplyResponse(ReplyResponse):
    discussion_title: str


class AuthoredReplyListResponse(DiscussionHttpModel):
    items: list[AuthoredReplyResponse]
    next_cursor: str | None


class PreviewResponse(DiscussionHttpModel):
    body_html: str


class DiscussionOperationOkResponse(DiscussionHttpModel):
    ok: bool


class SavedResponse(DiscussionHttpModel):
    saved: bool


class FollowingResponse(DiscussionHttpModel):
    following: bool


class PinnedResponse(DiscussionHttpModel):
    pinned: bool


class LockedResponse(DiscussionHttpModel):
    locked: bool
