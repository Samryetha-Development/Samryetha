"""关注 service — 镜像 backend/src/follows/service.ts。"""

from __future__ import annotations

from sqlalchemy import and_, delete, select
from sqlalchemy.engine import Connection

from ..authz import Abilities, Actor, assert_can
from ..db import now_ms
from ..errors import internal_error, not_found
from ..events.outbox import emit_event
from ..notifications.models import UserFollowedPayload
from ..ids import UserID
from ..schema import user_follows, users
from .service import normalize_username


def get_user_id_by_username(conn: Connection, username: str) -> UserID | None:
    value = conn.execute(
        select(users.c.id).where(and_(users.c.username == normalize_username(username), users.c.deleted_at.is_(None)))
    ).scalar_one_or_none()
    return UserID(value) if value is not None else None


def follow_user(conn: Connection, actor: Actor | None, followee_id: UserID) -> None:
    if actor is None:
        raise internal_error()
    assert_can(actor, Abilities.USER_FOLLOW, {"type": "user", "id": followee_id}, conn)
    followee = conn.execute(select(users.c.id).where(users.c.id == followee_id)).first()
    if followee is None:
        raise not_found("User not found")
    existing = conn.execute(
        select(user_follows.c.followee_id).where(
            and_(
                user_follows.c.follower_id == actor.id,
                user_follows.c.followee_id == followee_id,
            )
        )
    ).first()
    if existing:
        return
    conn.execute(user_follows.insert().values(follower_id=actor.id, followee_id=followee_id, created_at=now_ms()))
    emit_event(
        conn,
        "user.followed",
        aggregate_type="user",
        aggregate_id=str(followee_id),
        payload=UserFollowedPayload(follower_id=actor.id, followee_id=followee_id),
    )


def unfollow_user(conn: Connection, actor: Actor | None, followee_id: UserID) -> None:
    if actor is None:
        raise internal_error()
    conn.execute(
        delete(user_follows).where(
            and_(
                user_follows.c.follower_id == actor.id,
                user_follows.c.followee_id == followee_id,
            )
        )
    )


def is_following(conn: Connection, follower_id: UserID, followee_id: UserID) -> bool:
    row = conn.execute(
        select(user_follows.c.followee_id).where(
            and_(
                user_follows.c.follower_id == follower_id,
                user_follows.c.followee_id == followee_id,
            )
        )
    ).first()
    return row is not None
