"""Account-specific hiding persists after any report review decision."""

from sqlalchemy import select, true
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

from ..core.schema import reports


def not_reported(user_id: int | None, target_type: str, target_id: object) -> ColumnElement[bool]:
    if user_id is None:
        return true()
    return ~select(reports.c.id).where(
        reports.c.reporter_user_id == user_id,
        reports.c.reportable_type == target_type,
        reports.c.reportable_id == target_id,
    ).exists()


def is_reported(conn: Connection, user_id: int | None, target_type: str, target_id: int) -> bool:
    return not bool(conn.execute(select(not_reported(user_id, target_type, target_id))).scalar_one())
