"""翻译提交与审核 API。

GET    /api/submissions         — 登录用户查看自己的提交（管理员可见全部）
POST   /api/submissions         — 登录用户提交翻译建议
POST   /api/submissions/{id}/review  — 管理员审核（approve/reject）

审核操作：
  approve → 同事务更新 submissions.status + upsert catalog_entries（原子性）
  reject  → 仅更新 submissions.status
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import insert, select, update

from ..db import now_ms
from ..deps import ActiveUser, AdminUser, DbConn
from ..errors import bad_request, not_found
from ..schema import catalog_entries, submissions
from ..validation import validate_key, validate_locale

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


# ---------------------------------------------------------------- models

class SubmissionOut(BaseModel):
    id: int
    locale: str
    key: str
    value: str
    note: str | None
    submitter_id: int
    submitter_name: str
    status: str
    reviewer_id: int | None
    reviewer_name: str | None
    review_note: str | None
    reviewed_at: int | None
    created_at: int
    updated_at: int


class SubmissionsResponse(BaseModel):
    submissions: list[SubmissionOut]
    total: int


class CreateSubmissionBody(BaseModel):
    locale: str
    key: str
    value: str
    note: str | None = None

    @field_validator("value")
    @classmethod
    def value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("value must not be empty")
        return v


class ReviewBody(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None


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


def _row_to_out(r) -> SubmissionOut:  # noqa: ANN001
    return SubmissionOut(**dict(r))


# ---------------------------------------------------------------- GET /api/submissions

@router.get("", response_model=SubmissionsResponse)
def list_submissions(
    conn: DbConn,
    user: ActiveUser,
    status: str | None = Query(default=None, description="过滤 status: pending|approved|rejected"),
    locale: str | None = Query(default=None),
):
    stmt = select(submissions)
    if not user.is_admin:
        # 普通用户只能看自己的提交
        stmt = stmt.where(submissions.c.submitter_id == user.id)
    if status:
        if status not in ("pending", "approved", "rejected"):
            raise bad_request("status must be one of: pending, approved, rejected")
        stmt = stmt.where(submissions.c.status == status)
    if locale:
        stmt = stmt.where(submissions.c.locale == locale)

    stmt = stmt.order_by(submissions.c.created_at.desc())
    rows = conn.execute(stmt).mappings().all()
    items = [_row_to_out(r) for r in rows]
    return SubmissionsResponse(submissions=items, total=len(items))


# ---------------------------------------------------------------- POST /api/submissions

@router.post("", response_model=SubmissionOut, status_code=201)
def create_submission(
    body: CreateSubmissionBody,
    conn: DbConn,
    user: ActiveUser,
    request: Request,
):
    _validate_locale_param(body.locale, request)
    _validate_key_param(body.key)

    now = now_ms()
    result = conn.execute(
        insert(submissions).values(
            locale=body.locale,
            key=body.key,
            value=body.value,
            note=body.note,
            submitter_id=user.id,
            submitter_name=user.display_name,
            status="pending",
            created_at=now,
            updated_at=now,
        )
    )
    row = conn.execute(
        select(submissions).where(submissions.c.id == result.inserted_primary_key[0])
    ).mappings().first()
    return _row_to_out(row)


# ---------------------------------------------------------------- POST /api/submissions/{id}/review

@router.post("/{submission_id}/review", response_model=SubmissionOut)
def review_submission(
    submission_id: Annotated[int, Path()],
    body: ReviewBody,
    conn: DbConn,
    admin: AdminUser,
):
    row = conn.execute(
        select(submissions).where(submissions.c.id == submission_id)
    ).mappings().first()
    if row is None:
        raise not_found(f"Submission {submission_id} not found")

    sub = dict(row)
    if sub["status"] != "pending":
        raise bad_request(f"Submission is already {sub['status']}")

    now = now_ms()

    # 更新审核状态
    conn.execute(
        update(submissions)
        .where(submissions.c.id == submission_id)
        .values(
            status="approved" if body.action == "approve" else "rejected",
            reviewer_id=admin.id,
            reviewer_name=admin.display_name,
            review_note=body.note,
            reviewed_at=now,
            updated_at=now,
        )
    )

    if body.action == "approve":
        # 原子性 upsert catalog_entries
        existing = conn.execute(
            select(catalog_entries).where(
                catalog_entries.c.locale == sub["locale"],
                catalog_entries.c.key == sub["key"],
            )
        ).first()
        if existing:
            conn.execute(
                update(catalog_entries)
                .where(
                    catalog_entries.c.locale == sub["locale"],
                    catalog_entries.c.key == sub["key"],
                )
                .values(value=sub["value"], updated_at=now)
            )
        else:
            conn.execute(
                insert(catalog_entries).values(
                    locale=sub["locale"],
                    key=sub["key"],
                    value=sub["value"],
                    created_at=now,
                    updated_at=now,
                )
            )

    updated = conn.execute(
        select(submissions).where(submissions.c.id == submission_id)
    ).mappings().first()
    return _row_to_out(updated)
