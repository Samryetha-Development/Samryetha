"""Typed moderation HTTP boundary."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from .. import moderation as service
from ..deps import CurrentUser, DbConn, require_active_user, require_admin
from ..ids import ModerationActionID, ReportID
from ..moderation.models import (
    BanUserBody, CreateReportBody, ModerationActionListResponse, ModerationOperationOkResponse,
    ReportListResponse, ReportResponse, ReportStatus, ResolveReportBody, RestoreBody, UnbanUserBody,
)

router = APIRouter()
ReportIDPath = Annotated[ReportID, Path(ge=1)]
UsernamePath = Annotated[str, Path(min_length=1, max_length=30)]


@router.post("/api/moderation/reports", status_code=201, response_model=ReportResponse)
def create_report(body: CreateReportBody, conn: DbConn,
                  user: CurrentUser = Depends(require_active_user)) -> ReportResponse:
    return service.create_report(conn, user, body.reportable_type, body.reportable_id, body.reason)


@router.get("/api/moderation/reports", response_model=ReportListResponse)
def list_reports(conn: DbConn, user: CurrentUser = Depends(require_admin),
                 status: ReportStatus | None = Query(default=None),
                 cursor: ReportID | None = Query(default=None, ge=1),
                 limit: int = Query(default=20, ge=1, le=50)) -> ReportListResponse:
    return service.list_reports(conn, user, status, cursor, limit)


@router.patch("/api/moderation/reports/{id}", response_model=ReportResponse)
def resolve_report(id: ReportIDPath, body: ResolveReportBody, conn: DbConn,
                   user: CurrentUser = Depends(require_admin)) -> ReportResponse:
    return service.resolve_report(conn, user, id, body.status, body.action, body.reason)


@router.post("/api/moderation/bans", response_model=ModerationOperationOkResponse)
def ban_user(body: BanUserBody, conn: DbConn,
             user: CurrentUser = Depends(require_admin)) -> ModerationOperationOkResponse:
    service.ban_user(conn, user, body.username, body.reason, body.duration_hours)
    return ModerationOperationOkResponse()


@router.delete("/api/moderation/bans/{username}", response_model=ModerationOperationOkResponse)
def unban_user(username: UsernamePath, conn: DbConn, user: CurrentUser = Depends(require_admin),
               body: UnbanUserBody | None = Body(default=None)) -> ModerationOperationOkResponse:
    service.unban_user(conn, user, username, body.reason if body else None)
    return ModerationOperationOkResponse()


@router.get("/api/moderation/actions", response_model=ModerationActionListResponse)
def list_actions(conn: DbConn, user: CurrentUser = Depends(require_admin),
                 cursor: ModerationActionID | None = Query(default=None, ge=1),
                 limit: int = Query(default=20, ge=1, le=50)) -> ModerationActionListResponse:
    return service.list_actions(conn, user, cursor, limit)


@router.post("/api/moderation/restore", response_model=ModerationOperationOkResponse)
def restore_content(body: RestoreBody, conn: DbConn,
                    user: CurrentUser = Depends(require_admin)) -> ModerationOperationOkResponse:
    service.restore_content(conn, user, body.target_type, body.target_id, body.reason)
    return ModerationOperationOkResponse()
