"""Typed domain records and HTTP contracts for attachments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..ids import AttachmentID, DiscussionID, UserID
from ..adapters.storage import MAX_UPLOAD_BYTES


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class AttachmentState(StrEnum):
    Pending = "pending"
    Uploaded = "uploaded"
    Attached = "attached"
    Orphaned = "orphaned"


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    id: AttachmentID
    uploader_id: UserID
    discussion_id: DiscussionID | None
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    state: AttachmentState
    created_at: int


@dataclass(frozen=True, slots=True)
class CreateAttachment:
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class AttachmentView:
    id: AttachmentID
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_image: bool
    download_url: str


@dataclass(frozen=True, slots=True)
class AttachmentDetail(AttachmentView):
    state: AttachmentState
    created_at: int


@dataclass(frozen=True, slots=True)
class PresignResult:
    attachment_id: AttachmentID
    object_key: str
    upload_url: str
    upload_method: str
    upload_headers: dict[str, str]


class AttachmentHttpModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class AttachmentRequest(AttachmentHttpModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class PresignBody(AttachmentRequest):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    mime_type: Annotated[str, Field(min_length=1, max_length=100)]
    size_bytes: Annotated[int, Field(gt=0, le=MAX_UPLOAD_BYTES)]

    def to_command(self) -> CreateAttachment:
        return CreateAttachment(
            filename=self.filename,
            mime_type=self.mime_type,
            size_bytes=self.size_bytes,
        )


class AttachmentConfigResponse(AttachmentHttpModel):
    allowed_extensions: list[str]
    max_upload_bytes: int


class PresignResponse(AttachmentHttpModel):
    attachment_id: AttachmentID
    object_key: str
    upload_url: str
    upload_method: str
    upload_headers: dict[str, str]


class AttachmentResponse(AttachmentHttpModel):
    id: AttachmentID
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    is_image: bool
    download_url: str
    state: AttachmentState
    created_at: int


class AttachmentOperationOkResponse(AttachmentHttpModel):
    ok: bool
