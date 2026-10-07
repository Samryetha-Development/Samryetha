"""Shared SQL visibility predicates for moderated discussion content."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement


class ModerationViewer(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def role(self) -> str: ...


def moderation_visible(
    status_column: ColumnElement[str],
    author_column: ColumnElement[int],
    viewer: ModerationViewer | None,
) -> ColumnElement[bool] | None:
    """Return the row-level visibility predicate for moderated content."""
    if viewer is not None and viewer.role == "admin":
        return None
    if viewer is not None and viewer.role == "moderator":
        return status_column != "rejected"
    if viewer is not None:
        return or_(status_column == "approved", and_(status_column == "pending", author_column == viewer.id))
    return status_column == "approved"
