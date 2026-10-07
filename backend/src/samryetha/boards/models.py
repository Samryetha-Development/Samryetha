"""Typed contracts for boards and board membership."""

from enum import StrEnum
from typing import Annotated, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from ..ids import BoardID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class BoardVisibility(StrEnum):
    Public = "public"
    Members = "members"
    Private = "private"


class PostingPolicy(StrEnum):
    Everyone = "everyone"
    Members = "members"
    Moderators = "moderators"


class BoardMemberRole(StrEnum):
    Member = "member"
    Moderator = "moderator"


class BoardModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class BoardRequest(BoardModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class BoardBody(BoardRequest):
    name: Annotated[str, Field(min_length=1, max_length=60)]
    slug: Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")]
    description: Annotated[str, Field(max_length=500)] | None = None
    visibility: BoardVisibility | None = None
    posting_policy: PostingPolicy | None = None


class BoardPatch(BoardRequest):
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    visibility: BoardVisibility | None = None
    posting_policy: PostingPolicy | None = None


class DeleteBoardBody(BoardRequest):
    reason: Annotated[str, Field(max_length=500)] | None = None


class MemberRoleBody(BoardRequest):
    role: BoardMemberRole


class BoardSummaryResponse(BoardModel):
    id: BoardID
    slug: str
    name: str
    description: str
    visibility: BoardVisibility
    posting_policy: PostingPolicy
    member_count: int
    today_activity: int
    current_user_role: BoardMemberRole | None


class BoardListResponse(BoardModel):
    items: list[BoardSummaryResponse]


class BoardMemberResponse(BoardModel):
    id: UserID
    username: str
    handle: str
    display_name: str
    role: BoardMemberRole


class BoardMemberListResponse(BoardModel):
    items: list[BoardMemberResponse]


class MembershipResponse(BoardModel):
    member: bool


class BoardOperationOkResponse(BoardModel):
    ok: bool = True


class BoardAuthz(TypedDict):
    id: int
    visibility: str
    postingPolicy: str
    slug: str
