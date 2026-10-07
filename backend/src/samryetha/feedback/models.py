"""Typed commands and results for the Feedback domain."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class FeedbackType(StrEnum):
    Bug = "bug"
    Suggestion = "suggestion"


class FeedbackUrgency(StrEnum):
    Urgent = "urgent"
    Normal = "normal"


class FeedbackStatus(StrEnum):
    Open = "open"
    Done = "done"
    Expired = "expired"


class AgentRole(StrEnum):
    Read = "read"
    Write = "write"


class FeedbackModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class FeedbackRequest(FeedbackModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class FeedbackBody(FeedbackRequest):
    project_id: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    detail: Annotated[str, Field(max_length=5000)] | None = None
    type: FeedbackType
    urgency: FeedbackUrgency | None = None


class FeedbackPatch(FeedbackRequest):
    title: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    detail: Annotated[str, Field(max_length=5000)] | None = None
    type: FeedbackType | None = None
    urgency: FeedbackUrgency | None = None


class StatusBody(FeedbackRequest):
    status: FeedbackStatus


class AgentStatusBody(FeedbackRequest):
    status: Literal[FeedbackStatus.Done, FeedbackStatus.Open]


class CommentBody(FeedbackRequest):
    body: Annotated[str, Field(min_length=1, max_length=5000)]
    parent_comment_id: Annotated[int, Field(ge=1)] | None = None


class CommentPatch(FeedbackRequest):
    body: Annotated[str, Field(min_length=1, max_length=5000)]


class ProjectBody(FeedbackRequest):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(max_length=500)] | None = None


class ProjectPatch(FeedbackRequest):
    name: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None


class MemberInput(FeedbackRequest):
    user_id: Annotated[int, Field(ge=1)]
    is_programmer: bool


class MembersBody(FeedbackRequest):
    members: list[MemberInput] = Field(max_length=500)


class KeyBody(FeedbackRequest):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    role: AgentRole
    project_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)


class KeyEnabledBody(FeedbackRequest):
    enabled: bool


class AuthorRef(FeedbackModel):
    id: int
    username: str
    handle: str
    display_name: str


class FeedbackItemResponse(FeedbackModel):
    id: int
    seq: int
    project_id: int
    author: AuthorRef | None
    title: str
    detail: str
    type: FeedbackType
    urgency: FeedbackUrgency
    status: FeedbackStatus
    closed_at: int | None
    edited_at: int | None
    created_at: int
    updated_at: int


class FeedbackListResponse(FeedbackModel):
    items: list[FeedbackItemResponse]
    can_manage: bool


class MemberResponse(FeedbackModel):
    user_id: int
    username: str
    handle: str
    display_name: str
    is_programmer: bool
    joined_at: int


class MyProjectResponse(FeedbackModel):
    id: int
    name: str
    description: str
    member_count: int
    is_programmer: bool
    created_at: int


class ProjectAdminResponse(FeedbackModel):
    id: int
    name: str
    description: str
    members: list[MemberResponse]
    created_at: int


class CommentResponse(FeedbackModel):
    id: int
    item_id: int
    parent_comment_id: int | None
    author: AuthorRef | None
    body: str
    is_deleted: bool
    created_at: int
    updated_at: int


class AgentKeyResponse(FeedbackModel):
    id: int
    name: str
    prefix: str
    role: AgentRole
    project_ids: list[int]
    enabled: bool
    last_used_at: int | None
    created_at: int


class AgentProjectResponse(FeedbackModel):
    id: int
    name: str
    description: str


class AgentSummaryResponse(FeedbackModel):
    open: int
    done: int
    expired: int


class AgentTasksResponse(FeedbackModel):
    items: list[FeedbackItemResponse]
    summary: AgentSummaryResponse


class MyProjectsResponse(FeedbackModel):
    items: list[MyProjectResponse]


class ProjectsAdminListResponse(FeedbackModel):
    items: list[ProjectAdminResponse]


class CommentsListResponse(FeedbackModel):
    items: list[CommentResponse]


class AgentKeysListResponse(FeedbackModel):
    items: list[AgentKeyResponse]


class AgentKeyCreatedResponse(FeedbackModel):
    key: str
    key_row: AgentKeyResponse


class AgentProjectsResponse(FeedbackModel):
    items: list[AgentProjectResponse]


class OperationOkResponse(FeedbackModel):
    ok: bool


class RestartRequiredResponse(OperationOkResponse):
    restart_required: bool


class BackupSettingsResponse(FeedbackModel):
    backup_cron: str
    backup_keep: int


class BackupFileResponse(FeedbackModel):
    name: str
    size: int
    created_at: int


class BackupsResponse(FeedbackModel):
    backups: list[BackupFileResponse]
    settings: BackupSettingsResponse


class BackupCreatedResponse(FeedbackModel):
    backup: BackupFileResponse


class AgentEndpointResponse(FeedbackModel):
    method: str
    path: str
    auth: str
    query: str | None = None
    body: str | None = None


class AgentIndexResponse(FeedbackModel):
    name: str
    version: str
    auth: str
    endpoints: dict[str, AgentEndpointResponse]


class ProjectAuthz(TypedDict):
    id: int
    projectId: int


class ItemAuthz(TypedDict):
    id: int
    projectId: int
    authorId: int
    deletedAt: int | None


class CommentAuthz(TypedDict):
    id: int
    itemId: int
    projectId: int | None
    authorId: int
    deletedAt: int | None
