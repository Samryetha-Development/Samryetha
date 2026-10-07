"""关注 service — 镜像 backend/src/follows/service.ts。"""

from __future__ import annotations

from samryetha.users.repository import UserRepository

from sqlalchemy.engine import Connection

from ..authz import Abilities, Actor, AuthorizationService
from ..core.db import now_ms
from ..core.errors import internal_error, not_found
from ..events.outbox import OutboxWriter
from ..notifications.models import UserFollowedPayload
from ..core.ids import UserID
from .service import normalize_username


class FollowService:
    """Application use cases within the caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._outbox = OutboxWriter(self._conn)
        self._repository = UserRepository(self._conn)

    def get_user_id_by_username(self, username: str) -> UserID | None:
        return self._repository.user_id_by_username(normalize_username(username))

    def follow_username(self, actor: Actor, username: str) -> None:
        target_id = self.get_user_id_by_username(username)
        if target_id is None:
            # Preserve the existing HTTP 500 for an unknown follow target.
            raise internal_error()
        self.follow_user(actor, target_id)

    def unfollow_username(self, actor: Actor, username: str) -> None:
        target_id = self.get_user_id_by_username(username)
        if target_id is None:
            raise internal_error()
        self.unfollow_user(actor, target_id)

    def follow_user(self, actor: Actor | None, followee_id: UserID) -> None:
        if actor is None:
            raise internal_error()
        AuthorizationService(self._conn).assert_can(actor, Abilities.USER_FOLLOW, {"type": "user", "id": followee_id})
        if not self._repository.user_exists(followee_id):
            raise not_found("User not found")
        actor_id = UserID(actor.id)
        if self._repository.is_following(actor_id, followee_id):
            return
        self._repository.insert_follow(actor_id, followee_id, created_at=now_ms())
        self._outbox.emit(
            "user.followed",
            aggregate_type="user",
            aggregate_id=str(followee_id),
            payload=UserFollowedPayload(follower_id=actor.id, followee_id=followee_id),
        )

    def unfollow_user(self, actor: Actor | None, followee_id: UserID) -> None:
        if actor is None:
            raise internal_error()
        self._repository.delete_follow(UserID(actor.id), followee_id)

    def is_following(self, follower_id: UserID, followee_id: UserID) -> bool:
        return self._repository.is_following(follower_id, followee_id)
