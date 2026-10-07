"""Typed domain and HTTP contracts for the moderation review queue."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ..core.ids import ModerationQueueID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ContentType(StrEnum):
    Discussion = "discussion"
    Reply = "reply"
    Profile = "profile"
    Message = "message"
    Attachment = "attachment"


class ReviewState(StrEnum):
    Pending = "pending"
    Approved = "approved"
    Rejected = "rejected"


class QueueStatus(StrEnum):
    Pending = "pending"
    Approved = "approved"
    Rejected = "rejected"
    All = "all"


class ModerationDecision(StrEnum):
    Allow = "allow"
    Review = "review"
    Block = "block"


class Resolution(StrEnum):
    PublishedByAI = "published_by_ai"
    PublishedByHuman = "published_by_human"
    Blocked = "blocked"
    BlockedByMachine = "blocked_by_machine"


class ResolutionFilter(StrEnum):
    Awaiting = "awaiting"
    PublishedByAI = "published_by_ai"
    PublishedByHuman = "published_by_human"
    Blocked = "blocked"
    BlockedByMachine = "blocked_by_machine"


class QueueModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class DecideBody(QueueModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")
    note: str | None = Field(default=None, max_length=1000)


class QueueAuthorResponse(QueueModel):
    id: UserID
    username: str
    display_name: str


class QueueCountsResponse(QueueModel):
    pending: int
    approved: int
    rejected: int
    awaiting: int
    ai_published: int
    ai_blocked: int
    blocked: int


class SignalResponse(QueueModel):
    rule: str
    weight: int
    detail: str | None = None


class RecheckResponse(QueueModel):
    at: int
    decision: ModerationDecision
    score: int
    source: str
    signals: list[SignalResponse]
    note: str
    published: bool


class QueueItemResponse(QueueModel):
    id: ModerationQueueID
    content_type: ContentType
    content_id: int
    author: QueueAuthorResponse | None
    excerpt: str
    excerpt_restricted: bool
    decision: ModerationDecision
    score: int
    signals: list[SignalResponse]
    created_at: int | None
    review_state: ReviewState
    reviewer: QueueAuthorResponse | None
    review_note: str | None
    reviewed_at: int | None
    href: str | None
    hold_until: int | None
    resolution: Resolution | None
    resolved_at: int | None
    resolved_by_ai: bool
    overturned: bool
    recheck: RecheckResponse | None
    awaiting_human: bool
    needs_uphold: bool
    needs_release: bool


class QueueListResponse(QueueModel):
    items: list[QueueItemResponse]
    next_cursor: str | None
    counts: QueueCountsResponse


class DecisionResponse(QueueModel):
    ok: bool = True
    review_state: ReviewState
    resolution: Resolution
    overturned: bool


class RetainedItemResponse(QueueModel):
    id: ModerationQueueID
    content_type: ContentType
    content_id: int
    author_id: UserID
    author: QueueAuthorResponse | None
    excerpt: str
    title: str | None
    body: str
    from_snapshot: bool
    content_exists: bool
    decision: ModerationDecision
    score: int
    signals: list[SignalResponse]
    resolution: Resolution | None
    resolved_at: int | None
    reviewer_id: UserID | None
    review_note: str | None
    overturned: bool
    recheck: RecheckResponse | None
    created_at: int | None
    href: str | None


class RetainedListResponse(QueueModel):
    items: list[RetainedItemResponse]
    next_cursor: ModerationQueueID | None
    total: int


class FinalizedItemResponse(QueueModel):
    id: ModerationQueueID
    content_type: ContentType
    content_id: int
    resolution: Resolution
    published: bool


class FinalizeResponse(QueueModel):
    ok: bool = True
    count: int
    published: int
    blocked: int
    items: list[FinalizedItemResponse]


@dataclass(frozen=True, slots=True)
class DecisionResult:
    response: DecisionResponse
    row: "QueueRecord"
    should_notify: bool


if TYPE_CHECKING:
    from .repository import QueueRecord
