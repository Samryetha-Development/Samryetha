"""Typed board service."""

from __future__ import annotations

from samryetha.boards.repository import BoardRepository

import re
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.engine import Connection

from .models import (
    BoardAuthz,
    BoardBody,
    BoardMemberResponse,
    BoardMemberRole,
    BoardPatch,
    BoardSummaryResponse,
    BoardVisibility,
    PostingPolicy,
)
from ..core.db import now_ms
from ..core.errors import conflict, internal_error, not_found
from ..authz import Abilities, Actor, AuthorizationService
from ..core.ids import UserID
from ..users import make_handle
from .repository import BoardRecord

if TYPE_CHECKING:
    from ..core.deps import CurrentUser


def _start_of_today_ms() -> int:
    local_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp() * 1000)


def _slugify(slug: str) -> str:
    return re.sub(r"\s+", "-", slug.strip().lower())


class BoardService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = BoardRepository(self._conn)

    def get_by_slug(self, slug: str) -> BoardRecord | None:
        return self._repository.get_by_slug(slug)

    def get_board_for_authz(self, slug: str) -> BoardAuthz | None:
        board = self.get_by_slug(slug)
        if board is None:
            return None
        return {
            "id": board.id,
            "visibility": board.visibility.value,
            "postingPolicy": board.posting_policy.value,
            "slug": board.slug,
        }

    def _summary(self, board: BoardRecord, viewer_id: UserID | None) -> BoardSummaryResponse:
        metrics = self._repository.metrics(board.id, viewer_id=viewer_id, activity_since=_start_of_today_ms())
        return BoardSummaryResponse(
            id=board.id,
            slug=board.slug,
            name=board.name,
            description=board.description,
            visibility=board.visibility,
            posting_policy=board.posting_policy,
            member_count=metrics.member_count,
            today_activity=metrics.today_activity,
            current_user_role=metrics.current_user_role,
        )

    def _is_visible(self, viewer: CurrentUser | None, board: BoardRecord) -> bool:
        if viewer is not None and viewer.role == "admin":
            return True
        if board.visibility is BoardVisibility.Public:
            return True
        if viewer is None:
            return False
        return self._repository.is_member(board.id, UserID(viewer.id))

    def list_boards(self, viewer: CurrentUser | None) -> list[BoardSummaryResponse]:
        records = self._repository.list_active()
        viewer_id = UserID(viewer.id) if viewer is not None else None
        return [self._summary(board, viewer_id) for board in records if self._is_visible(viewer, board)]

    def get_board(self, viewer: CurrentUser | None, slug: str) -> BoardSummaryResponse:
        board = self.get_by_slug(slug)
        if board is None or not self._is_visible(viewer, board):
            raise not_found("Board not found")
        return self._summary(board, UserID(viewer.id) if viewer is not None else None)

    def _authorize_board(self, actor: Actor, ability: str, slug: str) -> None:
        board = self.get_board_for_authz(slug)
        if board is None:
            # Preserve the existing HTTP boundary's missing-resource error.
            raise internal_error()
        AuthorizationService(self._conn).assert_can(actor, ability, {"type": "board", **board})

    def create_board(self, actor: Actor, command: BoardBody) -> BoardSummaryResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.BOARD_CREATE, None)
        actor_id = UserID(actor.id)
        slug = _slugify(command.slug)
        if self.get_by_slug(slug) is not None:
            raise conflict("Board slug already exists")
        stamp = now_ms()
        board_id = self._repository.insert_board(
            slug=slug,
            name=command.name,
            description=command.description or "",
            visibility=command.visibility or BoardVisibility.Public,
            posting_policy=command.posting_policy or PostingPolicy.Members,
            created_by_user_id=actor_id,
            created_at=stamp,
        )
        self._repository.insert_member(board_id, actor_id, BoardMemberRole.Moderator, joined_at=stamp)
        created = self.get_by_slug(slug)
        if created is None:
            raise RuntimeError("created board could not be loaded")
        return self._summary(created, None)

    def update_board(self, actor: Actor, slug: str, command: BoardPatch) -> BoardSummaryResponse:
        self._authorize_board(actor, Abilities.BOARD_UPDATE, slug)
        board = self.get_by_slug(slug)
        if board is None:
            raise not_found("Board not found")
        values: dict[str, object] = {"updated_at": now_ms()}
        fields = command.model_fields_set
        if "name" in fields and command.name is not None:
            values["name"] = command.name
        if "description" in fields and command.description is not None:
            values["description"] = command.description
        if "visibility" in fields and command.visibility is not None:
            values["visibility"] = command.visibility.value
        if "posting_policy" in fields and command.posting_policy is not None:
            values["posting_policy"] = command.posting_policy.value
        self._repository.update_board(board.id, values)
        updated = self.get_by_slug(slug)
        if updated is None:
            raise RuntimeError("updated board could not be loaded")
        return self._summary(updated, None)

    def delete_board(self, actor: Actor, slug: str, reason: str | None) -> None:
        self._authorize_board(actor, Abilities.BOARD_DELETE, slug)
        actor_id = UserID(actor.id)
        board = self.get_by_slug(slug)
        if board is None:
            raise not_found("Board not found")
        stamp = now_ms()
        self._repository.soft_delete_board(board.id, actor_id=actor_id, reason=reason, deleted_at=stamp)

    def join_board(self, actor: Actor, slug: str) -> None:
        self._authorize_board(actor, Abilities.BOARD_JOIN, slug)
        user_id = UserID(actor.id)
        board = self.get_by_slug(slug)
        if board is None:
            raise not_found("Board not found")
        if self._repository.is_member(board.id, user_id):
            raise conflict("Already a member")
        self._repository.insert_member(board.id, user_id, BoardMemberRole.Member, joined_at=now_ms())

    def leave_board(self, user_id: UserID, slug: str) -> None:
        board = self.get_by_slug(slug)
        if board is None:
            raise not_found("Board not found")
        self._repository.delete_member(board.id, user_id)

    def list_members(self, viewer: CurrentUser | None, slug: str) -> list[BoardMemberResponse]:
        board = self.get_by_slug(slug)
        if board is None or not self._is_visible(viewer, board):
            raise not_found("Board not found")
        rows = self._repository.members(board.id)
        return [
            BoardMemberResponse(
                id=row.id,
                username=row.username,
                handle=make_handle(row.username, row.discriminator),
                display_name=row.display_name,
                role=row.role,
            )
            for row in rows
        ]

    def update_member_role(self, actor: Actor, slug: str, user_id: UserID, role: BoardMemberRole) -> None:
        self._authorize_board(actor, Abilities.BOARD_MANAGE_MEMBERS, slug)
        board = self.get_by_slug(slug)
        if board is None:
            raise not_found("Board not found")
        if not self._repository.is_member(board.id, user_id):
            raise not_found("User is not a member of this board")
        self._repository.update_member_role(board.id, user_id, role)
