"""Private draft CRUD; active account owners only, including for administrators."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import drafts
from ..deps import CurrentUser, DbConn, get_storage, require_active_user

router = APIRouter()
DraftId = Annotated[int, Path(ge=1)]


class SaveDraftBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    boardSlug: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    title: Annotated[str, Field(max_length=100)] = ""
    bodyMarkdown: Annotated[str, Field(max_length=40000)] = ""
    bodyFormat: Literal["markdown", "text"] = "text"
    attachmentIds: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=10)


@router.get("/api/drafts")
def list_drafts(conn: DbConn, user: CurrentUser = Depends(require_active_user),
                cursor: Annotated[int, Query(ge=1)] | None = None, limit: int = Query(default=20, ge=1, le=50)) -> dict:
    return drafts.list_drafts(conn, user, cursor, limit)


@router.post("/api/drafts", status_code=201)
def create_draft(body: SaveDraftBody, conn: DbConn, user: CurrentUser = Depends(require_active_user),
                 storage: object = Depends(get_storage)) -> dict:
    return drafts.save_draft(conn, user, body.model_dump(), storage)


@router.get("/api/drafts/{draft_id}")
def get_draft(draft_id: DraftId, conn: DbConn, user: CurrentUser = Depends(require_active_user),
              storage: object = Depends(get_storage)) -> dict:
    return drafts.get_draft(conn, user, draft_id, storage)


@router.put("/api/drafts/{draft_id}")
def update_draft(draft_id: DraftId, body: SaveDraftBody, conn: DbConn,
                 user: CurrentUser = Depends(require_active_user), storage: object = Depends(get_storage)) -> dict:
    return drafts.save_draft(conn, user, body.model_dump(), storage, draft_id)


@router.delete("/api/drafts/{draft_id}")
def delete_draft(draft_id: DraftId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    drafts.delete_draft(conn, user, draft_id)
    return {"ok": True}
