"""SQLAlchemy persistence checks used by authorization rules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..core.ids import BoardID, FeedbackProjectID, UserID
from ..core.schema import board_members, feedback_project_members


class AuthorizationRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def is_board_member(self, board_id: BoardID, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(board_members.c.board_id).where(
                    (board_members.c.board_id == board_id) & (board_members.c.user_id == user_id)
                )
            ).first()
            is not None
        )

    def is_board_moderator(self, board_id: BoardID, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(board_members.c.board_id).where(
                    (board_members.c.board_id == board_id)
                    & (board_members.c.user_id == user_id)
                    & (board_members.c.role == "moderator")
                )
            ).first()
            is not None
        )

    def is_project_member(self, project_id: FeedbackProjectID, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(feedback_project_members.c.project_id).where(
                    (feedback_project_members.c.project_id == project_id)
                    & (feedback_project_members.c.user_id == user_id)
                )
            ).first()
            is not None
        )

    def is_project_programmer(self, project_id: FeedbackProjectID, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(feedback_project_members.c.project_id).where(
                    (feedback_project_members.c.project_id == project_id)
                    & (feedback_project_members.c.user_id == user_id)
                    & (feedback_project_members.c.is_programmer == 1)
                )
            ).first()
            is not None
        )
