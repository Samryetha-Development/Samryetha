"""/api/tasks — 开发任务追踪（独立表，不依赖 feedback）。

仅管理员可读写（与论坛内管理员任务页一致）；含嵌套评论。
author 记录创建者，将来接 OAuth 后同一会话模型仍可用（无本地账号假设）。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from .. import tasks as service
from ..deps import CurrentUser, DbConn, require_admin
from ..errors import not_found

router = APIRouter()

TaskId = Annotated[int, Path(ge=1)]

PRIORITY = Literal["urgent", "normal"]
STATUS = Literal["open", "done"]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str | None = Field(default=None, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)
    priority: PRIORITY | None = None
    status: STATUS | None = None


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)
    priority: PRIORITY | None = None


class TaskStatusBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: STATUS


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    body: str = Field(min_length=1, max_length=5000)
    parentCommentId: int | None = Field(default=None, ge=1)


class CommentPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    body: str = Field(min_length=1, max_length=5000)


# ================================================================ 读（仅管理员）


@router.get("/api/tasks")
def list_tasks(conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    data = service.list_tasks(conn)
    # 仅管理员可见 => 管理员始终有写权限。
    data["canWrite"] = True
    return data


# ================================================================ 写（仅管理员）


@router.post("/api/tasks", status_code=201)
def create_task(body: TaskCreate, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    return service.create_task(conn, user.id, body.model_dump())


@router.patch("/api/tasks/{id}")
def update_task(id: TaskId, body: TaskPatch, conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    return service.update_task(conn, id, body.model_dump(exclude_none=True))


@router.post("/api/tasks/{id}/status")
def set_task_status(id: TaskId, body: TaskStatusBody, conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    return service.set_task_status(conn, id, body.status)


@router.delete("/api/tasks/{id}")
def delete_task(id: TaskId, conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    service.delete_task(conn, id)
    return {"ok": True}


# ================================================================ 评论（仅管理员）


@router.get("/api/tasks/{id}/comments")
def list_comments(id: TaskId, conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    return {"items": service.list_comments(conn, id)}


@router.post("/api/tasks/{id}/comments", status_code=201)
def create_comment(id: TaskId, body: CommentBody, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    return service.create_comment(conn, user.id, id, body.body, body.parentCommentId)


@router.patch("/api/tasks/comments/{comment_id}")
def update_comment(comment_id: TaskId, body: CommentPatch, conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    if service.get_comment_for_authz(conn, comment_id) is None:
        raise not_found("Comment not found")
    return service.update_comment(conn, comment_id, body.body)


@router.delete("/api/tasks/comments/{comment_id}")
def delete_comment(comment_id: TaskId, conn: DbConn, _user: CurrentUser = Depends(require_admin)) -> dict:
    if service.get_comment_for_authz(conn, comment_id) is None:
        raise not_found("Comment not found")
    service.delete_comment(conn, comment_id)
    return {"ok": True}
