"""Typed HTTP boundary for boards."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from .. import boards as service
from .. import discussions as discussions_service
from ..authz import Abilities, assert_can
from ..boards.models import (
    BoardBody, BoardListResponse, BoardMemberListResponse, BoardOperationOkResponse,
    BoardPatch, BoardSummaryResponse, DeleteBoardBody, MemberRoleBody, MembershipResponse,
)
from ..core.deps import CurrentUser, DbConn, get_current_user, require_active_user
from ..discussions.models import DiscussionFeedQuery, DiscussionListResponse
from ..core.errors import internal_error
from ..core.ids import UserID

router = APIRouter()

Slug = Annotated[str, Path(min_length=1, max_length=50)]
MemberUserID = Annotated[UserID, Path(ge=1)]
ViewerDep = Annotated[CurrentUser | None, Depends(get_current_user)]
ActiveUserDep = Annotated[CurrentUser, Depends(require_active_user)]


@router.get("/api/boards", response_model=BoardListResponse)
def list_boards(conn: DbConn, viewer: ViewerDep) -> BoardListResponse:
    return BoardListResponse(items=service.list_boards(conn, viewer))


@router.get("/api/boards/{slug}", response_model=BoardSummaryResponse)
def get_board(slug: Slug, conn: DbConn, viewer: ViewerDep) -> BoardSummaryResponse:
    return service.get_board(conn, viewer, slug)


@router.post("/api/boards", status_code=201, response_model=BoardSummaryResponse)
def create_board(body: BoardBody, conn: DbConn, user: ActiveUserDep) -> BoardSummaryResponse:
    assert_can(user, Abilities.BOARD_CREATE, None, conn)
    return service.create_board(conn, UserID(user.id), body)


@router.patch("/api/boards/{slug}", response_model=BoardSummaryResponse)
def patch_board(slug: Slug, body: BoardPatch, conn: DbConn, user: ActiveUserDep) -> BoardSummaryResponse:
    board = service.get_board_for_authz(conn, slug)
    if board is None:
        raise internal_error()
    assert_can(user, Abilities.BOARD_UPDATE, {"type": "board", **board}, conn)
    return service.update_board(conn, slug, body)


@router.delete("/api/boards/{slug}", response_model=BoardOperationOkResponse)
def delete_board(slug: Slug, conn: DbConn, user: ActiveUserDep, body: DeleteBoardBody | None = Body(default=None)) -> BoardOperationOkResponse:
    board = service.get_board_for_authz(conn, slug)
    if board is None:
        raise internal_error()
    assert_can(user, Abilities.BOARD_DELETE, {"type": "board", **board}, conn)
    service.delete_board(conn, UserID(user.id), slug, body.reason if body else None)
    return BoardOperationOkResponse()


@router.get("/api/boards/{slug}/discussions", response_model=DiscussionListResponse)
def board_discussions(slug: Slug, conn: DbConn, viewer: ViewerDep, cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50)) -> DiscussionListResponse:
    return discussions_service.list_discussions(conn, viewer, DiscussionFeedQuery(cursor=cursor, limit=limit, board_slug=slug))


@router.post("/api/boards/{slug}/join", response_model=MembershipResponse)
def join_board(slug: Slug, conn: DbConn, user: ActiveUserDep) -> MembershipResponse:
    board = service.get_board_for_authz(conn, slug)
    if board is None:
        raise internal_error()
    assert_can(user, Abilities.BOARD_JOIN, {"type": "board", **board}, conn)
    service.join_board(conn, UserID(user.id), slug)
    return MembershipResponse(member=True)


@router.delete("/api/boards/{slug}/leave", response_model=MembershipResponse)
def leave_board(slug: Slug, conn: DbConn, user: ActiveUserDep) -> MembershipResponse:
    service.leave_board(conn, UserID(user.id), slug)
    return MembershipResponse(member=False)


@router.get("/api/boards/{slug}/members", response_model=BoardMemberListResponse)
def members(slug: Slug, conn: DbConn, viewer: ViewerDep) -> BoardMemberListResponse:
    return BoardMemberListResponse(items=service.list_members(conn, viewer, slug))


@router.patch("/api/boards/{slug}/members/{userId}", response_model=BoardOperationOkResponse)
def set_member_role(slug: Slug, userId: MemberUserID, body: MemberRoleBody, conn: DbConn, user: ActiveUserDep) -> BoardOperationOkResponse:
    board = service.get_board_for_authz(conn, slug)
    if board is None:
        raise internal_error()
    assert_can(user, Abilities.BOARD_MANAGE_MEMBERS, {"type": "board", **board}, conn)
    service.update_member_role(conn, slug, userId, body.role)
    return BoardOperationOkResponse()
