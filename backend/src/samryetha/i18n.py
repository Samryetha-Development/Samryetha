"""i18n service layer — catalog + translation submissions.

Tables created by ensure_schema_drift / create_all:
  i18n_catalog       — source strings (key, source_lang, value, context?)
  i18n_submissions   — user translation submissions (key, lang, value, status)
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


# ---------------------------------------------------------------- catalog


def list_catalog(conn: Connection, *, lang: str | None = None) -> list[dict]:
    """Return all catalog entries. If lang given, attach approved translation."""
    rows = conn.execute(text("SELECT key, source_lang, value, context FROM i18n_catalog ORDER BY key")).mappings().all()
    entries = [dict(r) for r in rows]
    if lang:
        # attach approved translation for each key
        approved = {
            r["key"]: r["value"]
            for r in conn.execute(
                text(
                    "SELECT key, value FROM i18n_submissions "
                    "WHERE lang = :lang AND status = 'approved' "
                    "ORDER BY reviewed_at DESC"
                ),
                {"lang": lang},
            ).mappings()
        }
        for e in entries:
            e["translation"] = approved.get(e["key"])
    return entries


def get_catalog_entry(conn: Connection, key: str, *, lang: str | None = None) -> dict | None:
    row = conn.execute(
        text("SELECT key, source_lang, value, context FROM i18n_catalog WHERE key = :key"),
        {"key": key},
    ).mappings().first()
    if row is None:
        return None
    entry = dict(row)
    if lang:
        tr = conn.execute(
            text(
                "SELECT value FROM i18n_submissions "
                "WHERE key = :key AND lang = :lang AND status = 'approved' "
                "ORDER BY reviewed_at DESC LIMIT 1"
            ),
            {"key": key, "lang": lang},
        ).scalar()
        entry["translation"] = tr
    return entry


# ---------------------------------------------------------------- submissions


def list_submissions(
    conn: Connection,
    *,
    user_id: int | None,
    lang: str | None,
    key: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    clauses = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if user_id is not None:
        clauses.append("s.user_id = :user_id")
        params["user_id"] = user_id
    if lang:
        clauses.append("s.lang = :lang")
        params["lang"] = lang
    if key:
        clauses.append("s.key = :key")
        params["key"] = key
    if status:
        clauses.append("s.status = :status")
        params["status"] = status
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT s.id, s.key, s.lang, s.value, s.note, s.status,
               s.reject_reason, s.user_id, s.reviewer_id,
               s.submitted_at, s.reviewed_at,
               u.username, u.display_name
        FROM i18n_submissions s
        LEFT JOIN users u ON u.id = s.user_id
        {where}
        ORDER BY s.submitted_at DESC
        LIMIT :limit OFFSET :offset
    """
    rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_submission(conn: Connection, submission_id: int) -> dict | None:
    row = conn.execute(
        text(
            "SELECT s.id, s.key, s.lang, s.value, s.note, s.status, "
            "s.reject_reason, s.user_id, s.reviewer_id, "
            "s.submitted_at, s.reviewed_at, "
            "u.username, u.display_name "
            "FROM i18n_submissions s "
            "LEFT JOIN users u ON u.id = s.user_id "
            "WHERE s.id = :id"
        ),
        {"id": submission_id},
    ).mappings().first()
    return dict(row) if row else None


def create_submission(
    conn: Connection,
    *,
    user_id: int,
    key: str,
    lang: str,
    value: str,
    note: str | None,
) -> dict:
    now = int(time.time() * 1000)
    result = conn.execute(
        text(
            "INSERT INTO i18n_submissions (key, lang, value, note, status, user_id, submitted_at) "
            "VALUES (:key, :lang, :value, :note, 'pending', :user_id, :now) "
            "RETURNING id"
        ),
        {"key": key, "lang": lang, "value": value, "note": note, "user_id": user_id, "now": now},
    )
    sub_id = result.scalar()
    conn.commit()
    return get_submission(conn, sub_id)


def update_submission_status(
    conn: Connection,
    submission_id: int,
    *,
    status: str,
    reviewer_id: int,
    reject_reason: str | None = None,
) -> dict:
    now = int(time.time() * 1000)
    conn.execute(
        text(
            "UPDATE i18n_submissions "
            "SET status = :status, reviewer_id = :reviewer_id, "
            "    reject_reason = :reject_reason, reviewed_at = :now "
            "WHERE id = :id"
        ),
        {
            "status": status,
            "reviewer_id": reviewer_id,
            "reject_reason": reject_reason,
            "now": now,
            "id": submission_id,
        },
    )
    conn.commit()
    return get_submission(conn, submission_id)
