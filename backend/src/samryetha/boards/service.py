"""Typed board service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection, RowMapping

from .models import (
    BoardAuthz, BoardBody, BoardMemberResponse, BoardMemberRole, BoardPatch,
    BoardSummaryResponse, BoardVisibility, PostingPolicy,
)
from ..core.db import now_ms
from ..core.deps import CurrentUser
from ..core.errors import conflict, not_found
from ..core.ids import BoardID, UserID
from ..core.schema import board_members, boards, discussions, users
from ..users import make_handle
from ..core.records import opt_int, require_int, require_str

@dataclass(frozen=True, slots=True)
class BoardRecord:
    id: BoardID
    slug: str
    name: str
    description: str
    visibility: BoardVisibility
    posting_policy: PostingPolicy
    created_by_user_id: UserID | None
    deleted_at: int | None

def _record(row: RowMapping) -> BoardRecord:
    creator = opt_int(row["created_by_user_id"], "created_by_user_id")
    return BoardRecord(
        BoardID(require_int(row["id"], "board id")), require_str(row["slug"], "slug"),
        require_str(row["name"], "name"), require_str(row["description"], "description"),
        BoardVisibility(require_str(row["visibility"], "visibility")),
        PostingPolicy(require_str(row["posting_policy"], "posting_policy")),
        UserID(creator) if creator is not None else None,
        opt_int(row["deleted_at"], "deleted_at"),
    )

def _start_of_today_ms() -> int:
    local_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp() * 1000)

def get_by_slug(conn: Connection, slug: str) -> BoardRecord | None:
    row = conn.execute(select(boards).where(boards.c.slug == slug, boards.c.deleted_at.is_(None))).mappings().first()
    return _record(row) if row is not None else None

def get_board_for_authz(conn: Connection, slug: str) -> BoardAuthz | None:
    board = get_by_slug(conn, slug)
    if board is None:
        return None
    return {"id": board.id, "visibility": board.visibility.value, "postingPolicy": board.posting_policy.value, "slug": board.slug}

def _summary(conn: Connection, board: BoardRecord, viewer_id: UserID | None) -> BoardSummaryResponse:
    member_count = require_int(conn.execute(select(func.count()).select_from(board_members).where(board_members.c.board_id == board.id)).scalar_one(), "member count")
    activity = require_int(conn.execute(select(func.count()).select_from(discussions).where(discussions.c.board_id == board.id, discussions.c.created_at >= _start_of_today_ms(), discussions.c.deleted_at.is_(None))).scalar_one(), "today activity")
    role: BoardMemberRole | None = None
    if viewer_id is not None:
        raw = conn.execute(select(board_members.c.role).where(board_members.c.board_id == board.id, board_members.c.user_id == viewer_id)).scalar_one_or_none()
        role = BoardMemberRole(require_str(raw, "member role")) if raw is not None else None
    return BoardSummaryResponse(
        id=board.id, slug=board.slug, name=board.name, description=board.description,
        visibility=board.visibility, posting_policy=board.posting_policy,
        member_count=member_count, today_activity=activity, current_user_role=role,
    )

def _is_visible(conn: Connection, viewer: CurrentUser | None, board: BoardRecord) -> bool:
    if viewer is not None and viewer.role == "admin":
        return True
    if board.visibility is BoardVisibility.Public:
        return True
    if viewer is None:
        return False
    return conn.execute(select(board_members.c.board_id).where(board_members.c.board_id == board.id, board_members.c.user_id == viewer.id)).first() is not None

def list_boards(conn: Connection, viewer: CurrentUser | None) -> list[BoardSummaryResponse]:
    rows = conn.execute(select(boards).where(boards.c.deleted_at.is_(None)).order_by(boards.c.name)).mappings().all()
    records = (_record(row) for row in rows)
    viewer_id = UserID(viewer.id) if viewer is not None else None
    return [_summary(conn, board, viewer_id) for board in records if _is_visible(conn, viewer, board)]

def get_board(conn: Connection, viewer: CurrentUser | None, slug: str) -> BoardSummaryResponse:
    board = get_by_slug(conn, slug)
    if board is None or not _is_visible(conn, viewer, board):
        raise not_found("Board not found")
    return _summary(conn, board, UserID(viewer.id) if viewer is not None else None)

def _slugify(slug: str) -> str:
    return re.sub(r"\s+", "-", slug.strip().lower())

def create_board(conn: Connection, actor_id: UserID, command: BoardBody) -> BoardSummaryResponse:
    slug = _slugify(command.slug)
    if get_by_slug(conn, slug) is not None:
        raise conflict("Board slug already exists")
    stamp = now_ms()
    result = conn.execute(boards.insert().values(
        slug=slug, name=command.name, description=command.description or "",
        visibility=(command.visibility or BoardVisibility.Public).value,
        posting_policy=(command.posting_policy or PostingPolicy.Members).value,
        created_by_user_id=actor_id, created_at=stamp, updated_at=stamp,
    ))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("board insert did not return a primary key")
    board_id = BoardID(require_int(primary_key[0], "inserted board id"))
    conn.execute(board_members.insert().values(board_id=board_id, user_id=actor_id, role=BoardMemberRole.Moderator.value, joined_at=stamp))
    created = get_by_slug(conn, slug)
    if created is None:
        raise RuntimeError("created board could not be loaded")
    return _summary(conn, created, None)

def update_board(conn: Connection, slug: str, command: BoardPatch) -> BoardSummaryResponse:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    values: dict[str, object] = {"updated_at": now_ms()}
    fields = command.model_fields_set
    if "name" in fields and command.name is not None:
        values["name"] = command.name
    if "description" in fields and command.description is not None:
        values["description"] = command.description
    if "visibility" in fields and command.visibility is not None:
        values["visibility"] = command.visibility.value
    if "posting_policy" in fields and command.posting_policy is not None:
        values["posting_policy"] = command.posting_policy.value
    conn.execute(update(boards).where(boards.c.id == board.id).values(**values))
    updated = get_by_slug(conn, slug)
    if updated is None:
        raise RuntimeError("updated board could not be loaded")
    return _summary(conn, updated, None)

def delete_board(conn: Connection, actor_id: UserID, slug: str, reason: str | None) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    stamp = now_ms()
    conn.execute(update(boards).where(boards.c.id == board.id).values(deleted_at=stamp, deleted_by=actor_id, deletion_reason=reason, updated_at=stamp))

def join_board(conn: Connection, user_id: UserID, slug: str) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    if conn.execute(select(board_members.c.board_id).where(board_members.c.board_id == board.id, board_members.c.user_id == user_id)).first() is not None:
        raise conflict("Already a member")
    conn.execute(board_members.insert().values(board_id=board.id, user_id=user_id, role=BoardMemberRole.Member.value, joined_at=now_ms()))

def leave_board(conn: Connection, user_id: UserID, slug: str) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    conn.execute(board_members.delete().where(board_members.c.board_id == board.id, board_members.c.user_id == user_id))

def list_members(conn: Connection, viewer: CurrentUser | None, slug: str) -> list[BoardMemberResponse]:
    board = get_by_slug(conn, slug)
    if board is None or not _is_visible(conn, viewer, board):
        raise not_found("Board not found")
    rows = conn.execute(select(users.c.id, users.c.username, users.c.display_name, users.c.discriminator, board_members.c.role).select_from(board_members).join(users, board_members.c.user_id == users.c.id).where(board_members.c.board_id == board.id)).mappings().all()
    return [BoardMemberResponse(
        id=UserID(require_int(row["id"], "user id")), username=require_str(row["username"], "username"),
        handle=make_handle(require_str(row["username"], "username"), opt_int(row["discriminator"], "discriminator")),
        display_name=require_str(row["display_name"], "display_name"), role=BoardMemberRole(require_str(row["role"], "role")),
    ) for row in rows]

def update_member_role(conn: Connection, slug: str, user_id: UserID, role: BoardMemberRole) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    if conn.execute(select(board_members.c.role).where(board_members.c.board_id == board.id, board_members.c.user_id == user_id)).first() is None:
        raise not_found("User is not a member of this board")
    conn.execute(update(board_members).where(board_members.c.board_id == board.id, board_members.c.user_id == user_id).values(role=role.value))
