"""Typed HTTP boundary for the moderation review queue."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from .. import review_queue as service
from ..automod import finalize_pending
from ..core.config import Settings
from ..core.deps import CurrentUser, DbConn, get_settings_dep, require_admin, require_moderator
from ..core.ids import ModerationQueueID
from ..core.records import require_bool, require_int, require_str
from ..review_queue.models import (
    ContentType, DecideBody, DecisionResponse, FinalizedItemResponse, FinalizeResponse,
    QueueListResponse, QueueStatus, Resolution, ResolutionFilter,
    RetainedListResponse,
)

router = APIRouter()

QueueIDPath = Annotated[ModerationQueueID, Path(ge=1)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ModeratorDep = Annotated[CurrentUser, Depends(require_moderator)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]

@router.get("/api/admin/moderation/queue", response_model=QueueListResponse)
def list_queue(
    conn: DbConn,
    _settings: SettingsDep,
    mod: ModeratorDep,
    status: Annotated[QueueStatus, Query()] = QueueStatus.Pending,
    type: Annotated[ContentType | None, Query()] = None,
    resolution: Annotated[ResolutionFilter | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> QueueListResponse:
    return service.list_queue(
        conn, viewer=mod, status=status, content_type=type,
        resolution=resolution, cursor=cursor, limit=limit,
    )

@router.post("/api/admin/moderation/queue/{queue_id}/approve", response_model=DecisionResponse)
def approve(
    queue_id: QueueIDPath,
    body: DecideBody,
    conn: DbConn,
    settings: SettingsDep,
    mod: ModeratorDep,
) -> DecisionResponse:
    result = service.decide(conn, mod, queue_id, approve=True, note=body.note)
    if result.should_notify and settings.automod_notify_author:
        service.notify_author(conn, result.row, approved=True, note=body.note)
    return result.response

@router.post("/api/admin/moderation/queue/{queue_id}/reject", response_model=DecisionResponse)
def reject(
    queue_id: QueueIDPath,
    body: DecideBody,
    conn: DbConn,
    settings: SettingsDep,
    mod: ModeratorDep,
) -> DecisionResponse:
    result = service.decide(conn, mod, queue_id, approve=False, note=body.note)
    if result.should_notify and settings.automod_notify_author:
        service.notify_author(conn, result.row, approved=False, note=body.note)
    return result.response

@router.post("/api/admin/moderation/finalize", response_model=FinalizeResponse)
def finalize_now(conn: DbConn, settings: SettingsDep, _admin: AdminDep) -> FinalizeResponse:
    finalized = finalize_pending(conn, settings)
    items = [
        FinalizedItemResponse(
            id=ModerationQueueID(require_int(item.get("id"), "id")),
            content_type=ContentType(require_str(item.get("contentType"), "contentType")),
            content_id=require_int(item.get("contentId"), "contentId"),
            resolution=Resolution(require_str(item.get("resolution"), "resolution")),
            published=require_bool(item.get("published"), "published"),
        )
        for item in finalized
    ]
    published = sum(item.published for item in items)
    return FinalizeResponse(
        count=len(items), published=published, blocked=len(items) - published, items=items,
    )

@router.get("/api/admin/moderation/retained", response_model=RetainedListResponse)
def list_retained(
    conn: DbConn,
    _settings: SettingsDep,
    _admin: AdminDep,
    type: Annotated[ContentType | None, Query()] = None,
    cursor: Annotated[ModerationQueueID | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RetainedListResponse:
    return service.list_retained(conn, content_type=type, cursor=cursor, limit=limit)
