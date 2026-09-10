"""/api/tasks — 开发任务追踪（独立表，不依赖 feedback）。

仅管理员可访问（读与写都需要 admin 会话）。author 记录创建者。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field

from .. import tasks as service
from ..deps import DbConn, require_admin

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


# ================================================================ 读（仅 admin）


@router.get("/api/tasks")
def list_tasks(conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    data = service.list_tasks(conn)
    data["canWrite"] = True
    return data


# ================================================================ 写（仅 admin）


@router.post("/api/tasks", status_code=201)
def create_task(body: TaskCreate, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    return service.create_task(conn, user.id, body.model_dump())


@router.patch("/api/tasks/{id}")
def update_task(id: TaskId, body: TaskPatch, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    return service.update_task(conn, id, body.model_dump(exclude_none=True))


@router.post("/api/tasks/{id}/status")
def set_task_status(id: TaskId, body: TaskStatusBody, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    return service.set_task_status(conn, id, body.status)


@router.delete("/api/tasks/{id}")
def delete_task(id: TaskId, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> dict:
    service.delete_task(conn, id)
    return {"ok": True}
