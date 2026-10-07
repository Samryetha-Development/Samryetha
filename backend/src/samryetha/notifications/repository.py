"""Typed persistence operations for notifications."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import and_, false, func, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..core.db import now_ms
from ..discussions import moderation_visible
from ..core.ids import DiscussionID, NotificationID, OutboxEventID, ReplyID, UserID
from ..core.schema import board_members, boards, discussions, notifications, replies, users
from .models import (
    CreateNotification,
    NotificationActor,
    NotificationRecord,
    NotificationType,
    NotificationViewer,
    UserRole,
    UserStatus,
)


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    return None if value is None else _required_int(value, field)


def _optional_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _record(row: RowMapping) -> NotificationRecord:
    raw_type = row["type"]
    if not isinstance(raw_type, str):
        raise ValueError("notification type must be a string")
    return NotificationRecord(
        id=NotificationID(_required_int(row["id"], "id")),
        user_id=UserID(_required_int(row["user_id"], "user_id")),
        actor_user_id=(
            UserID(value) if (value := _optional_int(row["actor_user_id"], "actor_user_id")) is not None else None
        ),
        type=NotificationType(raw_type),
        discussion_id=(
            DiscussionID(value) if (value := _optional_int(row["discussion_id"], "discussion_id")) is not None else None
        ),
        reply_id=ReplyID(value) if (value := _optional_int(row["reply_id"], "reply_id")) is not None else None,
        body=_optional_str(row["body"], "body"),
        source_event_id=(
            OutboxEventID(value)
            if (value := _optional_int(row["source_event_id"], "source_event_id")) is not None
            else None
        ),
        is_read=_required_int(row["is_read"], "is_read") == 1,
        read_at=_optional_int(row["read_at"], "read_at"),
        created_at=_required_int(row["created_at"], "created_at"),
    )


def _actor(row: RowMapping) -> NotificationActor:
    username = row["username"]
    display_name = row["display_name"]
    if not isinstance(username, str) or not isinstance(display_name, str):
        raise ValueError("notification actor names must be strings")
    return NotificationActor(
        id=UserID(_required_int(row["id"], "id")),
        username=username,
        discriminator=_optional_int(row["discriminator"], "discriminator"),
        display_name=display_name,
    )


def insert_notification(conn: Connection, command: CreateNotification) -> NotificationID:
    result = conn.execute(
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
    return NotificationID(_required_int(primary_key[0], "inserted notification id"))


def actor_map(conn: Connection, actor_ids: Sequence[UserID]) -> dict[UserID, NotificationActor]:
    if not actor_ids:
        return {}
    rows = conn.execute(select(users).where(users.c.id.in_(actor_ids))).mappings().all()
    actors = (_actor(row) for row in rows)
    return {actor.id: actor for actor in actors}


def _viewer(conn: Connection, user_id: UserID) -> NotificationViewer | None:
    row = conn.execute(
        select(users.c.id, users.c.role, users.c.status).where(
            users.c.id == user_id, users.c.deleted_at.is_(None), users.c.status == "active"
        )
    ).mappings().first()
    if row is None:
        return None
    role = row["role"]
    status = row["status"]
    if not isinstance(role, str) or not isinstance(status, str):
        raise ValueError("notification viewer role and status must be strings")
    return NotificationViewer(
        id=UserID(_required_int(row["id"], "viewer id")),
        role=UserRole(role),
        status=UserStatus(status),
    )


def content_access_predicate(
    conn: Connection,
    user_id: UserID,
    discussion_id: object,
    reply_id: object | None = None,
) -> ColumnElement[bool]:
    viewer = _viewer(conn, user_id)
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
    visibility = moderation_visible(discussions.c.moderation_status, discussions.c.author_id, viewer)
    if visibility is not None:
        conditions.append(visibility)
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
    reply_visibility = moderation_visible(replies.c.moderation_status, replies.c.author_id, viewer)
    if reply_visibility is not None:
        reply_conditions.append(reply_visibility)
    return and_(discussion_access, select(replies.c.id).where(*reply_conditions).exists())


def visible_predicate(conn: Connection, user_id: UserID) -> ColumnElement[bool]:
    return or_(
        notifications.c.type.not_in([NotificationType.Reply.value, NotificationType.Mention.value]),
        and_(
            content_access_predicate(conn, user_id, notifications.c.discussion_id),
            or_(
                notifications.c.reply_id.is_(None),
                content_access_predicate(conn, user_id, notifications.c.discussion_id, notifications.c.reply_id),
            ),
        ),
    )


def can_receive_content(
    conn: Connection,
    user_id: UserID,
    discussion_id: DiscussionID,
    reply_id: ReplyID | None = None,
) -> bool:
    predicate = content_access_predicate(conn, user_id, discussion_id, reply_id)
    return bool(conn.execute(select(predicate)).scalar_one())


def list_records(
    conn: Connection,
    user_id: UserID,
    *,
    unread_only: bool,
    before_id: NotificationID | None,
    limit: int,
) -> tuple[list[NotificationRecord], bool]:
    conditions: list[ColumnElement[bool]] = [notifications.c.user_id == user_id, visible_predicate(conn, user_id)]
    if unread_only:
        conditions.append(notifications.c.is_read == 0)
    if before_id is not None:
        conditions.append(notifications.c.id < before_id)
    rows: Sequence[RowMapping] = conn.execute(
        select(notifications)
        .where(and_(*conditions))
        .order_by(notifications.c.id.desc())
        .limit(limit + 1)
    ).mappings().all()
    return [_record(row) for row in rows[:limit]], len(rows) > limit


def count_unread(conn: Connection, user_id: UserID) -> int:
    value = conn.execute(
        select(func.count())
        .select_from(notifications)
        .where(
            and_(
                notifications.c.user_id == user_id,
                notifications.c.is_read == 0,
                visible_predicate(conn, user_id),
            )
        )
    ).scalar_one()
    return int(value)


def owns_notification(conn: Connection, user_id: UserID, notification_id: NotificationID) -> bool:
    return conn.execute(
        select(notifications.c.id).where(
            notifications.c.id == notification_id, notifications.c.user_id == user_id
        )
    ).first() is not None


def mark_read(conn: Connection, notification_id: NotificationID) -> None:
    conn.execute(
        update(notifications)
        .where(notifications.c.id == notification_id)
        .values(is_read=1, read_at=now_ms())
    )


def mark_all_read(conn: Connection, user_id: UserID) -> None:
    conn.execute(
        update(notifications)
        .where(notifications.c.user_id == user_id)
        .values(is_read=1, read_at=now_ms())
    )
