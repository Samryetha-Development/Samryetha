"""Typed SQLAlchemy persistence boundary for boards and memberships."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.ids import BoardID, UserID
from ..core.records import opt_int, require_int, require_str
from ..core.schema import board_members, boards, discussions, users
from .models import BoardMemberRole, BoardVisibility, PostingPolicy


@dataclass(frozen=True, slots=True)
class BoardRecord:
    id: BoardID
    slug: str
    name: str
    description: str
    visibility: BoardVisibility
    posting_policy: PostingPolicy
    created_by_user_id: UserID | None
    deleted_at: int | None


@dataclass(frozen=True, slots=True)
class BoardMetricsRecord:
    member_count: int
    today_activity: int
    current_user_role: BoardMemberRole | None


@dataclass(frozen=True, slots=True)
class BoardMemberRecord:
    id: UserID
    username: str
    discriminator: int | None
    display_name: str
    role: BoardMemberRole


def _record(row: RowMapping) -> BoardRecord:
    creator = opt_int(row["created_by_user_id"], "created_by_user_id")
    return BoardRecord(
        id=BoardID(require_int(row["id"], "board id")),
        slug=require_str(row["slug"], "slug"),
        name=require_str(row["name"], "name"),
        description=require_str(row["description"], "description"),
        visibility=BoardVisibility(require_str(row["visibility"], "visibility")),
        posting_policy=PostingPolicy(require_str(row["posting_policy"], "posting_policy")),
        created_by_user_id=UserID(creator) if creator is not None else None,
        deleted_at=opt_int(row["deleted_at"], "deleted_at"),
    )


class BoardRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get_by_slug(self, slug: str) -> BoardRecord | None:
        row = (
            self._conn.execute(select(boards).where(boards.c.slug == slug, boards.c.deleted_at.is_(None)))
            .mappings()
            .first()
        )
        return _record(row) if row is not None else None

    def list_active(self) -> list[BoardRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(select(boards).where(boards.c.deleted_at.is_(None)).order_by(boards.c.name))
            .mappings()
            .all()
        )
        return [_record(row) for row in rows]

    def metrics(self, board_id: BoardID, *, viewer_id: UserID | None, activity_since: int) -> BoardMetricsRecord:
        row = (
            self._conn.execute(
                select(
                    select(func.count())
                    .select_from(board_members)
                    .where(board_members.c.board_id == board_id)
                    .scalar_subquery()
                    .label("member_count"),
                    select(func.count())
                    .select_from(discussions)
                    .where(
                        discussions.c.board_id == board_id,
                        discussions.c.created_at >= activity_since,
                        discussions.c.deleted_at.is_(None),
                    )
                    .scalar_subquery()
                    .label("today_activity"),
                )
            )
            .mappings()
            .one()
        )
        role_value = None
        if viewer_id is not None:
            role_value = self._conn.execute(
                select(board_members.c.role).where(
                    board_members.c.board_id == board_id, board_members.c.user_id == viewer_id
                )
            ).scalar_one_or_none()
        return BoardMetricsRecord(
            member_count=require_int(row["member_count"], "member count"),
            today_activity=require_int(row["today_activity"], "today activity"),
            current_user_role=BoardMemberRole(require_str(role_value, "member role"))
            if role_value is not None
            else None,
        )

    def member_board_ids(self, user_id: UserID) -> set[BoardID]:
        values = (
            self._conn.execute(select(board_members.c.board_id).where(board_members.c.user_id == user_id))
            .scalars()
            .all()
        )
        return {BoardID(require_int(value, "board id")) for value in values}

    def insert_board(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        visibility: BoardVisibility,
        posting_policy: PostingPolicy,
        created_by_user_id: UserID,
        created_at: int,
    ) -> BoardID:
        result = self._conn.execute(
            boards.insert().values(
                slug=slug,
                name=name,
                description=description,
                visibility=visibility.value,
                posting_policy=posting_policy.value,
                created_by_user_id=created_by_user_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("board insert did not return a primary key")
        return BoardID(require_int(primary_key[0], "inserted board id"))

    def insert_member(self, board_id: BoardID, user_id: UserID, role: BoardMemberRole, *, joined_at: int) -> None:
        self._conn.execute(
            board_members.insert().values(board_id=board_id, user_id=user_id, role=role.value, joined_at=joined_at)
        )

    def update_board(self, board_id: BoardID, values: dict[str, object]) -> None:
        self._conn.execute(update(boards).where(boards.c.id == board_id).values(**values))

    def soft_delete_board(self, board_id: BoardID, *, actor_id: UserID, reason: str | None, deleted_at: int) -> None:
        self._conn.execute(
            update(boards)
            .where(boards.c.id == board_id)
            .values(deleted_at=deleted_at, deleted_by=actor_id, deletion_reason=reason, updated_at=deleted_at)
        )

    def is_member(self, board_id: BoardID, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(board_members.c.board_id).where(
                    board_members.c.board_id == board_id, board_members.c.user_id == user_id
                )
            ).first()
            is not None
        )

    def delete_member(self, board_id: BoardID, user_id: UserID) -> None:
        self._conn.execute(
            board_members.delete().where(board_members.c.board_id == board_id, board_members.c.user_id == user_id)
        )

    def members(self, board_id: BoardID) -> list[BoardMemberRecord]:
        rows = (
            self._conn.execute(
                select(users.c.id, users.c.username, users.c.display_name, users.c.discriminator, board_members.c.role)
                .select_from(board_members)
                .join(users, board_members.c.user_id == users.c.id)
                .where(board_members.c.board_id == board_id)
            )
            .mappings()
            .all()
        )
        return [
            BoardMemberRecord(
                id=UserID(require_int(row["id"], "user id")),
                username=require_str(row["username"], "username"),
                discriminator=opt_int(row["discriminator"], "discriminator"),
                display_name=require_str(row["display_name"], "display_name"),
                role=BoardMemberRole(require_str(row["role"], "role")),
            )
            for row in rows
        ]

    def update_member_role(self, board_id: BoardID, user_id: UserID, role: BoardMemberRole) -> None:
        self._conn.execute(
            update(board_members)
            .where(board_members.c.board_id == board_id, board_members.c.user_id == user_id)
            .values(role=role.value)
        )
