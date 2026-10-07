"""Typed HTTP boundary for the admin task tracker."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from .. import tasks as service
from ..deps import CurrentUser, DbConn, require_admin
from ..ids import TaskCommentID, TaskID, UserID
from ..tasks.models import (
    CommentBody, CommentPatch, TaskCommentListResponse,
    TaskCommentResponse, TaskCreate, TaskItemResponse, TaskListResponse,
    TaskOperationOkResponse, TaskPatch, TaskStatusBody,
)

router = APIRouter()

TaskIDPath = Annotated[TaskID, Path(ge=1)]
CommentIDPath = Annotated[TaskCommentID, Path(ge=1)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]


@router.get("/api/tasks", response_model=TaskListResponse)
def list_tasks(conn: DbConn, _user: AdminDep) -> TaskListResponse:
    return service.list_tasks(conn)


@router.post("/api/tasks", status_code=201, response_model=TaskItemResponse)
def create_task(body: TaskCreate, conn: DbConn, user: AdminDep) -> TaskItemResponse:
    return service.create_task(conn, UserID(user.id), body)


@router.patch("/api/tasks/{id}", response_model=TaskItemResponse)
def update_task(id: TaskIDPath, body: TaskPatch, conn: DbConn, _user: AdminDep) -> TaskItemResponse:
    return service.update_task(conn, id, body)


@router.post("/api/tasks/{id}/status", response_model=TaskItemResponse)
def set_task_status(id: TaskIDPath, body: TaskStatusBody, conn: DbConn, _user: AdminDep) -> TaskItemResponse:
    return service.set_task_status(conn, id, body.status)


@router.delete("/api/tasks/{id}", response_model=TaskOperationOkResponse)
def delete_task(id: TaskIDPath, conn: DbConn, _user: AdminDep) -> TaskOperationOkResponse:
    service.delete_task(conn, id)
    return TaskOperationOkResponse()


@router.get("/api/tasks/{id}/comments", response_model=TaskCommentListResponse)
def list_comments(id: TaskIDPath, conn: DbConn, _user: AdminDep) -> TaskCommentListResponse:
    return TaskCommentListResponse(items=service.list_comments(conn, id))


@router.post("/api/tasks/{id}/comments", status_code=201, response_model=TaskCommentResponse)
def create_comment(id: TaskIDPath, body: CommentBody, conn: DbConn, user: AdminDep) -> TaskCommentResponse:
    return service.create_comment(conn, UserID(user.id), id, body.body, body.parent_comment_id)


@router.patch("/api/tasks/comments/{comment_id}", response_model=TaskCommentResponse)
def update_comment(comment_id: CommentIDPath, body: CommentPatch, conn: DbConn, _user: AdminDep) -> TaskCommentResponse:
    return service.update_comment(conn, comment_id, body.body)


@router.delete("/api/tasks/comments/{comment_id}", response_model=TaskOperationOkResponse)
def delete_comment(comment_id: CommentIDPath, conn: DbConn, _user: AdminDep) -> TaskOperationOkResponse:
    service.delete_comment(conn, comment_id)
    return TaskOperationOkResponse()
