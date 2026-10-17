"""Persistence and serialization boundary for scheduled posts."""

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from ..core.schema import scheduled_posts, users
from .models import ScheduledAuthor, ScheduledPostRecord


class ScheduledPostRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def owned(self, author_id: int, post_id: int, *, lock: bool = False) -> ScheduledPostRecord | None:
        condition = (scheduled_posts.c.id == post_id) & (scheduled_posts.c.author_id == author_id)
        if lock:
            self._conn.execute(update(scheduled_posts).where(condition).values(id=scheduled_posts.c.id))
        row = self._conn.execute(select(scheduled_posts).where(condition)).mappings().first()
        return ScheduledPostRecord.model_validate(row) if row is not None else None

    def list_owned(self, author_id: int, before_id: int | None, limit: int) -> list[ScheduledPostRecord]:
        stmt = select(scheduled_posts).where(
            scheduled_posts.c.author_id == author_id, scheduled_posts.c.draft_id.is_not(None)
        )
        if before_id is not None:
            stmt = stmt.where(scheduled_posts.c.id < before_id)
        rows = self._conn.execute(stmt.order_by(scheduled_posts.c.id.desc()).limit(limit + 1)).mappings()
        return [ScheduledPostRecord.model_validate(row) for row in rows]

    def insert(self, record: ScheduledPostRecord) -> int:
        values = record.model_dump(exclude={"id"})
        result = self._conn.execute(scheduled_posts.insert().values(**values))
        if result.inserted_primary_key is None or result.inserted_primary_key[0] is None:
            raise RuntimeError("Created schedule could not be identified")
        return int(result.inserted_primary_key[0])

    def change(self, post_id: int, **values: object) -> None:
        self._conn.execute(update(scheduled_posts).where(scheduled_posts.c.id == post_id).values(**values))

    def due_ids(self, stamp: int, limit: int) -> list[int]:
        return list(self._conn.execute(
            select(scheduled_posts.c.id).where(
                scheduled_posts.c.status == "pending", scheduled_posts.c.scheduled_at <= stamp
            ).order_by(scheduled_posts.c.scheduled_at, scheduled_posts.c.id).limit(limit)
        ).scalars())

    def claim(self, post_id: int, stamp: int) -> ScheduledPostRecord | None:
        row = self._conn.execute(
            update(scheduled_posts).where(
                scheduled_posts.c.id == post_id,
                scheduled_posts.c.status == "pending",
                scheduled_posts.c.scheduled_at <= stamp,
            ).values(status="publishing", updated_at=stamp).returning(*scheduled_posts.c)
        ).mappings().first()
        return ScheduledPostRecord.model_validate(row) if row is not None else None

    def author(self, author_id: int) -> ScheduledAuthor | None:
        row = self._conn.execute(select(users.c.id, users.c.role, users.c.status).where(users.c.id == author_id)).mappings().first()
        return ScheduledAuthor.model_validate(row) if row is not None else None
