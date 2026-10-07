"""Typed SQLAlchemy boundary for discussions, replies, boards, and authors."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from .models import BodyFormat, ModerationStatus
from ..core.ids import BoardID, DiscussionID, ReplyID, UserID
from ..core.schema import boards, discussions, replies, users


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    return None if value is None else _required_int(value, field)


def _required_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _optional_str(value: object, field: str) -> str | None:
    return None if value is None else _required_str(value, field)


@dataclass(frozen=True, slots=True)
class UserSummaryRecord:
    id: UserID
    username: str
    display_name: str
    discriminator: int | None


@dataclass(frozen=True, slots=True)
class BoardRecord:
    id: BoardID
    slug: str
    name: str
    visibility: str
    posting_policy: str


@dataclass(frozen=True, slots=True)
class DiscussionRecord:
    id: DiscussionID
    board_id: BoardID
    author_id: UserID
    title: str
    body_md: str
    body_html: str | None
    body_format: BodyFormat
    reply_count: int
    save_count: int
    is_pinned: bool
    is_locked: bool
    status: str
    moderation_status: ModerationStatus
    last_reply_at: int | None
    deleted_at: int | None
    deleted_by: UserID | None
    deletion_reason: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class DiscussionFeedRecord:
    id: DiscussionID
    board_id: BoardID
    author_id: UserID
    title: str
    body_md: str
    reply_count: int
    is_pinned: bool
    is_locked: bool
    moderation_status: ModerationStatus
    created_at: int
    last_reply_at: int | None


@dataclass(frozen=True, slots=True)
class ReplyRecord:
    id: ReplyID
    discussion_id: DiscussionID
    author_id: UserID
    parent_reply_id: ReplyID | None
    body_md: str
    body_html: str | None
    body_format: BodyFormat
    moderation_status: ModerationStatus
    deleted_at: int | None
    deleted_by: UserID | None
    deletion_reason: str | None
    created_at: int
    updated_at: int


def _user(row: RowMapping) -> UserSummaryRecord:
    return UserSummaryRecord(
        id=UserID(_required_int(row["id"], "user id")),
        username=_required_str(row["username"], "username"),
        display_name=_required_str(row["display_name"], "display_name"),
        discriminator=_optional_int(row["discriminator"], "discriminator"),
    )


def _board(row: RowMapping) -> BoardRecord:
    return BoardRecord(
        id=BoardID(_required_int(row["id"], "board id")),
        slug=_required_str(row["slug"], "board slug"),
        name=_required_str(row["name"], "board name"),
        visibility=_required_str(row["visibility"], "board visibility"),
        posting_policy=_required_str(row["posting_policy"], "board posting_policy"),
    )


def _discussion(row: RowMapping) -> DiscussionRecord:
    deleted_by = _optional_int(row["deleted_by"], "discussion deleted_by")
    return DiscussionRecord(
        id=DiscussionID(_required_int(row["id"], "discussion id")),
        board_id=BoardID(_required_int(row["board_id"], "discussion board_id")),
        author_id=UserID(_required_int(row["author_id"], "discussion author_id")),
        title=_required_str(row["title"], "discussion title"),
        body_md=_required_str(row["body_md"], "discussion body_md"),
        body_html=_optional_str(row["body_html"], "discussion body_html"),
        body_format=BodyFormat(_required_str(row["body_format"], "discussion body_format")),
        reply_count=_required_int(row["reply_count"], "discussion reply_count"),
        save_count=_required_int(row["save_count"], "discussion save_count"),
        is_pinned=_required_int(row["is_pinned"], "discussion is_pinned") == 1,
        is_locked=_required_int(row["is_locked"], "discussion is_locked") == 1,
        status=_required_str(row["status"], "discussion status"),
        moderation_status=ModerationStatus(
            _required_str(row["moderation_status"], "discussion moderation_status")
        ),
        last_reply_at=_optional_int(row["last_reply_at"], "discussion last_reply_at"),
        deleted_at=_optional_int(row["deleted_at"], "discussion deleted_at"),
        deleted_by=UserID(deleted_by) if deleted_by is not None else None,
        deletion_reason=_optional_str(row["deletion_reason"], "discussion deletion_reason"),
        created_at=_required_int(row["created_at"], "discussion created_at"),
        updated_at=_required_int(row["updated_at"], "discussion updated_at"),
    )


def _feed(row: RowMapping) -> DiscussionFeedRecord:
    return DiscussionFeedRecord(
        id=DiscussionID(_required_int(row["id"], "discussion id")),
        board_id=BoardID(_required_int(row["board_id"], "discussion board_id")),
        author_id=UserID(_required_int(row["author_id"], "discussion author_id")),
        title=_required_str(row["title"], "discussion title"),
        body_md=_required_str(row["body_md"], "discussion body_md"),
        reply_count=_required_int(row["reply_count"], "discussion reply_count"),
        is_pinned=_required_int(row["is_pinned"], "discussion is_pinned") == 1,
        is_locked=_required_int(row["is_locked"], "discussion is_locked") == 1,
        moderation_status=ModerationStatus(
            _required_str(row["moderation_status"], "discussion moderation_status")
        ),
        created_at=_required_int(row["created_at"], "discussion created_at"),
        last_reply_at=_optional_int(row["last_reply_at"], "discussion last_reply_at"),
    )


def _reply(row: RowMapping) -> ReplyRecord:
    parent_id = _optional_int(row["parent_reply_id"], "reply parent_reply_id")
    deleted_by = _optional_int(row["deleted_by"], "reply deleted_by")
    return ReplyRecord(
        id=ReplyID(_required_int(row["id"], "reply id")),
        discussion_id=DiscussionID(_required_int(row["discussion_id"], "reply discussion_id")),
        author_id=UserID(_required_int(row["author_id"], "reply author_id")),
        parent_reply_id=ReplyID(parent_id) if parent_id is not None else None,
        body_md=_required_str(row["body_md"], "reply body_md"),
        body_html=_optional_str(row["body_html"], "reply body_html"),
        body_format=BodyFormat(_required_str(row["body_format"], "reply body_format")),
        moderation_status=ModerationStatus(
            _required_str(row["moderation_status"], "reply moderation_status")
        ),
        deleted_at=_optional_int(row["deleted_at"], "reply deleted_at"),
        deleted_by=UserID(deleted_by) if deleted_by is not None else None,
        deletion_reason=_optional_str(row["deletion_reason"], "reply deletion_reason"),
        created_at=_required_int(row["created_at"], "reply created_at"),
        updated_at=_required_int(row["updated_at"], "reply updated_at"),
    )


def get_discussion(conn: Connection, discussion_id: DiscussionID) -> DiscussionRecord | None:
    row = conn.execute(select(discussions).where(discussions.c.id == discussion_id)).mappings().first()
    return _discussion(row) if row is not None else None


def get_reply(conn: Connection, reply_id: ReplyID) -> ReplyRecord | None:
    row = conn.execute(select(replies).where(replies.c.id == reply_id)).mappings().first()
    return _reply(row) if row is not None else None


def get_board(conn: Connection, board_id: BoardID) -> BoardRecord | None:
    row = conn.execute(
        select(boards.c.id, boards.c.slug, boards.c.name, boards.c.visibility, boards.c.posting_policy)
        .where(boards.c.id == board_id)
    ).mappings().first()
    return _board(row) if row is not None else None


def user_summaries(conn: Connection, user_ids: Iterable[UserID]) -> dict[UserID, UserSummaryRecord]:
    ids = tuple(user_ids)
    if not ids:
        return {}
    rows: Sequence[RowMapping] = conn.execute(
        select(users.c.id, users.c.username, users.c.display_name, users.c.discriminator)
        .where(users.c.id.in_(ids))
    ).mappings().all()
    records = (_user(row) for row in rows)
    return {record.id: record for record in records}


def board_records(conn: Connection, board_ids: Iterable[BoardID]) -> dict[BoardID, BoardRecord]:
    ids = tuple(board_ids)
    if not ids:
        return {}
    rows: Sequence[RowMapping] = conn.execute(
        select(boards.c.id, boards.c.slug, boards.c.name, boards.c.visibility, boards.c.posting_policy)
        .where(boards.c.id.in_(ids))
    ).mappings().all()
    records = (_board(row) for row in rows)
    return {record.id: record for record in records}


def list_feed(
    conn: Connection,
    conditions: Sequence[ColumnElement[bool]],
    *,
    limit: int,
    sort_by_replies: bool,
) -> list[DiscussionFeedRecord]:
    primary_sort = discussions.c.reply_count if sort_by_replies else discussions.c.created_at
    rows: Sequence[RowMapping] = conn.execute(
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
            discussions.c.moderation_status,
        )
        .where(and_(*conditions))
        .order_by(discussions.c.is_pinned.desc(), primary_sort.desc(), discussions.c.id.desc())
        .limit(limit + 1)
    ).mappings().all()
    return [_feed(row) for row in rows]


def list_discussion_replies(
    conn: Connection,
    conditions: Sequence[ColumnElement[bool]],
) -> list[ReplyRecord]:
    rows: Sequence[RowMapping] = conn.execute(
        select(replies).where(and_(*conditions)).order_by(replies.c.created_at)
    ).mappings().all()
    return [_reply(row) for row in rows]


def list_reply_feed(
    conn: Connection,
    conditions: Sequence[ColumnElement[bool]],
    *,
    limit: int,
) -> list[ReplyRecord]:
    rows: Sequence[RowMapping] = conn.execute(
        select(replies)
        .where(and_(*conditions))
        .order_by(replies.c.id.desc())
        .limit(limit + 1)
    ).mappings().all()
    return [_reply(row) for row in rows]


def discussion_titles(
    conn: Connection,
    discussion_ids: Iterable[DiscussionID],
) -> dict[DiscussionID, str]:
    ids = tuple(discussion_ids)
    if not ids:
        return {}
    rows: Sequence[RowMapping] = conn.execute(
        select(discussions.c.id, discussions.c.title).where(discussions.c.id.in_(ids))
    ).mappings().all()
    return {
        DiscussionID(_required_int(row["id"], "discussion id")): _required_str(
            row["title"], "discussion title"
        )
        for row in rows
    }
