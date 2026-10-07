"""Typed SQLAlchemy persistence boundary for administration workflows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import Table, and_, delete, func, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from .models import AdminAssignableRole
from ..core.ids import BoardID, DiscussionID, ReplyID, UserID
from ..core.records import opt_int, opt_str, require_int, require_str
from ..core.schema import bans, boards, discussions, moderation_actions, replies, reports, sessions, users
from ..users.models import AccountStatus, UserRow
from ..users.repository import user_row_from_mapping


@dataclass(frozen=True, slots=True)
class UserAdminRecord:
    user: UserRow
    ban_active: bool
    report_count: int


@dataclass(frozen=True, slots=True)
class StatsRecord:
    distribution: dict[AccountStatus, int]
    active_today: int
    discussions: int
    replies: int
    boards: int
    open_reports: int
    active_bans: int
    new_users_today: int
    new_discussions_today: int
    new_replies_today: int


@dataclass(frozen=True, slots=True)
class DeletedDiscussionRecord:
    id: DiscussionID
    board_id: BoardID
    title: str
    body_md: str
    deleted_by: UserID | None
    deleted_at: int
    reason: str | None


@dataclass(frozen=True, slots=True)
class DeletedReplyRecord:
    id: ReplyID
    discussion_id: DiscussionID
    body_md: str
    deleted_by: UserID | None
    deleted_at: int
    reason: str | None


class AdminRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def _count(self, table: Table, condition: ColumnElement[bool] | None = None) -> int:
        statement = select(func.count()).select_from(table)
        if condition is not None:
            statement = statement.where(condition)
        return require_int(self._conn.execute(statement).scalar_one(), "count")

    def stats(self, *, today: int) -> StatsRecord:
        distribution = {status: 0 for status in AccountStatus}
        for raw_status, raw_count in self._conn.execute(select(users.c.status, func.count()).group_by(users.c.status)):
            distribution[AccountStatus(require_str(raw_status, "status"))] = require_int(raw_count, "count")
        author_ids = {
            UserID(require_int(value, "author_id"))
            for value in self._conn.execute(
                select(discussions.c.author_id)
                .where(discussions.c.created_at > today, discussions.c.deleted_at.is_(None))
                .union(select(replies.c.author_id).where(replies.c.created_at > today, replies.c.deleted_at.is_(None)))
            ).scalars()
        }
        return StatsRecord(
            distribution=distribution,
            active_today=len(author_ids),
            discussions=self._count(discussions, discussions.c.deleted_at.is_(None)),
            replies=self._count(replies, replies.c.deleted_at.is_(None)),
            boards=self._count(boards, boards.c.deleted_at.is_(None)),
            open_reports=self._count(reports, reports.c.status.in_(["open", "in_progress"])),
            active_bans=self._count(bans, bans.c.is_active == 1),
            new_users_today=self._count(users, users.c.created_at > today),
            new_discussions_today=self._count(
                discussions, (discussions.c.created_at > today) & discussions.c.deleted_at.is_(None)
            ),
            new_replies_today=self._count(replies, (replies.c.created_at > today) & replies.c.deleted_at.is_(None)),
        )

    def user(self, user_id: UserID) -> UserRow | None:
        row = self._conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
        return user_row_from_mapping(row) if row is not None else None

    def user_admin_record(self, user_id: UserID) -> UserAdminRecord | None:
        value = self.user(user_id)
        if value is None:
            return None
        ban_active = (
            self._conn.execute(select(bans.c.id).where(bans.c.user_id == user_id, bans.c.is_active == 1)).first()
            is not None
        )
        report_count = require_int(
            self._conn.execute(
                select(func.count())
                .select_from(reports)
                .where(reports.c.reportable_type == "user", reports.c.reportable_id == user_id)
            ).scalar_one(),
            "report count",
        )
        return UserAdminRecord(value, ban_active, report_count)

    def user_page(
        self,
        *,
        query_pattern: str | None,
        status: AccountStatus | None,
        role: AdminAssignableRole | None,
        cursor: UserID | None,
        limit: int,
        exclude_pending: bool,
    ) -> list[UserAdminRecord]:
        conditions: list[ColumnElement[bool]] = [users.c.deleted_at.is_(None)]
        if query_pattern is not None:
            conditions.append(
                or_(
                    users.c.username.like(query_pattern, escape="\\"),
                    users.c.display_name.like(query_pattern, escape="\\"),
                    users.c.email.like(query_pattern, escape="\\"),
                )
            )
        if status is not None:
            conditions.append(users.c.status == status.value)
        if exclude_pending:
            conditions.append(users.c.status != AccountStatus.Pending.value)
        if role is not None:
            conditions.append(users.c.role == role.value)
        if cursor is not None:
            conditions.append(users.c.id < cursor)
        rows: Sequence[RowMapping] = (
            self._conn.execute(select(users).where(and_(*conditions)).order_by(users.c.id.desc()).limit(limit + 1))
            .mappings()
            .all()
        )
        user_rows = [user_row_from_mapping(row) for row in rows]
        ids = [UserID(row["id"]) for row in user_rows]
        banned_ids: set[UserID] = set()
        if ids:
            banned_ids = {
                UserID(require_int(value, "user_id"))
                for value in self._conn.execute(
                    select(bans.c.user_id).where(bans.c.user_id.in_(ids), bans.c.is_active == 1)
                ).scalars()
            }
        report_counts: dict[UserID, int] = {}
        if ids:
            for raw_id, raw_count in self._conn.execute(
                select(reports.c.reportable_id, func.count())
                .where(reports.c.reportable_type == "user", reports.c.reportable_id.in_(ids))
                .group_by(reports.c.reportable_id)
            ):
                report_counts[UserID(require_int(raw_id, "reportable_id"))] = require_int(raw_count, "count")
        return [
            UserAdminRecord(row, UserID(row["id"]) in banned_ids, report_counts.get(UserID(row["id"]), 0))
            for row in user_rows
        ]

    def admin_count(self) -> int:
        return self._count(users, users.c.role == "admin")

    def update_user(self, user_id: UserID, values: dict[str, object]) -> None:
        self._conn.execute(update(users).where(users.c.id == user_id).values(**values))

    def delete_user_sessions(self, user_id: UserID) -> None:
        self._conn.execute(delete(sessions).where(sessions.c.user_id == user_id))

    def log_action(
        self, *, actor_id: UserID, action: str, target_id: UserID, reason: str | None, created_at: int
    ) -> None:
        self._conn.execute(
            moderation_actions.insert().values(
                actor_user_id=actor_id,
                action=action,
                target_type="user",
                target_id=target_id,
                reason=reason,
                created_at=created_at,
            )
        )

    def deleted_discussions(self, cursor: DiscussionID | None, *, limit: int) -> list[DeletedDiscussionRecord]:
        conditions: list[ColumnElement[bool]] = [discussions.c.deleted_at.is_not(None)]
        if cursor is not None:
            conditions.append(discussions.c.id < cursor)
        rows = (
            self._conn.execute(
                select(discussions).where(and_(*conditions)).order_by(discussions.c.id.desc()).limit(limit + 1)
            )
            .mappings()
            .all()
        )
        return [
            DeletedDiscussionRecord(
                id=DiscussionID(require_int(row["id"], "id")),
                board_id=BoardID(require_int(row["board_id"], "board_id")),
                title=require_str(row["title"], "title"),
                body_md=require_str(row["body_md"], "body_md"),
                deleted_by=UserID(value) if (value := opt_int(row["deleted_by"], "deleted_by")) is not None else None,
                deleted_at=require_int(row["deleted_at"], "deleted_at"),
                reason=opt_str(row["deletion_reason"], "deletion_reason"),
            )
            for row in rows
        ]

    def deleted_replies(self, cursor: ReplyID | None, *, limit: int) -> list[DeletedReplyRecord]:
        conditions: list[ColumnElement[bool]] = [replies.c.deleted_at.is_not(None)]
        if cursor is not None:
            conditions.append(replies.c.id < cursor)
        rows = (
            self._conn.execute(select(replies).where(and_(*conditions)).order_by(replies.c.id.desc()).limit(limit + 1))
            .mappings()
            .all()
        )
        return [
            DeletedReplyRecord(
                id=ReplyID(require_int(row["id"], "id")),
                discussion_id=DiscussionID(require_int(row["discussion_id"], "discussion_id")),
                body_md=require_str(row["body_md"], "body_md"),
                deleted_by=UserID(value) if (value := opt_int(row["deleted_by"], "deleted_by")) is not None else None,
                deleted_at=require_int(row["deleted_at"], "deleted_at"),
                reason=opt_str(row["deletion_reason"], "deletion_reason"),
            )
            for row in rows
        ]

    def board_slugs(self, board_ids: Iterable[BoardID]) -> dict[BoardID, str]:
        values = tuple(set(board_ids))
        if not values:
            return {}
        return {
            BoardID(require_int(row["id"], "id")): require_str(row["slug"], "slug")
            for row in self._conn.execute(select(boards.c.id, boards.c.slug).where(boards.c.id.in_(values))).mappings()
        }

    def users_by_ids(self, user_ids: Iterable[UserID]) -> dict[UserID, UserRow]:
        values = tuple(set(user_ids))
        if not values:
            return {}
        records = (
            user_row_from_mapping(row)
            for row in self._conn.execute(select(users).where(users.c.id.in_(values))).mappings()
        )
        return {UserID(record["id"]): record for record in records}

    def discussion_titles(self, discussion_ids: Iterable[DiscussionID]) -> dict[DiscussionID, str]:
        values = tuple(set(discussion_ids))
        if not values:
            return {}
        return {
            DiscussionID(require_int(row["id"], "id")): require_str(row["title"], "title")
            for row in self._conn.execute(
                select(discussions.c.id, discussions.c.title).where(discussions.c.id.in_(values))
            ).mappings()
        }
