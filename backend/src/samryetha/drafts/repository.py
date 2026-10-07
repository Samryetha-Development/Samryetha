"""SQLAlchemy persistence boundary for discussion drafts."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..db import now_ms
from ..ids import AttachmentID, DraftID, UserID
from ..schema import attachments, discussion_drafts, draft_attachments
from .models import DraftAttachmentRecord, DraftBodyFormat, DraftRecord, SaveDraft


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _required_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _optional_str(value: object, field: str) -> str | None:
    return None if value is None else _required_str(value, field)


def _record(row: RowMapping) -> DraftRecord:
    return DraftRecord(
        id=DraftID(_required_int(row["id"], "id")),
        author_id=UserID(_required_int(row["author_id"], "author_id")),
        board_slug=_optional_str(row["board_slug"], "board_slug"),
        title=_required_str(row["title"], "title"),
        body_markdown=_required_str(row["body_md"], "body_md"),
        body_format=DraftBodyFormat(_required_str(row["body_format"], "body_format")),
        created_at=_required_int(row["created_at"], "created_at"),
        updated_at=_required_int(row["updated_at"], "updated_at"),
    )


def _attachment(row: RowMapping) -> DraftAttachmentRecord:
    return DraftAttachmentRecord(
        id=AttachmentID(_required_int(row["id"], "attachment id")),
        object_key=_required_str(row["object_key"], "object_key"),
        original_filename=_required_str(row["original_filename"], "original_filename"),
        size_bytes=_required_int(row["size_bytes"], "size_bytes"),
    )


def owned(conn: Connection, user_id: UserID, draft_id: DraftID) -> DraftRecord | None:
    row = conn.execute(
        select(discussion_drafts).where(
            discussion_drafts.c.id == draft_id, discussion_drafts.c.author_id == user_id
        )
    ).mappings().first()
    return None if row is None else _record(row)


def list_records(
    conn: Connection,
    user_id: UserID,
    before_id: DraftID | None,
    limit: int,
) -> tuple[list[tuple[DraftRecord, int]], bool]:
    attachment_count = (
        select(func.count())
        .select_from(draft_attachments)
        .where(draft_attachments.c.draft_id == discussion_drafts.c.id)
        .scalar_subquery()
        .label("attachment_count")
    )
    statement = select(discussion_drafts, attachment_count).where(discussion_drafts.c.author_id == user_id)
    if before_id is not None:
        statement = statement.where(discussion_drafts.c.id < before_id)
    rows: Sequence[RowMapping] = conn.execute(
        statement.order_by(discussion_drafts.c.id.desc()).limit(limit + 1)
    ).mappings().all()
    records = [(_record(row), _required_int(row["attachment_count"], "attachment_count")) for row in rows[:limit]]
    return records, len(rows) > limit


def attachment_records(conn: Connection, draft_id: DraftID) -> list[DraftAttachmentRecord]:
    rows: Sequence[RowMapping] = conn.execute(
        select(attachments)
        .join(draft_attachments, draft_attachments.c.attachment_id == attachments.c.id)
        .where(draft_attachments.c.draft_id == draft_id)
        .order_by(attachments.c.id)
    ).mappings().all()
    return [_attachment(row) for row in rows]


def available_attachment_owners(
    conn: Connection,
    user_id: UserID,
    attachment_ids: Sequence[AttachmentID],
) -> dict[AttachmentID, DraftID | None]:
    if not attachment_ids:
        return {}
    rows: Sequence[RowMapping] = conn.execute(
        select(attachments.c.id, draft_attachments.c.draft_id)
        .outerjoin(draft_attachments, draft_attachments.c.attachment_id == attachments.c.id)
        .where(
            attachments.c.id.in_(attachment_ids),
            attachments.c.uploader_id == user_id,
            attachments.c.discussion_id.is_(None),
            attachments.c.state == "uploaded",
        )
    ).mappings().all()
    result: dict[AttachmentID, DraftID | None] = {}
    for row in rows:
        attachment_id = AttachmentID(_required_int(row["id"], "attachment id"))
        raw_draft_id = row["draft_id"]
        result[attachment_id] = None if raw_draft_id is None else DraftID(_required_int(raw_draft_id, "draft id"))
    return result


def save(
    conn: Connection,
    user_id: UserID,
    command: SaveDraft,
    draft_id: DraftID | None,
) -> DraftID:
    stamp = now_ms()
    values: dict[str, object] = {
        "board_slug": command.board_slug,
        "title": command.title,
        "body_md": command.body_markdown,
        "body_format": command.body_format.value,
        "updated_at": stamp,
    }
    if draft_id is None:
        result = conn.execute(
            discussion_drafts.insert().values(author_id=user_id, created_at=stamp, **values)
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("draft insert did not return a primary key")
        draft_id = DraftID(_required_int(primary_key[0], "inserted draft id"))
    else:
        conn.execute(update(discussion_drafts).where(discussion_drafts.c.id == draft_id).values(**values))
        conn.execute(draft_attachments.delete().where(draft_attachments.c.draft_id == draft_id))
    if command.attachment_ids:
        conn.execute(
            draft_attachments.insert(),
            [
                {"draft_id": draft_id, "attachment_id": attachment_id}
                for attachment_id in sorted(command.attachment_ids, key=lambda value: value)
            ],
        )
    return draft_id


def delete(conn: Connection, draft_id: DraftID) -> None:
    conn.execute(discussion_drafts.delete().where(discussion_drafts.c.id == draft_id))


def referenced_draft_ids(conn: Connection, attachment_ids: Sequence[AttachmentID]) -> list[DraftID]:
    if not attachment_ids:
        return []
    values = conn.execute(
        select(draft_attachments.c.draft_id).where(
            draft_attachments.c.attachment_id.in_(attachment_ids)
        )
    ).scalars().all()
    return [DraftID(_required_int(value, "referenced draft id")) for value in values]
