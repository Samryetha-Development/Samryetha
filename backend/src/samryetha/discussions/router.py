"""/api/discussions + /api/replies — 镜像 backend/src/discussions/routes.ts。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Path, Query
from .. import discussions as d
from .. import attachments as att
from ..core.config import Settings
from ..core.deps import CurrentUser, DbConn, get_current_user, get_settings_dep, get_storage, require_active_user
from ..discussions.models import (
    CreateDiscussionBody,
    CreateReplyBody,
    DeleteDiscussionBody,
    DiscussionAttachmentResponse,
    DiscussionDetailResponse,
    DiscussionFeed,
    DiscussionFeedQuery,
    DiscussionListResponse,
    DiscussionOperationOkResponse,
    FollowingResponse,
    LockedResponse,
    PinnedResponse,
    PreviewBody,
    PreviewResponse,
    ReplyListResponse,
    ReplyResponse,
    SavedResponse,
    UpdateDiscussionBody,
    UpdateReplyBody,
    DiscussionSort,
)
from ..core.errors import validation_failed
from ..adapters.markdown import render_body
from ..adapters.storage import Storage

router = APIRouter()

DiscussionId = Annotated[int, Path(ge=1)]
ReplyId = Annotated[int, Path(ge=1)]


def _feed_query(
    cursor: str | None,
    limit: int,
    feed: str | None = None,
    board: str | None = None,
    sort: str | None = None,
) -> DiscussionFeedQuery:
    return DiscussionFeedQuery(
        cursor=cursor,
        limit=limit,
        feed=DiscussionFeed(feed) if feed is not None else DiscussionFeed.Latest,
        sort=DiscussionSort(sort) if sort is not None else DiscussionSort.Date,
        board_slug=board,
    )


@router.get("/api/discussions", response_model=DiscussionListResponse)
def list_discussions(
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    feed: Literal["latest", "followed"] = Query(default="latest"),
    sort: Literal["date", "replies"] = Query(default="date"),
    board: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DiscussionListResponse:
    return DiscussionListResponse.model_validate(
        d.list_discussions(conn, viewer, _feed_query(cursor, limit, feed, board, sort))
    )


@router.post("/api/discussions", status_code=201, response_model=DiscussionDetailResponse)
def create_discussion(
    body: CreateDiscussionBody,
    conn: DbConn,
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(require_active_user),
    storage: Storage = Depends(get_storage),
) -> DiscussionDetailResponse:
    result = d.create_discussion(conn, user, body, settings)
    response = DiscussionDetailResponse.model_validate(result)
    response.attachments = [
        DiscussionAttachmentResponse.model_validate(item, from_attributes=True)
        for item in att.list_for_discussion(conn, response.id, storage, user)
    ]
    return response


@router.post("/api/discussions/preview", response_model=PreviewResponse)
def preview_body(
    body: PreviewBody,
    user: CurrentUser = Depends(require_active_user),
) -> PreviewResponse:
    return PreviewResponse(body_html=render_body(body.body_markdown, body.body_format.value))


@router.get("/api/discussions/{discussion_id}", response_model=DiscussionDetailResponse)
def get_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
) -> DiscussionDetailResponse:
    result = d.get_discussion(conn, viewer, discussion_id)
    response = DiscussionDetailResponse.model_validate(result)
    response.attachments = [
        DiscussionAttachmentResponse.model_validate(item, from_attributes=True)
        for item in att.list_for_discussion(conn, discussion_id, storage, viewer)
    ]
    return response


@router.patch("/api/discussions/{discussion_id}", response_model=DiscussionDetailResponse)
def update_discussion(
    discussion_id: DiscussionId,
    body: UpdateDiscussionBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    settings: Settings = Depends(get_settings_dep),
    storage: Storage = Depends(get_storage),
) -> DiscussionDetailResponse:
    if not body.model_fields_set:
        raise validation_failed([{"field": "", "message": "Nothing to update", "code": "custom"}])
    result = d.update_discussion(conn, user, discussion_id, body, settings)
    response = DiscussionDetailResponse.model_validate(result)
    response.attachments = [
        DiscussionAttachmentResponse.model_validate(item, from_attributes=True)
        for item in att.list_for_discussion(conn, discussion_id, storage, user)
    ]
    return response


@router.delete("/api/discussions/{discussion_id}", response_model=DiscussionOperationOkResponse)
def delete_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    body: DeleteDiscussionBody | None = Body(default=None),
) -> DiscussionOperationOkResponse:
    d.delete_discussion(conn, user, discussion_id, (body.reason if body else None))
    return DiscussionOperationOkResponse(ok=True)


@router.post("/api/discussions/{discussion_id}/replies", status_code=201, response_model=ReplyResponse)
def create_reply(
    discussion_id: DiscussionId,
    body: CreateReplyBody,
    conn: DbConn,
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(require_active_user),
) -> ReplyResponse:
    return ReplyResponse.model_validate(d.create_reply(conn, user, discussion_id, body, settings))


@router.get("/api/discussions/{discussion_id}/replies", response_model=ReplyListResponse)
def list_replies(
    discussion_id: DiscussionId,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
) -> ReplyListResponse:
    return ReplyListResponse.model_validate(d.list_replies(conn, viewer, discussion_id))


@router.patch("/api/replies/{reply_id}", response_model=ReplyResponse)
def update_reply(
    reply_id: ReplyId,
    body: UpdateReplyBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    settings: Settings = Depends(get_settings_dep),
) -> ReplyResponse:
    return ReplyResponse.model_validate(
        d.update_reply(
            conn,
            user,
            reply_id,
            body.body_markdown,
            body.body_format.value if body.body_format is not None else "markdown",
            settings,
        )
    )


@router.delete("/api/replies/{reply_id}", response_model=DiscussionOperationOkResponse)
def delete_reply(
    reply_id: ReplyId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> DiscussionOperationOkResponse:
    d.delete_reply(conn, user, reply_id, None)
    return DiscussionOperationOkResponse(ok=True)


@router.post("/api/discussions/{discussion_id}/save", response_model=SavedResponse)
def save_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> SavedResponse:
    d.save(conn, user, discussion_id)
    return SavedResponse(saved=True)


@router.delete("/api/discussions/{discussion_id}/save", response_model=SavedResponse)
def unsave_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> SavedResponse:
    d.unsave(conn, user, discussion_id)
    return SavedResponse(saved=False)


@router.post("/api/discussions/{discussion_id}/follow", response_model=FollowingResponse)
def follow_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FollowingResponse:
    d.follow(conn, user, discussion_id)
    return FollowingResponse(following=True)


@router.delete("/api/discussions/{discussion_id}/follow", response_model=FollowingResponse)
def unfollow_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FollowingResponse:
    d.unfollow(conn, user, discussion_id)
    return FollowingResponse(following=False)


@router.post("/api/discussions/{discussion_id}/pin", response_model=PinnedResponse)
def pin_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> PinnedResponse:
    d.pin(conn, user, discussion_id)
    return PinnedResponse(pinned=True)


@router.post("/api/discussions/{discussion_id}/lock", response_model=LockedResponse)
def lock_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> LockedResponse:
    d.lock(conn, user, discussion_id)
    return LockedResponse(locked=True)
