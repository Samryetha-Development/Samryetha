"""GET /api/catalog/{locale} — 公开接口，返回该 locale 的全部翻译条目。

也包含管理员写入端点（PUT /api/catalog/{locale}/{key}，DELETE 同路径）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, insert, select, update

from ..db import now_ms
from ..deps import AdminUser, DbConn
from ..errors import bad_request, conflict, not_found
from ..schema import catalog_entries
from ..validation import validate_key, validate_locale

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


# ---------------------------------------------------------------- response models

class CatalogEntry(BaseModel):
    id: int
    locale: str
    key: str
    value: str
    description: str | None
    created_at: int
    updated_at: int


class CatalogResponse(BaseModel):
    locale: str
    entries: list[CatalogEntry]
    total: int


# ---------------------------------------------------------------- helpers

def _validate_locale_param(locale: str, request: Request) -> str:
    supported: list[str] = request.app.state.settings.supported_locale_list
    try:
        return validate_locale(locale, supported)
    except ValueError as exc:
        raise bad_request(str(exc))


def _validate_key_param(key: str) -> str:
    try:
        return validate_key(key)
    except ValueError as exc:
        raise bad_request(str(exc))


# ---------------------------------------------------------------- GET /api/catalog/{locale}  （公开）

@router.get("/{locale}", response_model=CatalogResponse)
def get_catalog(
    locale: Annotated[str, Path(description="BCP-47 locale, e.g. zh-CN")],
    conn: DbConn,
    request: Request,
    key_prefix: str | None = Query(default=None, description="按 key 前缀过滤"),
):
    _validate_locale_param(locale, request)

    stmt = select(catalog_entries).where(catalog_entries.c.locale == locale)
    if key_prefix:
        stmt = stmt.where(catalog_entries.c.key.like(f"{key_prefix}%"))
    rows = conn.execute(stmt.order_by(catalog_entries.c.key)).mappings().all()
    entries = [CatalogEntry(**dict(r)) for r in rows]
    return CatalogResponse(locale=locale, entries=entries, total=len(entries))


# ---------------------------------------------------------------- PUT /api/catalog/{locale}/{key}  （管理员）

class UpsertEntryBody(BaseModel):
    value: str
    description: str | None = None

    @field_validator("value")
    @classmethod
    def value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("value must not be empty")
        return v


@router.put("/{locale}/{key:path}", response_model=CatalogEntry, status_code=200)
def upsert_entry(
    locale: Annotated[str, Path()],
    key: Annotated[str, Path()],
    body: UpsertEntryBody,
    conn: DbConn,
    request: Request,
    _admin: AdminUser,
):
    _validate_locale_param(locale, request)
    _validate_key_param(key)

    now = now_ms()
    existing = conn.execute(
        select(catalog_entries).where(
            catalog_entries.c.locale == locale,
            catalog_entries.c.key == key,
        )
    ).first()

    if existing:
        conn.execute(
            update(catalog_entries)
            .where(catalog_entries.c.locale == locale, catalog_entries.c.key == key)
            .values(value=body.value, description=body.description, updated_at=now)
        )
        row = conn.execute(
            select(catalog_entries).where(
                catalog_entries.c.locale == locale,
                catalog_entries.c.key == key,
            )
        ).mappings().first()
    else:
        conn.execute(
            insert(catalog_entries).values(
                locale=locale,
                key=key,
                value=body.value,
                description=body.description,
                created_at=now,
                updated_at=now,
            )
        )
        row = conn.execute(
            select(catalog_entries).where(
                catalog_entries.c.locale == locale,
                catalog_entries.c.key == key,
            )
        ).mappings().first()

    return CatalogEntry(**dict(row))


# ---------------------------------------------------------------- DELETE /api/catalog/{locale}/{key}  （管理员）

@router.delete("/{locale}/{key:path}", status_code=204)
def delete_entry(
    locale: Annotated[str, Path()],
    key: Annotated[str, Path()],
    conn: DbConn,
    request: Request,
    _admin: AdminUser,
):
    _validate_locale_param(locale, request)
    _validate_key_param(key)

    result = conn.execute(
        delete(catalog_entries).where(
            catalog_entries.c.locale == locale,
            catalog_entries.c.key == key,
        )
    )
    if result.rowcount == 0:
        raise not_found(f"Entry not found: {locale}/{key}")
