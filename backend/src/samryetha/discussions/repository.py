"""Typed SQLAlchemy boundary for discussions, replies, boards, and authors."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from .models import BodyFormat
from ..core.ids import BoardID, DiscussionID, ReplyID, UserID
from ..core.schema import (
    attachments,
    board_members,
    boards,
    discussion_follows,
    discussion_saves,
    discussions,
    replies,
    user_follows,
    users,
)
from ..core.records import opt_int, opt_str, require_int, require_str
from ..authz import Actor


def _visible_discussion_conditions(
    board_ids: Sequence[BoardID], viewer: Actor | None
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [discussions.c.deleted_at.is_(None), discussions.c.board_id.in_(board_ids)]
    return conditions


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
    deleted_at: int | None
    deleted_by: UserID | None
    deletion_reason: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class BoardVisibilityRecord:
    id: BoardID
    visibility: str


def _user(row: RowMapping) -> UserSummaryRecord:
    return UserSummaryRecord(
        id=UserID(require_int(row["id"], "user id")),
        username=require_str(row["username"], "username"),
        display_name=require_str(row["display_name"], "display_name"),
        discriminator=opt_int(row["discriminator"], "discriminator"),
    )


def _board(row: RowMapping) -> BoardRecord:
    return BoardRecord(
        id=BoardID(require_int(row["id"], "board id")),
        slug=require_str(row["slug"], "board slug"),
        name=require_str(row["name"], "board name"),
        visibility=require_str(row["visibility"], "board visibility"),
        posting_policy=require_str(row["posting_policy"], "board posting_policy"),
    )


def _discussion(row: RowMapping) -> DiscussionRecord:
    deleted_by = opt_int(row["deleted_by"], "discussion deleted_by")
    return DiscussionRecord(
        id=DiscussionID(require_int(row["id"], "discussion id")),
        board_id=BoardID(require_int(row["board_id"], "discussion board_id")),
        author_id=UserID(require_int(row["author_id"], "discussion author_id")),
        title=require_str(row["title"], "discussion title"),
        body_md=require_str(row["body_md"], "discussion body_md"),
        body_html=opt_str(row["body_html"], "discussion body_html"),
        body_format=BodyFormat(require_str(row["body_format"], "discussion body_format")),
        reply_count=require_int(row["reply_count"], "discussion reply_count"),
        save_count=require_int(row["save_count"], "discussion save_count"),
        is_pinned=require_int(row["is_pinned"], "discussion is_pinned") == 1,
        is_locked=require_int(row["is_locked"], "discussion is_locked") == 1,
        status=require_str(row["status"], "discussion status"),
        last_reply_at=opt_int(row["last_reply_at"], "discussion last_reply_at"),
        deleted_at=opt_int(row["deleted_at"], "discussion deleted_at"),
        deleted_by=UserID(deleted_by) if deleted_by is not None else None,
        deletion_reason=opt_str(row["deletion_reason"], "discussion deletion_reason"),
        created_at=require_int(row["created_at"], "discussion created_at"),
        updated_at=require_int(row["updated_at"], "discussion updated_at"),
    )


def _feed(row: RowMapping) -> DiscussionFeedRecord:
    return DiscussionFeedRecord(
        id=DiscussionID(require_int(row["id"], "discussion id")),
        board_id=BoardID(require_int(row["board_id"], "discussion board_id")),
        author_id=UserID(require_int(row["author_id"], "discussion author_id")),
        title=require_str(row["title"], "discussion title"),
        body_md=require_str(row["body_md"], "discussion body_md"),
        reply_count=require_int(row["reply_count"], "discussion reply_count"),
        is_pinned=require_int(row["is_pinned"], "discussion is_pinned") == 1,
        is_locked=require_int(row["is_locked"], "discussion is_locked") == 1,
        created_at=require_int(row["created_at"], "discussion created_at"),
        last_reply_at=opt_int(row["last_reply_at"], "discussion last_reply_at"),
    )


def _reply(row: RowMapping) -> ReplyRecord:
    parent_id = opt_int(row["parent_reply_id"], "reply parent_reply_id")
    deleted_by = opt_int(row["deleted_by"], "reply deleted_by")
    return ReplyRecord(
        id=ReplyID(require_int(row["id"], "reply id")),
        discussion_id=DiscussionID(require_int(row["discussion_id"], "reply discussion_id")),
        author_id=UserID(require_int(row["author_id"], "reply author_id")),
        parent_reply_id=ReplyID(parent_id) if parent_id is not None else None,
        body_md=require_str(row["body_md"], "reply body_md"),
        body_html=opt_str(row["body_html"], "reply body_html"),
        body_format=BodyFormat(require_str(row["body_format"], "reply body_format")),
        deleted_at=opt_int(row["deleted_at"], "reply deleted_at"),
        deleted_by=UserID(deleted_by) if deleted_by is not None else None,
        deletion_reason=opt_str(row["deletion_reason"], "reply deletion_reason"),
        created_at=require_int(row["created_at"], "reply created_at"),
        updated_at=require_int(row["updated_at"], "reply updated_at"),
    )


class DiscussionRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def feed_records(
        self,
        board_ids: Sequence[BoardID],
        viewer: Actor | None,
        *,
        limit: int,
        sort: str = "date",
        cursor: tuple[int, int, int, bool] | None = None,
        board_id: int | None = None,
        author_id: int | None = None,
        saved_ids: Sequence[DiscussionID] | None = None,
        followed_ids: tuple[list[UserID], list[DiscussionID]] | None = None,
    ) -> list[DiscussionFeedRecord]:
        conditions = _visible_discussion_conditions(board_ids, viewer)
        if board_id is not None:
            conditions.append(discussions.c.board_id == board_id)
        if author_id is not None:
            conditions.append(discussions.c.author_id == author_id)
        if saved_ids is not None:
            conditions.append(discussions.c.id.in_(saved_ids))
        if followed_ids is not None:
            following_ids, followed_discussion_ids = followed_ids
            conditions.append(
                or_(discussions.c.author_id.in_(following_ids), discussions.c.id.in_(followed_discussion_ids))
            )
        if cursor is not None:
            pinned, at, content_id, partitioned = cursor
            primary = discussions.c.reply_count if sort == "replies" else discussions.c.created_at
            if partitioned:
                conditions.append(
                    or_(
                        discussions.c.is_pinned < pinned,
                        (discussions.c.is_pinned == pinned) & (primary < at),
                        (discussions.c.is_pinned == pinned) & (primary == at) & (discussions.c.id < content_id),
                    )
                )
            else:
                conditions.append(or_(primary < at, (primary == at) & (discussions.c.id < content_id)))
        return self.list_feed(conditions, limit=limit, sort_by_replies=sort == "replies")

    def visible_reply_records(self, discussion_id: int, viewer: Actor | None) -> list[ReplyRecord]:
        conditions: list[ColumnElement[bool]] = [replies.c.discussion_id == discussion_id]
        return self.list_discussion_replies(conditions)

    def authored_reply_records(
        self,
        board_ids: Sequence[BoardID],
        viewer: Actor | None,
        author_id: int,
        *,
        limit: int,
        cursor_id: int | None,
    ) -> list[ReplyRecord]:
        parent_ids = self.discussion_ids(_visible_discussion_conditions(board_ids, viewer))
        if not parent_ids:
            return []
        conditions: list[ColumnElement[bool]] = [
            replies.c.author_id == author_id,
            replies.c.deleted_at.is_(None),
            replies.c.discussion_id.in_(parent_ids),
        ]
        if cursor_id is not None:
            conditions.append(replies.c.id < cursor_id)
        return self.list_reply_feed(conditions, limit=limit)

    def get_discussion(self, discussion_id: DiscussionID) -> DiscussionRecord | None:
        row = self._conn.execute(select(discussions).where(discussions.c.id == discussion_id)).mappings().first()
        return _discussion(row) if row is not None else None

    def get_reply(self, reply_id: ReplyID) -> ReplyRecord | None:
        row = self._conn.execute(select(replies).where(replies.c.id == reply_id)).mappings().first()
        return _reply(row) if row is not None else None

    def get_board(self, board_id: BoardID) -> BoardRecord | None:
        row = (
            self._conn.execute(
                select(boards.c.id, boards.c.slug, boards.c.name, boards.c.visibility, boards.c.posting_policy).where(
                    boards.c.id == board_id
                )
            )
            .mappings()
            .first()
        )
        return _board(row) if row is not None else None

    def user_summaries(self, user_ids: Iterable[UserID]) -> dict[UserID, UserSummaryRecord]:
        ids = tuple(user_ids)
        if not ids:
            return {}
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(users.c.id, users.c.username, users.c.display_name, users.c.discriminator).where(
                    users.c.id.in_(ids)
                )
            )
            .mappings()
            .all()
        )
        records = (_user(row) for row in rows)
        return {record.id: record for record in records}

    def board_records(self, board_ids: Iterable[BoardID]) -> dict[BoardID, BoardRecord]:
        ids = tuple(board_ids)
        if not ids:
            return {}
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(boards.c.id, boards.c.slug, boards.c.name, boards.c.visibility, boards.c.posting_policy).where(
                    boards.c.id.in_(ids)
                )
            )
            .mappings()
            .all()
        )
        records = (_board(row) for row in rows)
        return {record.id: record for record in records}

    def list_feed(
        self, conditions: Sequence[ColumnElement[bool]], *, limit: int, sort_by_replies: bool
    ) -> list[DiscussionFeedRecord]:
        primary_sort = discussions.c.reply_count if sort_by_replies else discussions.c.created_at
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
                .order_by(discussions.c.is_pinned.desc(), primary_sort.desc(), discussions.c.id.desc())
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
        return [_feed(row) for row in rows]

    def list_discussion_replies(self, conditions: Sequence[ColumnElement[bool]]) -> list[ReplyRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(select(replies).where(and_(*conditions)).order_by(replies.c.created_at)).mappings().all()
        )
        return [_reply(row) for row in rows]

    def list_reply_feed(self, conditions: Sequence[ColumnElement[bool]], *, limit: int) -> list[ReplyRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(select(replies).where(and_(*conditions)).order_by(replies.c.id.desc()).limit(limit + 1))
            .mappings()
            .all()
        )
        return [_reply(row) for row in rows]

    def discussion_titles(self, discussion_ids: Iterable[DiscussionID]) -> dict[DiscussionID, str]:
        ids = tuple(discussion_ids)
        if not ids:
            return {}
        rows: Sequence[RowMapping] = (
            self._conn.execute(select(discussions.c.id, discussions.c.title).where(discussions.c.id.in_(ids)))
            .mappings()
            .all()
        )
        return {
            DiscussionID(require_int(row["id"], "discussion id")): require_str(row["title"], "discussion title")
            for row in rows
        }

    def visible_boards(self, user_id: UserID | None, *, is_admin: bool) -> list[BoardID]:
        rows = self._conn.execute(select(boards.c.id, boards.c.visibility).where(boards.c.deleted_at.is_(None))).all()
        if is_admin:
            return [BoardID(row.id) for row in rows]
        memberships: set[int] = set()
        if user_id is not None:
            memberships = set(
                self._conn.execute(select(board_members.c.board_id).where(board_members.c.user_id == user_id))
                .scalars()
                .all()
            )
        return [BoardID(row.id) for row in rows if row.visibility == "public" or row.id in memberships]

    def mentioned_users(self, usernames: Sequence[str]) -> list[tuple[UserID, str]]:
        if not usernames:
            return []
        rows = self._conn.execute(
            select(users.c.id, users.c.username).where(users.c.username.in_(usernames), users.c.deleted_at.is_(None))
        ).all()
        return [(UserID(row.id), row.username) for row in rows]

    def discussion_relationships(self, user_id: UserID, discussion_id: DiscussionID) -> tuple[bool, bool]:
        saved = (
            self._conn.execute(
                select(discussion_saves.c.discussion_id).where(
                    discussion_saves.c.user_id == user_id, discussion_saves.c.discussion_id == discussion_id
                )
            ).first()
            is not None
        )
        following = (
            self._conn.execute(
                select(discussion_follows.c.discussion_id).where(
                    discussion_follows.c.user_id == user_id, discussion_follows.c.discussion_id == discussion_id
                )
            ).first()
            is not None
        )
        return saved, following

    def followed_feed_ids(self, user_id: UserID) -> tuple[list[UserID], list[DiscussionID]]:
        author_ids = [
            UserID(value)
            for value in self._conn.execute(
                select(user_follows.c.followee_id).where(user_follows.c.follower_id == user_id)
            )
            .scalars()
            .all()
        ]
        discussion_ids = [
            DiscussionID(value)
            for value in self._conn.execute(
                select(discussion_follows.c.discussion_id).where(discussion_follows.c.user_id == user_id)
            )
            .scalars()
            .all()
        ]
        return author_ids, discussion_ids

    def board_is_public(self, board_id: BoardID) -> bool:
        return (
            self._conn.execute(select(boards.c.visibility).where(boards.c.id == board_id)).scalar_one_or_none()
            == "public"
        )

    def recent_bodies(self, *, content_type: str, author_id: UserID, limit: int) -> list[str]:
        table = discussions if content_type == "discussion" else replies
        values = (
            self._conn.execute(
                select(table.c.body_md).where(table.c.author_id == author_id).order_by(table.c.id.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        return [value for value in values if value]

    def insert_discussion(self, values: dict[str, object]) -> DiscussionID:
        result = self._conn.execute(discussions.insert().values(**values))
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("discussion insert did not return a primary key")
        return DiscussionID(require_int(primary_key[0], "discussion id"))

    def attach_uploads(self, attachment_ids: set[int], *, uploader_id: UserID, discussion_id: DiscussionID) -> bool:
        result = self._conn.execute(
            attachments.update()
            .where(
                attachments.c.id.in_(attachment_ids),
                attachments.c.uploader_id == uploader_id,
                attachments.c.discussion_id.is_(None),
                attachments.c.state == "uploaded",
            )
            .values(discussion_id=discussion_id, state="attached")
        )
        return result.rowcount == len(attachment_ids)

    def update_discussion_optimistic(self, discussion: DiscussionRecord, values: dict[str, object]) -> bool:
        result = self._conn.execute(
            discussions.update()
            .where(
                discussions.c.id == discussion.id,
                discussions.c.updated_at == discussion.updated_at,
                discussions.c.title == discussion.title,
                discussions.c.body_md == discussion.body_md,
                discussions.c.body_format == discussion.body_format.value,
                discussions.c.board_id == discussion.board_id,
                discussions.c.deleted_at.is_(None),
                discussions.c.is_locked == (1 if discussion.is_locked else 0),
            )
            .values(**values)
        )
        return result.rowcount == 1

    def delete_discussion(self, discussion_id: DiscussionID, *, actor_id: UserID, reason: str | None, now: int) -> None:
        self._conn.execute(
            discussions.update()
            .where(discussions.c.id == discussion_id)
            .values(deleted_at=now, deleted_by=actor_id, deletion_reason=reason, updated_at=now)
        )
        self._conn.execute(
            attachments.update().where(attachments.c.discussion_id == discussion_id).values(state="orphaned")
        )

    def insert_reply(self, values: dict[str, object]) -> ReplyID:
        result = self._conn.execute(replies.insert().values(**values))
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("reply insert did not return a primary key")
        return ReplyID(require_int(primary_key[0], "reply id"))

    def increment_reply_count(self, discussion_id: DiscussionID, *, now: int) -> None:
        self._conn.execute(
            discussions.update()
            .where(discussions.c.id == discussion_id)
            .values(reply_count=discussions.c.reply_count + 1, last_reply_at=now, updated_at=now)
        )

    def update_reply_optimistic(
        self, reply: ReplyRecord, *, body_md: str, body_html: str, body_format: str, updated_at: int
    ) -> bool:
        result = self._conn.execute(
            replies.update()
            .where(
                replies.c.id == reply.id,
                replies.c.updated_at == reply.updated_at,
                replies.c.deleted_at.is_(None),
                replies.c.body_md == reply.body_md,
                replies.c.body_format == reply.body_format.value,
            )
            .values(body_md=body_md, body_html=body_html, body_format=body_format, updated_at=updated_at)
        )
        return result.rowcount == 1

    def delete_reply(self, reply: ReplyRecord, *, actor_id: UserID, reason: str | None, now: int) -> None:
        self._conn.execute(
            replies.update()
            .where(replies.c.id == reply.id)
            .values(deleted_at=now, deleted_by=actor_id, deletion_reason=reason, updated_at=now)
        )
        self._conn.execute(
            discussions.update()
            .where(discussions.c.id == reply.discussion_id)
            .values(reply_count=discussions.c.reply_count - 1)
        )

    def save_discussion(self, user_id: UserID, discussion_id: DiscussionID, *, now: int) -> bool:
        if (
            self._conn.execute(
                select(discussion_saves.c.discussion_id).where(
                    discussion_saves.c.user_id == user_id, discussion_saves.c.discussion_id == discussion_id
                )
            ).first()
            is not None
        ):
            return False
        self._conn.execute(
            discussion_saves.insert().values(user_id=user_id, discussion_id=discussion_id, created_at=now)
        )
        self._conn.execute(
            discussions.update()
            .where(discussions.c.id == discussion_id)
            .values(save_count=discussions.c.save_count + 1)
        )
        return True

    def unsave_discussion(self, user_id: UserID, discussion_id: DiscussionID) -> bool:
        if (
            self._conn.execute(
                select(discussion_saves.c.discussion_id).where(
                    discussion_saves.c.user_id == user_id, discussion_saves.c.discussion_id == discussion_id
                )
            ).first()
            is None
        ):
            return False
        self._conn.execute(
            discussion_saves.delete().where(
                discussion_saves.c.user_id == user_id, discussion_saves.c.discussion_id == discussion_id
            )
        )
        self._conn.execute(
            discussions.update()
            .where(discussions.c.id == discussion_id)
            .values(save_count=func.max(discussions.c.save_count - 1, 0))
        )
        return True

    def follow_discussion(self, user_id: UserID, discussion_id: DiscussionID, *, now: int) -> bool:
        if (
            self._conn.execute(
                select(discussion_follows.c.discussion_id).where(
                    discussion_follows.c.user_id == user_id, discussion_follows.c.discussion_id == discussion_id
                )
            ).first()
            is not None
        ):
            return False
        self._conn.execute(
            discussion_follows.insert().values(user_id=user_id, discussion_id=discussion_id, created_at=now)
        )
        return True

    def unfollow_discussion(self, user_id: UserID, discussion_id: DiscussionID) -> None:
        self._conn.execute(
            discussion_follows.delete().where(
                discussion_follows.c.user_id == user_id, discussion_follows.c.discussion_id == discussion_id
            )
        )

    def pinned_count(self, board_id: BoardID) -> int:
        return int(
            self._conn.execute(
                select(func.count())
                .select_from(discussions)
                .where(
                    discussions.c.board_id == board_id, discussions.c.is_pinned == 1, discussions.c.deleted_at.is_(None)
                )
            ).scalar_one()
        )

    def set_toggle(self, discussion_id: DiscussionID, field: str, value: int) -> None:
        self._conn.execute(discussions.update().where(discussions.c.id == discussion_id).values(**{field: value}))

    def saved_discussion_ids(self, user_id: UserID) -> list[DiscussionID]:
        return [
            DiscussionID(value)
            for value in self._conn.execute(
                select(discussion_saves.c.discussion_id).where(discussion_saves.c.user_id == user_id)
            )
            .scalars()
            .all()
        ]

    def discussion_ids(self, conditions: Sequence[ColumnElement[bool]]) -> list[DiscussionID]:
        return [
            DiscussionID(value)
            for value in self._conn.execute(select(discussions.c.id).where(and_(*conditions))).scalars().all()
        ]
