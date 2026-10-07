"""Typed SQLAlchemy persistence boundary for discussion search."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..authz import Actor
from ..core.ids import BoardID, DiscussionID, UserID
from ..core.records import opt_int, require_int, require_str
from ..core.schema import boards, discussions, users


@dataclass(frozen=True, slots=True)
class SearchRecord:
    id: DiscussionID
    title: str
    body_md: str
    reply_count: int
    is_pinned: bool
    is_locked: bool
    created_at: int
    last_reply_at: int | None
    board_id: BoardID
    author_id: UserID


@dataclass(frozen=True, slots=True)
class BoardRecord:
    id: BoardID
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    id: UserID
    username: str
    discriminator: int | None
    display_name: str


def _search_record(row: RowMapping) -> SearchRecord:
    return SearchRecord(
        id=DiscussionID(require_int(row["id"], "discussion id")),
        title=require_str(row["title"], "title"),
        body_md=require_str(row["body_md"], "body"),
        reply_count=require_int(row["reply_count"], "reply count"),
        is_pinned=bool(require_int(row["is_pinned"], "is pinned")),
        is_locked=bool(require_int(row["is_locked"], "is locked")),
        created_at=require_int(row["created_at"], "created at"),
        last_reply_at=opt_int(row["last_reply_at"], "last reply at"),
        board_id=BoardID(require_int(row["board_id"], "board id")),
        author_id=UserID(require_int(row["author_id"], "author id")),
    )


class SearchRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def search_records(
        self, *, viewer: Actor | None, visible_board_ids: Sequence[int], query: str, board_slug: str | None, limit: int
    ) -> tuple[list[SearchRecord], int]:
        match = or_(
            discussions.c.title.like(f"%{query}%"),
            discussions.c.body_md.like(f"%{query}%"),
        )
        conditions: list[ColumnElement[bool]] = [
            discussions.c.deleted_at.is_(None),
            discussions.c.board_id.in_(visible_board_ids),
            match,
        ]
        if board_slug:
            board_id = self._conn.execute(select(boards.c.id).where(boards.c.slug == board_slug)).scalar_one_or_none()
            if board_id is not None:
                conditions.append(discussions.c.board_id == board_id)
        total = require_int(
            self._conn.execute(select(func.count()).select_from(discussions).where(and_(*conditions))).scalar_one(),
            "total",
        )
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(
                    discussions.c.id,
                    discussions.c.title,
                    discussions.c.body_md,
                    discussions.c.reply_count,
                    discussions.c.is_pinned,
                    discussions.c.is_locked,
                    discussions.c.created_at,
                    discussions.c.last_reply_at,
                    discussions.c.board_id,
                    discussions.c.author_id,
                )
                .where(and_(*conditions))
                .order_by(discussions.c.last_reply_at.desc(), discussions.c.created_at.desc(), discussions.c.id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_search_record(row) for row in rows], total

    def board_map(self, board_ids: set[BoardID]) -> dict[BoardID, BoardRecord]:
        if not board_ids:
            return {}
        rows = (
            self._conn.execute(select(boards.c.id, boards.c.slug, boards.c.name).where(boards.c.id.in_(board_ids)))
            .mappings()
            .all()
        )
        records = (
            BoardRecord(
                id=BoardID(require_int(row["id"], "board id")),
                slug=require_str(row["slug"], "board slug"),
                name=require_str(row["name"], "board name"),
            )
            for row in rows
        )
        return {record.id: record for record in records}

    def author_map(self, author_ids: set[UserID]) -> dict[UserID, AuthorRecord]:
        if not author_ids:
            return {}
        rows = (
            self._conn.execute(
                select(users.c.id, users.c.username, users.c.discriminator, users.c.display_name).where(
                    users.c.id.in_(author_ids)
                )
            )
            .mappings()
            .all()
        )
        records = (
            AuthorRecord(
                id=UserID(require_int(row["id"], "user id")),
                username=require_str(row["username"], "username"),
                discriminator=opt_int(row["discriminator"], "discriminator"),
                display_name=require_str(row["display_name"], "display name"),
            )
            for row in rows
        )
        return {record.id: record for record in records}
