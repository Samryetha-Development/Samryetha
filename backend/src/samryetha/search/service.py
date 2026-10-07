"""Typed SQLite substring search service."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..authz import Actor
from ..discussions.models import ModerationStatus
from ..discussions import moderation_visible, preview, visible_board_ids
from ..ids import BoardID, DiscussionID, UserID
from ..schema import boards, discussions, users
from .models import SearchAuthor, SearchBoard, SearchItem, SearchOptions, SearchResult
from ..users import make_handle

_LIKE_ESCAPE = re.compile(r'[\\%_]')


@dataclass(frozen=True, slots=True)
class _SearchRow:
    id: DiscussionID
    title: str
    body_md: str
    reply_count: int
    is_pinned: bool
    is_locked: bool
    moderation_status: ModerationStatus
    created_at: int
    last_reply_at: int | None
    board_id: BoardID
    author_id: UserID


@dataclass(frozen=True, slots=True)
class _AuthorLookup:
    id: UserID
    username: str
    discriminator: int | None
    display_name: str


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _search_row(row: RowMapping) -> _SearchRow:
    return _SearchRow(
        id=DiscussionID(_int(row["id"], "discussion id")),
        title=_str(row["title"], "title"),
        body_md=_str(row["body_md"], "body"),
        reply_count=_int(row["reply_count"], "reply count"),
        is_pinned=bool(_int(row["is_pinned"], "is pinned")),
        is_locked=bool(_int(row["is_locked"], "is locked")),
        moderation_status=ModerationStatus(_str(row["moderation_status"], "moderation status")),
        created_at=_int(row["created_at"], "created at"),
        last_reply_at=_optional_int(row["last_reply_at"], "last reply at"),
        board_id=BoardID(_int(row["board_id"], "board id")),
        author_id=UserID(_int(row["author_id"], "author id")),
    )


def escape_like(value: str) -> str:
    return _LIKE_ESCAPE.sub(lambda match: "\\" + match.group(0), value)


def search_discussions(
    conn: Connection,
    viewer: Actor | None,
    options: SearchOptions,
) -> SearchResult:
    query = escape_like(options.query.strip()[:100])
    limit = min(options.limit, 50)
    visible = visible_board_ids(conn, viewer)
    match = or_(
        discussions.c.title.like(f"%{query}%"),
        discussions.c.body_md.like(f"%{query}%"),
    )
    conditions: list[ColumnElement[bool]] = [
        discussions.c.deleted_at.is_(None),
        discussions.c.board_id.in_(visible),
        match,
    ]
    moderation_predicate = moderation_visible(
        discussions.c.moderation_status,
        discussions.c.author_id,
        viewer,
    )
    if moderation_predicate is not None:
        conditions.append(moderation_predicate)
    if options.board_slug:
        board_id = conn.execute(
            select(boards.c.id).where(boards.c.slug == options.board_slug)
        ).scalar_one_or_none()
        if board_id is not None:
            conditions.append(discussions.c.board_id == board_id)

    total_value = conn.execute(
        select(func.count()).select_from(discussions).where(and_(*conditions))
    ).scalar_one()
    total = _int(total_value, "total")
    rows = conn.execute(
        select(
            discussions.c.id,
            discussions.c.title,
            discussions.c.body_md,
            discussions.c.reply_count,
            discussions.c.is_pinned,
            discussions.c.is_locked,
            discussions.c.moderation_status,
            discussions.c.created_at,
            discussions.c.last_reply_at,
            discussions.c.board_id,
            discussions.c.author_id,
        )
        .where(and_(*conditions))
        .order_by(
            discussions.c.last_reply_at.desc(),
            discussions.c.created_at.desc(),
            discussions.c.id.desc(),
        )
        .limit(limit)
    ).mappings()
    records = [_search_row(row) for row in rows]
    board_ids = {record.board_id for record in records}
    author_ids = {record.author_id for record in records}

    board_map: dict[BoardID, SearchBoard] = {}
    if board_ids:
        for row in conn.execute(
            select(boards.c.id, boards.c.slug, boards.c.name).where(boards.c.id.in_(board_ids))
        ).mappings():
            record = SearchBoard(
                id=BoardID(_int(row["id"], "board id")),
                slug=_str(row["slug"], "board slug"),
                name=_str(row["name"], "board name"),
            )
            board_map[record.id] = record

    author_map: dict[UserID, _AuthorLookup] = {}
    if author_ids:
        for row in conn.execute(
            select(users.c.id, users.c.username, users.c.discriminator, users.c.display_name)
            .where(users.c.id.in_(author_ids))
        ).mappings():
            record = _AuthorLookup(
                id=UserID(_int(row["id"], "user id")),
                username=_str(row["username"], "username"),
                discriminator=_optional_int(row["discriminator"], "discriminator"),
                display_name=_str(row["display_name"], "display name"),
            )
            author_map[record.id] = record

    items: list[SearchItem] = []
    for record in records:
        board = board_map.get(record.board_id, SearchBoard(record.board_id, "", ""))
        author_record = author_map.get(record.author_id)
        author = (
            SearchAuthor(record.author_id, "", "", "")
            if author_record is None
            else SearchAuthor(
                id=author_record.id,
                username=author_record.username,
                handle=make_handle(author_record.username, author_record.discriminator),
                display_name=author_record.display_name,
            )
        )
        items.append(
            SearchItem(
                id=record.id,
                title=record.title,
                preview=preview(record.body_md),
                board=board,
                author=author,
                reply_count=record.reply_count,
                is_pinned=record.is_pinned,
                is_locked=record.is_locked,
                moderation_status=record.moderation_status,
                created_at=record.created_at,
                last_activity_at=record.last_reply_at or record.created_at,
            )
        )
    return SearchResult(items=tuple(items), total=total)
