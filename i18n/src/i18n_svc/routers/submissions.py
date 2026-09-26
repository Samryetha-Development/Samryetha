"""翻译提交与审核 API。

GET    /api/submissions         — 登录用户查看自己的提交（管理员可见全部）
POST   /api/submissions         — 登录用户提交翻译建议
GET    /api/submissions/{id}/notes   — 查看详情讨论记录
POST   /api/submissions/{id}/notes   — 管理员追加详情记录
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
from sqlalchemy.exc import IntegrityError

from ..db import now_ms
from ..deps import ActiveUser, AdminUser, DbConn
from ..errors import bad_request, forbidden, not_found
from ..schema import catalog_entries, submission_notes, submissions
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


class SubmissionNoteOut(BaseModel):
    id: int
    submission_id: int
    author_id: int
    author_name: str
    body: str
    created_at: int


class SubmissionNotesResponse(BaseModel):
    notes: list[SubmissionNoteOut]


class CreateSubmissionNoteBody(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("note must not be empty")
        if len(value) > 4000:
            raise ValueError("note must not exceed 4000 characters")
        return value


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


def _is_catalog_unique_violation(exc: IntegrityError) -> bool:
    """仅当 IntegrityError 确实是 (locale, key) 唯一约束冲突时返回 True。

    SQLite 的报错形如：
      "UNIQUE constraint failed: catalog_entries.locale, catalog_entries.key"
    其他完整性错误（NOT NULL、CHECK、其他表/约束）不得被误判为"已存在"，
    否则会静默走 update 分支并可能批准一个没有写入 catalog 的提交。
    """
    orig = getattr(exc, "orig", None)
    text = str(orig if orig is not None else exc)
    if "uq_catalog_locale_key" in text:
        return True
    lowered = text.lower()
    return (
        "unique" in lowered
        and "catalog_entries" in lowered
        and "locale" in lowered
        and "key" in lowered
    )


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


# ---------------------------------------------------------------- GET/POST /api/submissions/{id}/notes

@router.get("/{submission_id}/notes", response_model=SubmissionNotesResponse)
def list_submission_notes(
    submission_id: Annotated[int, Path()],
    conn: DbConn,
    user: ActiveUser,
):
    submission = conn.execute(
        select(submissions).where(submissions.c.id == submission_id)
    ).mappings().first()
    if submission is None:
        raise not_found(f"Submission {submission_id} not found")
    if not user.is_admin and submission["submitter_id"] != user.id:
        raise forbidden("You cannot view notes for this submission")

    rows = conn.execute(
        select(submission_notes)
        .where(submission_notes.c.submission_id == submission_id)
        .order_by(submission_notes.c.created_at.asc(), submission_notes.c.id.asc())
    ).mappings().all()
    return SubmissionNotesResponse(notes=[SubmissionNoteOut(**dict(row)) for row in rows])


@router.post("/{submission_id}/notes", response_model=SubmissionNoteOut, status_code=201)
def create_submission_note(
    submission_id: Annotated[int, Path()],
    body: CreateSubmissionNoteBody,
    conn: DbConn,
    admin: AdminUser,
):
    submission = conn.execute(
        select(submissions.c.id).where(submissions.c.id == submission_id)
    ).first()
    if submission is None:
        raise not_found(f"Submission {submission_id} not found")

    result = conn.execute(
        insert(submission_notes).values(
            submission_id=submission_id,
            author_id=admin.id,
            author_name=admin.display_name,
            body=body.body,
            created_at=now_ms(),
        )
    )
    row = conn.execute(
        select(submission_notes).where(submission_notes.c.id == result.inserted_primary_key[0])
    ).mappings().first()
    return SubmissionNoteOut(**dict(row))


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
        # 并发安全的 upsert：先尝试 insert，命中 uq_catalog_locale_key 则回退为 update。
        # insert 包在 savepoint 中，避免 IntegrityError 污染外层 request 事务
        #（外层事务还包含 submissions 状态更新，必须保留）。
        try:
            with conn.begin_nested():
                conn.execute(
                    insert(catalog_entries).values(
                        locale=sub["locale"],
                        key=sub["key"],
                        value=sub["value"],
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as exc:
            # 只把真正的 (locale, key) 唯一冲突当作"已存在"；其余完整性错误必须上抛，
            # 否则会静默批准一个并未写入 catalog 的提交。
            if not _is_catalog_unique_violation(exc):
                raise
            result = conn.execute(
                update(catalog_entries)
                .where(
                    catalog_entries.c.locale == sub["locale"],
                    catalog_entries.c.key == sub["key"],
                )
                .values(value=sub["value"], updated_at=now)
            )
            if result.rowcount != 1:
                # 冲突说"已存在"，回退 update 却没命中行：状态不一致，必须报错而不是
                # 静默批准。
                raise RuntimeError(
                    f"catalog upsert fallback updated {result.rowcount} rows for "
                    f"{sub['locale']}/{sub['key']}; expected exactly 1"
                )

    updated = conn.execute(
        select(submissions).where(submissions.c.id == submission_id)
    ).mappings().first()
    return _row_to_out(updated)
