"""Account-private discussion drafts; saved attachments survive orphan cleanup."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from .attachments import to_attachment
from .authz import Abilities, assert_can
from .boards import get_board_for_authz
from .db import now_ms
from .errors import not_found, validation_failed
from .schema import attachments, discussion_drafts, draft_attachments


def require_owned(conn: Connection, actor, draft_id: int) -> dict:
    row = conn.execute(select(discussion_drafts).where(
        (discussion_drafts.c.id == draft_id) & (discussion_drafts.c.author_id == actor.id)
    )).first()
    if row is None:
        raise not_found("Draft not found")
    return dict(row._mapping)


def _dto(row: dict) -> dict:
    return {
        "id": row["id"], "boardSlug": row["board_slug"], "title": row["title"],
        "bodyMarkdown": row["body_md"], "bodyFormat": row["body_format"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def list_drafts(conn: Connection, actor, before_id: int | None = None, limit: int = 20) -> dict:
    # ID cursors keep pagination stable when another draft is saved while browsing.
    stmt = select(discussion_drafts).where(discussion_drafts.c.author_id == actor.id)
    if before_id is not None:
        stmt = stmt.where(discussion_drafts.c.id < before_id)
    rows = conn.execute(stmt.order_by(discussion_drafts.c.id.desc()).limit(limit + 1)).all()
    items = []
    for row in rows[:limit]:
        item = _dto(dict(row._mapping))
        item["preview"] = " ".join(item.pop("bodyMarkdown").split())[:160]
        item["attachmentCount"] = conn.execute(select(func.count()).select_from(draft_attachments).where(
            draft_attachments.c.draft_id == row.id
        )).scalar_one()
        items.append(item)
    return {"items": items, "nextCursor": str(rows[limit - 1].id) if len(rows) > limit else None}


def get_draft(conn: Connection, actor, draft_id: int, storage) -> dict:
    item = _dto(require_owned(conn, actor, draft_id))
    rows = conn.execute(select(attachments).join(
        draft_attachments, draft_attachments.c.attachment_id == attachments.c.id
    ).where(draft_attachments.c.draft_id == draft_id).order_by(attachments.c.id)).all()
    item["attachments"] = [to_attachment(dict(row._mapping), storage) for row in rows]
    return item


def save_draft(conn: Connection, actor, data: dict, storage, draft_id: int | None = None) -> dict:
    if draft_id is not None:
        require_owned(conn, actor, draft_id)
    slug = data.get("boardSlug")
    if slug:
        board = get_board_for_authz(conn, slug)
        if board is None:
            raise not_found("Board not found")
        assert_can(actor, Abilities.DISCUSSION_READ, {"type": "board", **board}, conn)
    ids = set(data.get("attachmentIds") or [])
    if ids:
        rows = conn.execute(select(attachments.c.id, draft_attachments.c.draft_id).outerjoin(
            draft_attachments, draft_attachments.c.attachment_id == attachments.c.id
        ).where(
            attachments.c.id.in_(ids) & (attachments.c.uploader_id == actor.id)
            & attachments.c.discussion_id.is_(None) & (attachments.c.state == "uploaded")
        )).all()
        if len(rows) != len(ids) or any(r.draft_id is not None and r.draft_id != draft_id for r in rows):
            raise validation_failed([{"field": "attachmentIds", "message": "One or more attachments are unavailable", "code": "custom"}])
    stamp = now_ms()
    values = {
        "board_slug": slug, "title": data["title"], "body_md": data["bodyMarkdown"],
        "body_format": data["bodyFormat"], "updated_at": stamp,
    }
    if draft_id is None:
        result = conn.execute(discussion_drafts.insert().values(author_id=actor.id, created_at=stamp, **values))
        draft_id = result.inserted_primary_key[0]
    else:
        conn.execute(update(discussion_drafts).where(discussion_drafts.c.id == draft_id).values(**values))
        conn.execute(draft_attachments.delete().where(draft_attachments.c.draft_id == draft_id))
    if ids:
        conn.execute(draft_attachments.insert(), [{"draft_id": draft_id, "attachment_id": aid} for aid in sorted(ids)])
    return get_draft(conn, actor, draft_id, storage)


def delete_draft(conn: Connection, actor, draft_id: int) -> None:
    require_owned(conn, actor, draft_id)
    # Uploaded files become eligible for normal orphan cleanup after references go away.
    conn.execute(discussion_drafts.delete().where(discussion_drafts.c.id == draft_id))


def validate_publish_attachments(conn: Connection, draft_id: int | None, attachment_ids: list[int]) -> None:
    refs = conn.execute(select(draft_attachments.c.draft_id).where(
        draft_attachments.c.attachment_id.in_(attachment_ids)
    )).all()
    if any(row.draft_id != draft_id for row in refs):
        raise validation_failed([{"field": "attachmentIds", "message": "Attachment belongs to another draft", "code": "custom"}])
