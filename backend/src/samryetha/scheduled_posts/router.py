"""Account-private schedule management and the current user's published posts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..adapters.storage import Storage
from ..core.config import Settings
from ..core.deps import CurrentUser, DbConn, get_settings_dep, get_storage, require_active_user
from ..core.errors import bad_request
from ..discussions.models import DiscussionListResponse, PageQuery
from ..discussions.service import DiscussionService
from .models import CancelScheduleResponse, ReschedulePostBody, SchedulePostBody, ScheduledPostListResponse, ScheduledPostResponse
from .service import ScheduledPostService

router = APIRouter()
ActiveUser = Annotated[CurrentUser, Depends(require_active_user)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
StorageDep = Annotated[Storage, Depends(get_storage)]
PostId = Annotated[int, Path(ge=1)]


@router.get("/api/me/posts", response_model=DiscussionListResponse)
def own_posts(conn: DbConn, user: ActiveUser, cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50)) -> DiscussionListResponse:
    return DiscussionService(conn).list_by_author(user, user.id, PageQuery(cursor=cursor, limit=limit))


@router.get("/api/scheduled-posts", response_model=ScheduledPostListResponse)
def list_schedules(conn: DbConn, user: ActiveUser, settings: SettingsDep, storage: StorageDep,
                   cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50)) -> ScheduledPostListResponse:
    if cursor is not None and (len(cursor) > 18 or not cursor.isascii() or not cursor.isdigit() or int(cursor) < 1):
        raise bad_request("Invalid cursor")
    return ScheduledPostService(conn, settings, storage).list(user, int(cursor) if cursor else None, limit)


@router.post("/api/scheduled-posts", status_code=201, response_model=ScheduledPostResponse)
def create_schedule(body: SchedulePostBody, conn: DbConn, user: ActiveUser, settings: SettingsDep, storage: StorageDep) -> ScheduledPostResponse:
    return ScheduledPostService(conn, settings, storage).create(user, body)


@router.get("/api/scheduled-posts/{post_id}", response_model=ScheduledPostResponse)
def get_schedule(post_id: PostId, conn: DbConn, user: ActiveUser, settings: SettingsDep, storage: StorageDep) -> ScheduledPostResponse:
    return ScheduledPostService(conn, settings, storage).owned(user, post_id)


@router.patch("/api/scheduled-posts/{post_id}", response_model=ScheduledPostResponse)
def reschedule(post_id: PostId, body: ReschedulePostBody, conn: DbConn, user: ActiveUser, settings: SettingsDep, storage: StorageDep) -> ScheduledPostResponse:
    return ScheduledPostService(conn, settings, storage).reschedule(user, post_id, body.scheduled_at)


@router.delete("/api/scheduled-posts/{post_id}", response_model=CancelScheduleResponse)
def cancel_schedule(post_id: PostId, conn: DbConn, user: ActiveUser, settings: SettingsDep, storage: StorageDep) -> CancelScheduleResponse:
    return CancelScheduleResponse(draft_id=ScheduledPostService(conn, settings, storage).cancel(user, post_id))
