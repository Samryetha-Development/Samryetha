"""/api/discussions + /api/replies — 镜像 backend/src/discussions/routes.ts。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import discussions as d
from ..deps import CurrentUser, DbConn, get_current_user, require_active_user
from ..errors import validation_failed

router = APIRouter()

DiscussionId = Annotated[int, Path(ge=1)]
ReplyId = Annotated[int, Path(ge=1)]


class CreateDiscussionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    boardSlug: Annotated[str, Field(min_length=1, max_length=50)]
    title: Annotated[str, Field(min_length=3, max_length=100)]
    bodyMarkdown: Annotated[str, Field(min_length=1, max_length=40000)]
    bodyFormat: Literal["markdown", "text"] = "markdown"
    attachmentIds: list[int] | None = Field(default=None, max_length=10)


class UpdateDiscussionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Annotated[str, Field(min_length=3, max_length=100)] | None = None
    bodyMarkdown: Annotated[str, Field(min_length=1, max_length=40000)] | None = None
    bodyFormat: Literal["markdown", "text"] | None = None


class DeleteDiscussionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: Annotated[str, Field(max_length=500)] | None = None


class CreateReplyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bodyMarkdown: Annotated[str, Field(min_length=1, max_length=5000)]
    bodyFormat: Literal["markdown", "text"] = "markdown"
    parentReplyId: int | None = Field(default=None, ge=1)


class UpdateReplyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bodyMarkdown: Annotated[str, Field(min_length=1, max_length=5000)]
    bodyFormat: Literal["markdown", "text"] | None = None


def _feed_opts(cursor: str | None, limit: int, feed: str | None = None, board: str | None = None) -> dict:
    opts: dict = {"cursor": cursor, "limit": limit}
    if feed is not None:
        opts["feed"] = feed
    if board is not None:
        opts["boardSlug"] = board
    return opts


@router.get("/api/discussions")
def list_discussions(
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    feed: Literal["latest", "followed"] = Query(default="latest"),
    board: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return d.list_discussions(conn, viewer, _feed_opts(cursor, limit, feed, board))


@router.post("/api/discussions", status_code=201)
def create_discussion(
    body: CreateDiscussionBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return d.create_discussion(conn, user, body.model_dump(exclude_none=True))


@router.get("/api/discussions/{discussion_id}")
def get_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
) -> dict:
    return d.get_discussion(conn, viewer, discussion_id)


@router.patch("/api/discussions/{discussion_id}")
def update_discussion(
    discussion_id: DiscussionId,
    body: UpdateDiscussionBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise validation_failed([{"field": "", "message": "Nothing to update", "code": "custom"}])
    return d.update_discussion(conn, user, discussion_id, patch)


@router.delete("/api/discussions/{discussion_id}")
def delete_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    body: DeleteDiscussionBody | None = Body(default=None),
) -> dict:
    d.delete_discussion(conn, user, discussion_id, (body.reason if body else None))
    return {"ok": True}


@router.post("/api/discussions/{discussion_id}/replies", status_code=201)
def create_reply(
    discussion_id: DiscussionId,
    body: CreateReplyBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return d.create_reply(conn, user, discussion_id, body.model_dump(exclude_none=True))


@router.get("/api/discussions/{discussion_id}/replies")
def list_replies(
    discussion_id: DiscussionId,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
) -> dict:
    return d.list_replies(conn, viewer, discussion_id)


@router.patch("/api/replies/{reply_id}")
def update_reply(
    reply_id: ReplyId,
    body: UpdateReplyBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return d.update_reply(conn, user, reply_id, body.bodyMarkdown, body.bodyFormat or "markdown")


@router.delete("/api/replies/{reply_id}")
def delete_reply(
    reply_id: ReplyId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.delete_reply(conn, user, reply_id, None)
    return {"ok": True}


@router.post("/api/discussions/{discussion_id}/save")
def save_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.save(conn, user, discussion_id)
    return {"saved": True}


@router.delete("/api/discussions/{discussion_id}/save")
def unsave_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.unsave(conn, user, discussion_id)
    return {"saved": False}


@router.post("/api/discussions/{discussion_id}/follow")
def follow_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.follow(conn, user, discussion_id)
    return {"following": True}


@router.delete("/api/discussions/{discussion_id}/follow")
def unfollow_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.unfollow(conn, user, discussion_id)
    return {"following": False}


@router.post("/api/discussions/{discussion_id}/pin")
def pin_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.pin(conn, user, discussion_id)
    return {"pinned": True}


@router.post("/api/discussions/{discussion_id}/lock")
def lock_discussion(
    discussion_id: DiscussionId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    d.lock(conn, user, discussion_id)
    return {"locked": True}
