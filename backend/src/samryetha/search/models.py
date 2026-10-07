"""Typed search commands, results, and HTTP contracts."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from ..discussions.models import ModerationStatus
from ..ids import BoardID, DiscussionID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


@dataclass(frozen=True, slots=True)
class SearchOptions:
    query: str
    board_slug: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class SearchBoard:
    id: BoardID
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class SearchAuthor:
    id: UserID
    username: str
    handle: str
    display_name: str


@dataclass(frozen=True, slots=True)
class SearchItem:
    id: DiscussionID
    title: str
    preview: str
    board: SearchBoard
    author: SearchAuthor
    reply_count: int
    is_pinned: bool
    is_locked: bool
    moderation_status: ModerationStatus
    created_at: int
    last_activity_at: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: tuple[SearchItem, ...]
    total: int


class SearchHttpModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class SearchBoardResponse(SearchHttpModel):
    id: BoardID
    slug: str
    name: str


class SearchAuthorResponse(SearchHttpModel):
    id: UserID
    username: str
    handle: str
    display_name: str


class SearchItemResponse(SearchHttpModel):
    id: DiscussionID
    title: str
    preview: str
    board: SearchBoardResponse
    author: SearchAuthorResponse
    reply_count: int
    is_pinned: bool
    is_locked: bool
    moderation_status: ModerationStatus
    created_at: int
    last_activity_at: int


class SearchResultResponse(SearchHttpModel):
    items: list[SearchItemResponse]
    total: int
