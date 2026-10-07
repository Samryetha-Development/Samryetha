"""Typed domain and HTTP contracts for private discussion drafts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..core.ids import AttachmentID, DraftID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class DraftBodyFormat(StrEnum):
    Markdown = "markdown"
    Text = "text"


@dataclass(frozen=True, slots=True)
class DraftRecord:
    id: DraftID
    author_id: UserID
    board_slug: str | None
    title: str
    body_markdown: str
    body_format: DraftBodyFormat
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class DraftAttachmentRecord:
    id: AttachmentID
    object_key: str
    original_filename: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DraftAttachment:
    id: AttachmentID
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_image: bool
    download_url: str


@dataclass(frozen=True, slots=True)
class DraftSummary:
    id: DraftID
    board_slug: str | None
    title: str
    preview: str
    body_format: DraftBodyFormat
    attachment_count: int
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class DraftDetail:
    id: DraftID
    board_slug: str | None
    title: str
    body_markdown: str
    body_format: DraftBodyFormat
    attachments: tuple[DraftAttachment, ...]
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class DraftPage:
    items: tuple[DraftSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SaveDraft:
    board_slug: str | None
    title: str
    body_markdown: str
    body_format: DraftBodyFormat
    attachment_ids: tuple[AttachmentID, ...]


class DraftHttpModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class SaveDraftBody(DraftHttpModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")

    board_slug: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    title: Annotated[str, Field(max_length=100)] = ""
    body_markdown: Annotated[str, Field(max_length=40000)] = ""
    body_format: DraftBodyFormat = DraftBodyFormat.Text
    attachment_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=10)

    def to_command(self) -> SaveDraft:
        return SaveDraft(
            board_slug=self.board_slug,
            title=self.title,
            body_markdown=self.body_markdown,
            body_format=self.body_format,
            attachment_ids=tuple(AttachmentID(value) for value in self.attachment_ids),
        )


class DraftAttachmentResponse(DraftHttpModel):
    id: int
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_image: bool
    download_url: str


class DraftSummaryResponse(DraftHttpModel):
    id: int
    board_slug: str | None
    title: str
    preview: str
    body_format: DraftBodyFormat
    attachment_count: int
    created_at: int
    updated_at: int


class DraftDetailResponse(DraftHttpModel):
    id: int
    board_slug: str | None
    title: str
    body_markdown: str
    body_format: DraftBodyFormat
    attachments: list[DraftAttachmentResponse]
    created_at: int
    updated_at: int


class DraftListResponse(DraftHttpModel):
    items: list[DraftSummaryResponse]
    next_cursor: str | None


class DraftOperationOkResponse(DraftHttpModel):
    ok: bool = True
