"""Typed persistence operations for notifications."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import and_, false, func, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..core.db import now_ms
from ..core.ids import DiscussionID, NotificationID, OutboxEventID, ReplyID, UserID
from ..core.schema import board_members, boards, discussions, notifications, replies, users
from ..core.records import opt_int, opt_str, require_int
from .models import (
    CreateNotification,
    NotificationActor,
    NotificationRecord,
    NotificationType,
    NotificationViewer,
    UserRole,
    UserStatus,
)


def _record(row: RowMapping) -> NotificationRecord:
    raw_type = row["type"]
    if not isinstance(raw_type, str):
        raise ValueError("notification type must be a string")
    return NotificationRecord(
        id=NotificationID(require_int(row["id"], "id")),
        user_id=UserID(require_int(row["user_id"], "user_id")),
        actor_user_id=(
            UserID(value) if (value := opt_int(row["actor_user_id"], "actor_user_id")) is not None else None
        ),
        type=NotificationType(raw_type),
        discussion_id=(
            DiscussionID(value) if (value := opt_int(row["discussion_id"], "discussion_id")) is not None else None
        ),
        reply_id=ReplyID(value) if (value := opt_int(row["reply_id"], "reply_id")) is not None else None,
        body=opt_str(row["body"], "body"),
        source_event_id=(
            OutboxEventID(value) if (value := opt_int(row["source_event_id"], "source_event_id")) is not None else None
        ),
        is_read=require_int(row["is_read"], "is_read") == 1,
        read_at=opt_int(row["read_at"], "read_at"),
        created_at=require_int(row["created_at"], "created_at"),
    )


def _actor(row: RowMapping) -> NotificationActor:
    username = row["username"]
    display_name = row["display_name"]
    if not isinstance(username, str) or not isinstance(display_name, str):
        raise ValueError("notification actor names must be strings")
    return NotificationActor(
        id=UserID(require_int(row["id"], "id")),
        username=username,
        discriminator=opt_int(row["discriminator"], "discriminator"),
        display_name=display_name,
    )


class NotificationRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert_notification(self, command: CreateNotification) -> NotificationID:
        result = self._conn.execute(
            notifications.insert().values(
                user_id=command.user_id,
                actor_user_id=command.actor_user_id,
                type=command.type.value,
                discussion_id=command.discussion_id,
                reply_id=command.reply_id,
                body=command.body,
                source_event_id=command.source_event_id,
                is_read=0,
                created_at=now_ms(),
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("notification insert did not return a primary key")
        return NotificationID(require_int(primary_key[0], "inserted notification id"))

    def actor_map(self, actor_ids: Sequence[UserID]) -> dict[UserID, NotificationActor]:
        if not actor_ids:
            return {}
        rows = self._conn.execute(select(users).where(users.c.id.in_(actor_ids))).mappings().all()
        actors = (_actor(row) for row in rows)
        return {actor.id: actor for actor in actors}

    def _viewer(self, user_id: UserID) -> NotificationViewer | None:
        row = (
            self._conn.execute(
                select(users.c.id, users.c.role, users.c.status).where(
                    users.c.id == user_id, users.c.deleted_at.is_(None), users.c.status == "active"
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        role = row["role"]
        status = row["status"]
        if not isinstance(role, str) or not isinstance(status, str):
            raise ValueError("notification viewer role and status must be strings")
        return NotificationViewer(
            id=UserID(require_int(row["id"], "viewer id")),
            role=UserRole(role),
            status=UserStatus(status),
        )

    def content_access_predicate(
        self, user_id: UserID, discussion_id: object, reply_id: object | None = None
    ) -> ColumnElement[bool]:
        viewer = self._viewer(user_id)
        if viewer is None:
            return false()
        conditions: list[ColumnElement[bool]] = [
            discussions.c.id == discussion_id,
            discussions.c.deleted_at.is_(None),
            boards.c.deleted_at.is_(None),
        ]
        if viewer.role is not UserRole.Admin:
            conditions.append(
                or_(
                    boards.c.visibility == "public",
                    select(board_members.c.board_id)
                    .where(board_members.c.board_id == boards.c.id, board_members.c.user_id == user_id)
                    .exists(),
                )
            )
        discussion_access = (
            select(discussions.c.id).join(boards, boards.c.id == discussions.c.board_id).where(*conditions).exists()
        )
        if reply_id is None:
            return discussion_access
        reply_conditions: list[ColumnElement[bool]] = [
            replies.c.id == reply_id,
            replies.c.discussion_id == discussion_id,
            replies.c.deleted_at.is_(None),
        ]
        return and_(discussion_access, select(replies.c.id).where(*reply_conditions).exists())

    def visible_predicate(self, user_id: UserID) -> ColumnElement[bool]:
        return or_(
            notifications.c.type.not_in([NotificationType.Reply.value, NotificationType.Mention.value]),
            and_(
                self.content_access_predicate(user_id, notifications.c.discussion_id),
                or_(
                    notifications.c.reply_id.is_(None),
                    self.content_access_predicate(user_id, notifications.c.discussion_id, notifications.c.reply_id),
                ),
            ),
        )

    def can_receive_content(
        self, user_id: UserID, discussion_id: DiscussionID, reply_id: ReplyID | None = None
    ) -> bool:
        predicate = self.content_access_predicate(user_id, discussion_id, reply_id)
        return bool(self._conn.execute(select(predicate)).scalar_one())

    def list_records(
        self, user_id: UserID, *, unread_only: bool, before_id: NotificationID | None, limit: int
    ) -> tuple[list[NotificationRecord], bool]:
        conditions: list[ColumnElement[bool]] = [notifications.c.user_id == user_id, self.visible_predicate(user_id)]
        if unread_only:
            conditions.append(notifications.c.is_read == 0)
        if before_id is not None:
            conditions.append(notifications.c.id < before_id)
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(notifications).where(and_(*conditions)).order_by(notifications.c.id.desc()).limit(limit + 1)
            )
            .mappings()
            .all()
        )
        return [_record(row) for row in rows[:limit]], len(rows) > limit

    def count_unread(self, user_id: UserID) -> int:
        value = self._conn.execute(
            select(func.count())
            .select_from(notifications)
            .where(
                and_(notifications.c.user_id == user_id, notifications.c.is_read == 0, self.visible_predicate(user_id))
            )
        ).scalar_one()
        return int(value)

    def owns_notification(self, user_id: UserID, notification_id: NotificationID) -> bool:
        return (
            self._conn.execute(
                select(notifications.c.id).where(
                    notifications.c.id == notification_id, notifications.c.user_id == user_id
                )
            ).first()
            is not None
        )

    def mark_read(self, notification_id: NotificationID) -> None:
        self._conn.execute(
            update(notifications).where(notifications.c.id == notification_id).values(is_read=1, read_at=now_ms())
        )

    def mark_all_read(self, user_id: UserID) -> None:
        self._conn.execute(
            update(notifications).where(notifications.c.user_id == user_id).values(is_read=1, read_at=now_ms())
        )
