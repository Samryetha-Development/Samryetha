"""/api/moderation — 镜像 backend/src/moderation/routes.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import moderation as service
from ..deps import CurrentUser, DbConn, require_admin

router = APIRouter()

ReportId = Annotated[int, Path(ge=1)]
UsernamePath = Annotated[str, Path(min_length=1, max_length=30)]


class CreateReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reportableType: Literal["discussion", "reply", "user"]
    reportableId: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class ResolveReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["open", "in_progress", "resolved", "dismissed"]
    action: str | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


class BanUserBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: str = Field(min_length=1, max_length=30)
    reason: str | None = Field(default=None, max_length=1000)
    durationHours: int | None = Field(default=None, ge=1, le=24 * 365)


class UnbanUserBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str | None = Field(default=None, max_length=1000)


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    targetType: Literal["discussion", "reply"]
    targetId: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/api/moderation/reports", status_code=201)
def create_report(
    body: CreateReportBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
) -> dict:
    return service.create_report(conn, user, body.reportableType, body.reportableId, body.reason)


@router.get("/api/moderation/reports")
def list_reports(
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
    status: Literal["open", "in_progress", "resolved", "dismissed"] | None = Query(default=None),
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return service.list_reports(conn, user, status, cursor, limit)


@router.patch("/api/moderation/reports/{id}")
def resolve_report(
    id: ReportId,
    body: ResolveReportBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
) -> dict:
    return service.resolve_report(conn, user, id, body.status, body.action, body.reason)


@router.post("/api/moderation/bans")
def ban_user(
    body: BanUserBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
) -> dict:
    service.ban_user(conn, user, body.username, body.reason, body.durationHours)
    return {"ok": True}


@router.delete("/api/moderation/bans/{username}")
def unban_user(
    username: UsernamePath,
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
    body: UnbanUserBody | None = Body(default=None),
) -> dict:
    service.unban_user(conn, user, username, body.reason if body else None)
    return {"ok": True}


@router.get("/api/moderation/actions")
def list_actions(
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return service.list_actions(conn, user, cursor, limit)


@router.post("/api/moderation/restore")
def restore_content(
    body: RestoreBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
) -> dict:
    service.restore_content(conn, user, body.targetType, body.targetId, body.reason)
    return {"ok": True}
