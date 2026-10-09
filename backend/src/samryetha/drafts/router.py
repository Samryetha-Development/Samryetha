"""Private draft CRUD; active account owners only, including for administrators."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from .. import drafts
from ..core.deps import CurrentUser, DbConn, get_storage, require_active_user
from ..drafts.models import (
    DraftAttachmentResponse,
    DraftDetail,
    DraftDetailResponse,
    DraftListResponse,
    DraftOperationOkResponse,
    DraftSummary,
    DraftSummaryResponse,
    SaveDraftBody,
)
from ..adapters.storage import Storage

router = APIRouter()
DraftPathID = Annotated[int, Path(ge=1)]


def _summary_response(item: DraftSummary) -> DraftSummaryResponse:
    return DraftSummaryResponse(
        id=item.id,
        board_slug=item.board_slug,
        title=item.title,
        preview=item.preview,
        body_format=item.body_format,
        attachment_count=item.attachment_count,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _detail_response(item: DraftDetail) -> DraftDetailResponse:
    return DraftDetailResponse(
        id=item.id,
        board_slug=item.board_slug,
        title=item.title,
        body_markdown=item.body_markdown,
        poll=item.poll,
        body_format=item.body_format,
        attachments=[
            DraftAttachmentResponse(
                id=attachment.id,
                object_key=attachment.object_key,
                original_filename=attachment.original_filename,
                mime_type=attachment.mime_type,
                size_bytes=attachment.size_bytes,
                is_image=attachment.is_image,
                download_url=attachment.download_url,
            )
            for attachment in item.attachments
        ],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/api/drafts", response_model=DraftListResponse)
def list_drafts(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    cursor: Annotated[int, Query(ge=1)] | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DraftListResponse:
    page = drafts.DraftService(conn).list_drafts(user, cursor, limit)
    return DraftListResponse(items=[_summary_response(item) for item in page.items], next_cursor=page.next_cursor)


@router.post("/api/drafts", status_code=201, response_model=DraftDetailResponse)
def create_draft(
    body: SaveDraftBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    storage: Storage = Depends(get_storage),
) -> DraftDetailResponse:
    return _detail_response(drafts.DraftService(conn, storage=storage).save_draft(user, body.to_command()))


@router.get("/api/drafts/{draft_id}", response_model=DraftDetailResponse)
def get_draft(
    draft_id: DraftPathID,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    storage: Storage = Depends(get_storage),
) -> DraftDetailResponse:
    return _detail_response(drafts.DraftService(conn, storage=storage).get_draft(user, draft_id))


@router.put("/api/drafts/{draft_id}", response_model=DraftDetailResponse)
def update_draft(
    draft_id: DraftPathID,
    body: SaveDraftBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    storage: Storage = Depends(get_storage),
) -> DraftDetailResponse:
    return _detail_response(drafts.DraftService(conn, storage=storage).save_draft(user, body.to_command(), draft_id))


@router.delete("/api/drafts/{draft_id}", response_model=DraftOperationOkResponse)
def delete_draft(
    draft_id: DraftPathID,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> DraftOperationOkResponse:
    drafts.DraftService(conn).delete_draft(user, draft_id)
    return DraftOperationOkResponse()
