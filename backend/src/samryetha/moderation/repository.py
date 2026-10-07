"""Typed SQLAlchemy persistence boundary for moderation reports and actions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..core.ids import DiscussionID, ModerationActionID, ReplyID, ReportID, UserID
from ..core.records import opt_int, opt_str, require_int, require_str
from ..core.schema import attachments, bans, boards, discussions, moderation_actions, replies, reports, sessions, users
from .models import ReportableType, ReportStatus


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    id: UserID
    username: str
    discriminator: int
    display_name: str


@dataclass(frozen=True, slots=True)
class ReportRecord:
    id: ReportID
    reporter_user_id: UserID
    reportable_type: ReportableType
    reportable_id: int
    reason: str | None
    status: ReportStatus
    created_at: int


@dataclass(frozen=True, slots=True)
class ActionRecord:
    id: ModerationActionID
    actor_user_id: UserID
    action: str
    target_type: str
    target_id: int
    reason: str | None
    created_at: int


@dataclass(frozen=True, slots=True)
class DiscussionTargetRecord:
    id: DiscussionID
    title: str
    board_slug: str


@dataclass(frozen=True, slots=True)
class ReplyTargetRecord:
    id: ReplyID
    discussion_id: DiscussionID


@dataclass(frozen=True, slots=True)
class BanTargetRecord:
    id: UserID
    role: str


@dataclass(frozen=True, slots=True)
class DeletedContentRecord:
    deleted: bool
    discussion_id: DiscussionID | None = None


def _author(row: RowMapping) -> AuthorRecord:
    return AuthorRecord(
        id=UserID(require_int(row["id"], "id")),
        username=require_str(row["username"], "username"),
        discriminator=require_int(row["discriminator"], "discriminator"),
        display_name=require_str(row["display_name"], "display_name"),
    )


def _report(row: RowMapping) -> ReportRecord:
    return ReportRecord(
        id=ReportID(require_int(row["id"], "id")),
        reporter_user_id=UserID(require_int(row["reporter_user_id"], "reporter_user_id")),
        reportable_type=ReportableType(require_str(row["reportable_type"], "reportable_type")),
        reportable_id=require_int(row["reportable_id"], "reportable_id"),
        reason=opt_str(row["reason"], "reason"),
        status=ReportStatus(require_str(row["status"], "status")),
        created_at=require_int(row["created_at"], "created_at"),
    )


def _action(row: RowMapping) -> ActionRecord:
    return ActionRecord(
        id=ModerationActionID(require_int(row["id"], "id")),
        actor_user_id=UserID(require_int(row["actor_user_id"], "actor_user_id")),
        action=require_str(row["action"], "action"),
        target_type=require_str(row["target_type"], "target_type"),
        target_id=require_int(row["target_id"], "target_id"),
        reason=opt_str(row["reason"], "reason"),
        created_at=require_int(row["created_at"], "created_at"),
    )


class ModerationRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert_report(
        self,
        *,
        reporter_user_id: UserID,
        reportable_type: ReportableType,
        reportable_id: int,
        reason: str | None,
        created_at: int,
    ) -> ReportID:
        result = self._conn.execute(
            reports.insert().values(
                reporter_user_id=reporter_user_id,
                reportable_type=reportable_type.value,
                reportable_id=reportable_id,
                reason=reason,
                status=ReportStatus.Open.value,
                created_at=created_at,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("report insert did not return a primary key")
        return ReportID(require_int(primary_key[0], "inserted report id"))

    def report(self, report_id: ReportID) -> ReportRecord | None:
        row = self._conn.execute(select(reports).where(reports.c.id == report_id)).mappings().first()
        return _report(row) if row is not None else None

    def report_page(self, *, status: ReportStatus | None, cursor: ReportID | None, limit: int) -> list[ReportRecord]:
        conditions: list[ColumnElement[bool]] = []
        if status is not None:
            conditions.append(reports.c.status == status.value)
        if cursor is not None:
            conditions.append(reports.c.id < cursor)
        statement = select(reports)
        if conditions:
            statement = statement.where(and_(*conditions))
        rows: Sequence[RowMapping] = (
            self._conn.execute(statement.order_by(reports.c.id.desc()).limit(limit + 1)).mappings().all()
        )
        return [_report(row) for row in rows]

    def update_report_status(self, report_id: ReportID, status: ReportStatus) -> None:
        self._conn.execute(update(reports).where(reports.c.id == report_id).values(status=status.value))

    def authors(self, user_ids: Iterable[UserID]) -> dict[UserID, AuthorRecord]:
        values = tuple(set(user_ids))
        if not values:
            return {}
        rows: Sequence[RowMapping] = self._conn.execute(select(users).where(users.c.id.in_(values))).mappings().all()
        records = (_author(row) for row in rows)
        return {record.id: record for record in records}

    def discussion_targets(self, discussion_ids: Iterable[DiscussionID]) -> dict[DiscussionID, DiscussionTargetRecord]:
        values = tuple(set(discussion_ids))
        if not values:
            return {}
        rows = (
            self._conn.execute(
                select(discussions.c.id, discussions.c.title, boards.c.slug.label("board_slug"))
                .select_from(discussions.join(boards, boards.c.id == discussions.c.board_id))
                .where(discussions.c.id.in_(values))
            )
            .mappings()
            .all()
        )
        records = (
            DiscussionTargetRecord(
                id=DiscussionID(require_int(row["id"], "id")),
                title=require_str(row["title"], "title"),
                board_slug=require_str(row["board_slug"], "board_slug"),
            )
            for row in rows
        )
        return {record.id: record for record in records}

    def reply_targets(self, reply_ids: Iterable[ReplyID]) -> dict[ReplyID, ReplyTargetRecord]:
        values = tuple(set(reply_ids))
        if not values:
            return {}
        rows = (
            self._conn.execute(select(replies.c.id, replies.c.discussion_id).where(replies.c.id.in_(values)))
            .mappings()
            .all()
        )
        records = (
            ReplyTargetRecord(
                id=ReplyID(require_int(row["id"], "id")),
                discussion_id=DiscussionID(require_int(row["discussion_id"], "discussion_id")),
            )
            for row in rows
        )
        return {record.id: record for record in records}

    def insert_action(
        self,
        *,
        actor_user_id: UserID,
        action: str,
        target_type: str,
        target_id: int,
        reason: str | None,
        created_at: int,
    ) -> None:
        self._conn.execute(
            moderation_actions.insert().values(
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                created_at=created_at,
            )
        )

    def action_page(self, *, cursor: ModerationActionID | None, limit: int) -> list[ActionRecord]:
        statement = select(moderation_actions)
        if cursor is not None:
            statement = statement.where(moderation_actions.c.id < cursor)
        rows: Sequence[RowMapping] = (
            self._conn.execute(statement.order_by(moderation_actions.c.id.desc()).limit(limit + 1)).mappings().all()
        )
        return [_action(row) for row in rows]

    def ban_target(self, username: str) -> BanTargetRecord | None:
        row = (
            self._conn.execute(
                select(users.c.id, users.c.role).where((users.c.username == username) & users.c.deleted_at.is_(None))
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return BanTargetRecord(
            id=UserID(require_int(row["id"], "id")),
            role=require_str(row["role"], "role"),
        )

    def insert_ban(
        self,
        *,
        user_id: UserID,
        banned_by_user_id: UserID,
        reason: str | None,
        banned_until: int | None,
        created_at: int,
    ) -> None:
        self._conn.execute(
            bans.insert().values(
                user_id=user_id,
                banned_by_user_id=banned_by_user_id,
                reason=reason,
                banned_until=banned_until,
                is_active=1,
                created_at=created_at,
            )
        )

    def set_user_status(self, user_id: UserID, status: str, *, updated_at: int) -> None:
        self._conn.execute(update(users).where(users.c.id == user_id).values(status=status, updated_at=updated_at))

    def delete_sessions(self, user_id: UserID) -> None:
        self._conn.execute(delete(sessions).where(sessions.c.user_id == user_id))

    def deactivate_active_bans(self, user_id: UserID) -> None:
        self._conn.execute(
            update(bans).where((bans.c.user_id == user_id) & (bans.c.is_active == 1)).values(is_active=0)
        )

    def active_ban_deadlines(self, user_id: UserID) -> list[int | None]:
        values = (
            self._conn.execute(select(bans.c.banned_until).where((bans.c.user_id == user_id) & (bans.c.is_active == 1)))
            .scalars()
            .all()
        )
        return [opt_int(value, "banned_until") for value in values]

    def discussion_deletion(self, discussion_id: DiscussionID) -> DeletedContentRecord | None:
        row = self._conn.execute(select(discussions.c.deleted_at).where(discussions.c.id == discussion_id)).first()
        return DeletedContentRecord(deleted=row.deleted_at is not None) if row is not None else None

    def restore_discussion(self, discussion_id: DiscussionID, *, updated_at: int) -> None:
        self._conn.execute(
            update(discussions)
            .where(discussions.c.id == discussion_id)
            .values(deleted_at=None, deleted_by=None, deletion_reason=None, updated_at=updated_at)
        )

    def restore_discussion_attachments(self, discussion_id: DiscussionID) -> None:
        self._conn.execute(
            update(attachments)
            .where((attachments.c.discussion_id == discussion_id) & (attachments.c.state == "orphaned"))
            .values(state="attached")
        )

    def reply_deletion(self, reply_id: ReplyID) -> DeletedContentRecord | None:
        row = (
            self._conn.execute(select(replies.c.deleted_at, replies.c.discussion_id).where(replies.c.id == reply_id))
            .mappings()
            .first()
        )
        if row is None:
            return None
        return DeletedContentRecord(
            deleted=row["deleted_at"] is not None,
            discussion_id=DiscussionID(require_int(row["discussion_id"], "discussion_id")),
        )

    def restore_reply(self, reply_id: ReplyID, *, updated_at: int) -> None:
        self._conn.execute(
            update(replies)
            .where(replies.c.id == reply_id)
            .values(deleted_at=None, deleted_by=None, deletion_reason=None, updated_at=updated_at)
        )

    def increment_reply_count(self, discussion_id: DiscussionID) -> None:
        self._conn.execute(
            update(discussions)
            .where(discussions.c.id == discussion_id)
            .values(reply_count=discussions.c.reply_count + 1)
        )
