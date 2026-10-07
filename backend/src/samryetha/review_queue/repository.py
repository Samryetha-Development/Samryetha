"""Typed SQLAlchemy persistence boundary for the moderation review queue."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.engine import Connection, RowMapping

from ..core.ids import ModerationQueueID, UserID
from .models import ContentType, ModerationDecision, Resolution, ReviewState
from ..core.schema import moderation_actions, moderation_queue, replies, users


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _opt_int(value: object, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _opt_str(value: object, field: str) -> str | None:
    return None if value is None else _str(value, field)


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    id: UserID
    username: str
    display_name: str


@dataclass(frozen=True, slots=True)
class QueueRecord:
    id: ModerationQueueID
    content_type: ContentType
    content_id: int
    author_id: UserID
    excerpt: str
    decision: ModerationDecision
    score: int
    signals: str
    review_state: ReviewState
    reviewer_id: UserID | None
    review_note: str | None
    reviewed_at: int | None
    created_at: int | None
    hold_until: int | None
    resolution: Resolution | None
    resolved_at: int | None
    recheck: str
    submitted_text: str
    superseded_at: int | None
    overturned: bool


def _record(row: RowMapping) -> QueueRecord:
    reviewer = _opt_int(row["reviewer_id"], "reviewer_id")
    resolution = _opt_str(row["resolution"], "resolution")
    return QueueRecord(
        id=ModerationQueueID(_int(row["id"], "queue id")),
        content_type=ContentType(_str(row["content_type"], "content_type")),
        content_id=_int(row["content_id"], "content_id"),
        author_id=UserID(_int(row["author_id"], "author_id")),
        excerpt=_str(row["excerpt"], "excerpt"),
        decision=ModerationDecision(_str(row["decision"], "decision")),
        score=_int(row["score"], "score"),
        signals=_str(row["signals"], "signals"),
        review_state=ReviewState(_str(row["review_state"], "review_state")),
        reviewer_id=UserID(reviewer) if reviewer is not None else None,
        review_note=_opt_str(row["review_note"], "review_note"),
        reviewed_at=_opt_int(row["reviewed_at"], "reviewed_at"),
        created_at=_opt_int(row["created_at"], "created_at"),
        hold_until=_opt_int(row["hold_until"], "hold_until"),
        resolution=Resolution(resolution) if resolution is not None else None,
        resolved_at=_opt_int(row["resolved_at"], "resolved_at"),
        recheck=_str(row["recheck"], "recheck"),
        submitted_text=_str(row["submitted_text"], "submitted_text"),
        superseded_at=_opt_int(row["superseded_at"], "superseded_at"),
        overturned=_int(row["overturned"], "overturned") == 1,
    )


def authors(conn: Connection, ids: Iterable[UserID]) -> dict[UserID, AuthorRecord]:
    values = tuple(set(ids))
    if not values:
        return {}
    rows: Sequence[RowMapping] = conn.execute(
        select(users.c.id, users.c.username, users.c.display_name).where(users.c.id.in_(values))
    ).mappings().all()
    records = (
        AuthorRecord(UserID(_int(row["id"], "user id")), _str(row["username"], "username"), _str(row["display_name"], "display_name"))
        for row in rows
    )
    return {record.id: record for record in records}


def queue_page(
    conn: Connection,
    *,
    state: ReviewState | None,
    content_type: ContentType | None,
    resolution: Resolution | None,
    awaiting: bool,
    blocked: bool,
    cursor: tuple[int, ModerationQueueID] | None,
    limit: int,
) -> list[QueueRecord]:
    conditions: list[ColumnElement[bool]] = [moderation_queue.c.superseded_at.is_(None)]
    if state is not None:
        conditions.append(moderation_queue.c.review_state == state.value)
    if content_type is not None:
        conditions.append(moderation_queue.c.content_type == content_type.value)
    if awaiting:
        conditions.append(moderation_queue.c.resolution.is_(None))
    elif blocked:
        conditions.append(moderation_queue.c.resolution.in_((Resolution.Blocked.value, Resolution.BlockedByMachine.value)))
    elif resolution is not None:
        conditions.append(moderation_queue.c.resolution == resolution.value)
    if cursor is not None:
        score, item_id = cursor
        conditions.append(or_(moderation_queue.c.score < score, and_(moderation_queue.c.score == score, moderation_queue.c.id < item_id)))
    rows: Sequence[RowMapping] = conn.execute(
        select(moderation_queue).where(and_(*conditions)).order_by(moderation_queue.c.score.desc(), moderation_queue.c.id.desc()).limit(limit + 1)
    ).mappings().all()
    return [_record(row) for row in rows]


def score_for_id(conn: Connection, queue_id: ModerationQueueID) -> int | None:
    value = conn.execute(select(moderation_queue.c.score).where(moderation_queue.c.id == queue_id)).scalar_one_or_none()
    return None if value is None else _int(value, "score")


def get(conn: Connection, queue_id: ModerationQueueID) -> QueueRecord | None:
    row = conn.execute(select(moderation_queue).where(moderation_queue.c.id == queue_id)).mappings().first()
    return _record(row) if row is not None else None


def decide(
    conn: Connection,
    row: QueueRecord,
    *,
    reviewer_id: UserID,
    state: ReviewState,
    resolution: Resolution,
    note: str | None,
    reviewed_at: int,
    overturned: bool,
) -> QueueRecord | None:
    result = conn.execute(
        moderation_queue.update().where(
            moderation_queue.c.id == row.id,
            moderation_queue.c.superseded_at.is_(None),
            moderation_queue.c.review_state == ReviewState.Pending.value,
        ).values(
            review_state=state.value,
            reviewer_id=reviewer_id,
            review_note=note,
            reviewed_at=reviewed_at,
            resolution=resolution.value,
            resolved_at=reviewed_at,
            overturned=int(overturned),
        )
    )
    if result.rowcount != 1:
        return None
    return replace(row, review_state=state, reviewer_id=reviewer_id, review_note=note, reviewed_at=reviewed_at, resolution=resolution, resolved_at=reviewed_at, overturned=overturned)


def add_action(conn: Connection, *, actor_id: UserID, action: str, row: QueueRecord, reason: str, created_at: int) -> None:
    conn.execute(moderation_actions.insert().values(actor_user_id=actor_id, action=action, target_type=row.content_type.value, target_id=row.content_id, reason=reason, created_at=created_at))


def content_href(conn: Connection, row: QueueRecord) -> str | None:
    if row.content_type is ContentType.Discussion:
        return f"/d/{row.content_id}"
    if row.content_type is ContentType.Reply:
        parent = conn.execute(select(replies.c.discussion_id).where(replies.c.id == row.content_id)).scalar_one_or_none()
        return None if parent is None else f"/d/{_int(parent, 'discussion_id')}#reply-{row.content_id}"
    return None


def reply_discussion_id(conn: Connection, reply_id: int) -> int | None:
    value = conn.execute(select(replies.c.discussion_id).where(replies.c.id == reply_id)).scalar_one_or_none()
    return None if value is None else _int(value, "discussion_id")


def retained_page(conn: Connection, *, content_type: ContentType | None, cursor: ModerationQueueID | None, limit: int) -> list[QueueRecord]:
    conditions: list[ColumnElement[bool]] = [moderation_queue.c.resolution.in_((Resolution.Blocked.value, Resolution.BlockedByMachine.value))]
    if content_type is not None:
        conditions.append(moderation_queue.c.content_type == content_type.value)
    if cursor is not None:
        conditions.append(moderation_queue.c.id < cursor)
    rows: Sequence[RowMapping] = conn.execute(select(moderation_queue).where(and_(*conditions)).order_by(moderation_queue.c.id.desc()).limit(limit + 1)).mappings().all()
    return [_record(row) for row in rows]


def retained_total(conn: Connection) -> int:
    value = conn.execute(select(func.count()).select_from(moderation_queue).where(moderation_queue.c.resolution.in_((Resolution.Blocked.value, Resolution.BlockedByMachine.value)))).scalar_one()
    return _int(value, "retained total")
