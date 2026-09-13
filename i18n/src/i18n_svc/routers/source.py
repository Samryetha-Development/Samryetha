"""GET /api/source — 公开接口，返回所有 locale + key 的全部翻译（多语言对照视图）。

适合翻译工具读取完整语料对照。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from ..deps import DbConn
from ..schema import catalog_entries

router = APIRouter(prefix="/api/source", tags=["source"])


class SourceEntry(BaseModel):
    locale: str
    key: str
    value: str
    description: str | None


class SourceResponse(BaseModel):
    entries: list[SourceEntry]
    total: int


@router.get("", response_model=SourceResponse)
def get_source(
    conn: DbConn,
    locale: str | None = Query(default=None, description="按 locale 过滤（可选）"),
    key_prefix: str | None = Query(default=None, description="按 key 前缀过滤（可选）"),
):
    """返回翻译条目原始数据，供外部工具对照所有语言。"""
    stmt = select(
        catalog_entries.c.locale,
        catalog_entries.c.key,
        catalog_entries.c.value,
        catalog_entries.c.description,
    )
    if locale:
        stmt = stmt.where(catalog_entries.c.locale == locale)
    if key_prefix:
        stmt = stmt.where(catalog_entries.c.key.like(f"{key_prefix}%"))
    stmt = stmt.order_by(catalog_entries.c.locale, catalog_entries.c.key)

    rows = conn.execute(stmt).mappings().all()
    entries = [SourceEntry(**dict(r)) for r in rows]
    return SourceResponse(entries=entries, total=len(entries))
