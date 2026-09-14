"""/api/i18n — 翻译提交站公开 + 认证 API。

公开（无需登录）:
  GET  /api/i18n/catalog          → 所有 source strings（语言 catalog）
  GET  /api/i18n/catalog/{key}    → 单条 source string

认证用户:
  GET  /api/i18n/submissions      → 当前用户的提交列表（可选 ?lang=zh&key=...）
  POST /api/i18n/submissions      → 提交翻译
  GET  /api/i18n/submissions/{id} → 单条提交详情

管理员:
  GET  /api/i18n/admin/submissions            → 全部提交（可按状态/语言过滤）
  POST /api/i18n/submissions/{id}/approve     → 批准
  POST /api/i18n/submissions/{id}/reject      → 拒绝
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..authz import assert_can, Abilities
from ..deps import CurrentUser, DbConn, get_current_user, require_active_user
from ..errors import auth_required, bad_request, forbidden, not_found
from .. import i18n as service

router = APIRouter(prefix="/api/i18n", tags=["i18n"])

SubmissionId = Annotated[int, Path(ge=1)]


# ---------------------------------------------------------------- request models


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str = Field(min_length=1, max_length=255, description="source string key")
    lang: str = Field(min_length=2, max_length=10, description="BCP-47 language tag, e.g. zh-Hans")
    value: str = Field(min_length=1, max_length=10000, description="translated text")
    note: str | None = Field(default=None, max_length=1000)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------- catalog (public)


@router.get("/catalog")
def list_catalog(
    conn: DbConn,
    lang: str | None = Query(default=None, description="filter by language"),
):
    """Return all source strings, optionally with approved translations for a language."""
    return {"entries": service.list_catalog(conn, lang=lang)}


@router.get("/catalog/{key:path}")
def get_catalog_entry(key: str, conn: DbConn, lang: str | None = Query(default=None)):
    """Return a single source string by key."""
    entry = service.get_catalog_entry(conn, key, lang=lang)
    if entry is None:
        raise not_found("Catalog entry not found")
    return entry


# ---------------------------------------------------------------- submissions (authed user)


@router.get("/submissions")
def list_my_submissions(
    conn: DbConn,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user)],
    lang: str | None = Query(default=None),
    key: str | None = Query(default=None),
    status: Literal["pending", "approved", "rejected"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List submissions. Admins see all; regular users see only their own."""
    if current_user is None:
        raise auth_required()
    is_admin = current_user.role in ("admin", "moderator")
    rows = service.list_submissions(
        conn,
        user_id=None if is_admin else current_user.id,
        lang=lang,
        key=key,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"submissions": rows}


@router.post("/submissions", status_code=201)
def submit_translation(
    body: SubmitBody,
    conn: DbConn,
    current_user: Annotated[CurrentUser, Depends(require_active_user)],
):
    """Submit a translation for a source string."""
    entry = service.get_catalog_entry(conn, body.key, lang=None)
    if entry is None:
        raise not_found(f"No catalog entry for key: {body.key!r}")
    sub = service.create_submission(
        conn,
        user_id=current_user.id,
        key=body.key,
        lang=body.lang,
        value=body.value,
        note=body.note,
    )
    return sub


@router.get("/submissions/{submission_id}")
def get_submission(
    submission_id: SubmissionId,
    conn: DbConn,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user)],
):
    if current_user is None:
        raise auth_required()
    sub = service.get_submission(conn, submission_id)
    if sub is None:
        raise not_found("Submission not found")
    is_admin = current_user.role in ("admin", "moderator")
    if not is_admin and sub["user_id"] != current_user.id:
        raise forbidden("Not your submission")
    return sub


# ---------------------------------------------------------------- admin actions


@router.post("/submissions/{submission_id}/approve")
def approve_submission(
    submission_id: SubmissionId,
    conn: DbConn,
    current_user: Annotated[CurrentUser, Depends(require_active_user)],
):
    """Approve a pending translation submission (admin/moderator only)."""
    if current_user.role not in ("admin", "moderator"):
        raise forbidden("Admin access required")
    sub = service.get_submission(conn, submission_id)
    if sub is None:
        raise not_found("Submission not found")
    updated = service.update_submission_status(
        conn, submission_id, status="approved", reviewer_id=current_user.id
    )
    return updated


@router.post("/submissions/{submission_id}/reject")
def reject_submission(
    submission_id: SubmissionId,
    body: RejectBody,
    conn: DbConn,
    current_user: Annotated[CurrentUser, Depends(require_active_user)],
):
    """Reject a pending translation submission (admin/moderator only)."""
    if current_user.role not in ("admin", "moderator"):
        raise forbidden("Admin access required")
    sub = service.get_submission(conn, submission_id)
    if sub is None:
        raise not_found("Submission not found")
    updated = service.update_submission_status(
        conn,
        submission_id,
        status="rejected",
        reviewer_id=current_user.id,
        reject_reason=body.reason,
    )
    return updated
