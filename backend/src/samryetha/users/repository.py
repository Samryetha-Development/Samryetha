"""Typed SQLAlchemy persistence boundary for users and follows."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.ids import UserID
from ..core.records import opt_int, opt_str, require_int, require_str
from ..core.schema import discussions, replies, user_follows, users
from .models import UserRow


@dataclass(frozen=True, slots=True)
class ProfileStatsRecord:
    discussions: int
    replies: int
    followers: int
    following: int


def user_row_from_mapping(row: RowMapping) -> UserRow:
    return {
        "id": require_int(row["id"], "id"),
        "username": require_str(row["username"], "username"),
        "email": require_str(row["email"], "email"),
        "recovery_email": opt_str(row["recovery_email"], "recovery_email"),
        "display_name": require_str(row["display_name"], "display_name"),
        "bio": require_str(row["bio"], "bio"),
        "profile_moderation_status": require_str(row["profile_moderation_status"], "profile_moderation_status"),
        "pending_display_name": opt_str(row["pending_display_name"], "pending_display_name"),
        "pending_bio": opt_str(row["pending_bio"], "pending_bio"),
        "password_hash": require_str(row["password_hash"], "password_hash"),
        "role": require_str(row["role"], "role"),
        "status": require_str(row["status"], "status"),
        "discriminator": opt_int(row["discriminator"], "discriminator"),
        "email_domain": opt_str(row["email_domain"], "email_domain"),
        "email_verified_at": opt_int(row["email_verified_at"], "email_verified_at"),
        "avatar_object_key": opt_str(row["avatar_object_key"], "avatar_object_key"),
        "last_seen_at": opt_int(row["last_seen_at"], "last_seen_at"),
        "settings": require_str(row["settings"], "settings"),
        "created_at": opt_int(row["created_at"], "created_at"),
        "updated_at": opt_int(row["updated_at"], "updated_at"),
        "deleted_at": opt_int(row["deleted_at"], "deleted_at"),
    }


class UserRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get_by_id(self, user_id: UserID) -> UserRow | None:
        row = (
            self._conn.execute(select(users).where(and_(users.c.id == user_id, users.c.deleted_at.is_(None))))
            .mappings()
            .first()
        )
        return user_row_from_mapping(row) if row is not None else None

    def get_by_username(self, username: str) -> UserRow | None:
        row = (
            self._conn.execute(select(users).where(and_(users.c.username == username, users.c.deleted_at.is_(None))))
            .mappings()
            .first()
        )
        return user_row_from_mapping(row) if row is not None else None

    def discriminator_exists(self, discriminator: int) -> bool:
        return self._conn.execute(select(users.c.id).where(users.c.discriminator == discriminator)).first() is not None

    def update_user(self, user_id: UserID, values: dict[str, object]) -> None:
        self._conn.execute(update(users).where(users.c.id == user_id).values(**values))

    def stage_profile(
        self, user_id: UserID, current: UserRow, *, pending_display_name: str, pending_bio: str, updated_at: int
    ) -> bool:
        result = self._conn.execute(
            update(users)
            .where(
                users.c.id == user_id,
                users.c.updated_at == current["updated_at"],
                users.c.status == current["status"],
                users.c.role == current["role"],
                users.c.profile_moderation_status == current["profile_moderation_status"],
                users.c.display_name == current["display_name"],
                users.c.bio == current["bio"],
                users.c.pending_display_name == current["pending_display_name"],
                users.c.pending_bio == current["pending_bio"],
                users.c.deleted_at.is_(None),
            )
            .values(
                pending_display_name=pending_display_name,
                pending_bio=pending_bio,
                profile_moderation_status="pending",
                updated_at=updated_at,
            )
        )
        return result.rowcount == 1

    def username_exists_except(self, username: str, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(users.c.id).where(and_(users.c.username == username, users.c.id != user_id))
            ).first()
            is not None
        )

    def profile_stats(self, user_id: UserID) -> ProfileStatsRecord:
        row = (
            self._conn.execute(
                select(
                    select(func.count())
                    .select_from(discussions)
                    .where(discussions.c.author_id == user_id, discussions.c.deleted_at.is_(None))
                    .scalar_subquery()
                    .label("discussions"),
                    select(func.count())
                    .select_from(replies)
                    .where(replies.c.author_id == user_id, replies.c.deleted_at.is_(None))
                    .scalar_subquery()
                    .label("replies"),
                    select(func.count())
                    .select_from(user_follows)
                    .where(user_follows.c.followee_id == user_id)
                    .scalar_subquery()
                    .label("followers"),
                    select(func.count())
                    .select_from(user_follows)
                    .where(user_follows.c.follower_id == user_id)
                    .scalar_subquery()
                    .label("following"),
                )
            )
            .mappings()
            .one()
        )
        return ProfileStatsRecord(
            discussions=require_int(row["discussions"], "discussion count"),
            replies=require_int(row["replies"], "reply count"),
            followers=require_int(row["followers"], "follower count"),
            following=require_int(row["following"], "following count"),
        )

    def insert_user(
        self,
        *,
        username: str,
        display_name: str,
        email: str,
        email_domain: str,
        password_hash: str,
        discriminator: int,
        created_at: int,
    ) -> UserID:
        result = self._conn.execute(
            users.insert().values(
                username=username,
                display_name=display_name,
                email=email,
                email_domain=email_domain,
                password_hash=password_hash,
                discriminator=discriminator,
                status="pending",
                settings="{}",
                bio="",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("user insert did not return a primary key")
        return UserID(require_int(primary_key[0], "inserted user id"))

    def user_id_by_username(self, username: str) -> UserID | None:
        value = self._conn.execute(
            select(users.c.id).where(and_(users.c.username == username, users.c.deleted_at.is_(None)))
        ).scalar_one_or_none()
        return UserID(require_int(value, "user id")) if value is not None else None

    def user_exists(self, user_id: UserID) -> bool:
        return self._conn.execute(select(users.c.id).where(users.c.id == user_id)).first() is not None

    def is_following(self, follower_id: UserID, followee_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(user_follows.c.followee_id).where(
                    and_(user_follows.c.follower_id == follower_id, user_follows.c.followee_id == followee_id)
                )
            ).first()
            is not None
        )

    def insert_follow(self, follower_id: UserID, followee_id: UserID, *, created_at: int) -> None:
        self._conn.execute(
            user_follows.insert().values(follower_id=follower_id, followee_id=followee_id, created_at=created_at)
        )

    def delete_follow(self, follower_id: UserID, followee_id: UserID) -> None:
        self._conn.execute(
            delete(user_follows).where(
                and_(user_follows.c.follower_id == follower_id, user_follows.c.followee_id == followee_id)
            )
        )
