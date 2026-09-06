"""/api/feedback + /api/admin/feedback/* + /api/agent/v1 — 镜像 feedback/routes.ts + agent.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import feedback as service
from .. import feedback_backup as backup
from ..authz import Abilities, assert_can
from ..deps import CurrentUser, DbConn, require_active_user
from ..errors import auth_required, forbidden, not_found

router = APIRouter()

FeedbackId = Annotated[int, Path(ge=1)]


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    projectId: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=5000)
    type: Literal["bug", "suggestion"]
    urgency: Literal["urgent", "normal"] | None = None


class FeedbackPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=5000)
    type: Literal["bug", "suggestion"] | None = None
    urgency: Literal["urgent", "normal"] | None = None


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["done", "expired", "open"]


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    body: str = Field(min_length=1, max_length=5000)
    parentCommentId: int | None = Field(default=None, ge=1)


class CommentPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    body: str = Field(min_length=1, max_length=5000)


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class MemberRow(BaseModel):
    userId: int = Field(ge=1)
    isProgrammer: bool


class MembersBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    members: list[MemberRow] = Field(max_length=500)


class RestoreBackupBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=100)


class BackupSettingsBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    backupCron: str = Field(max_length=100)
    backupKeep: int = Field(ge=1, le=500)


# ================================================================ 用户反馈


@router.get("/api/feedback/projects/mine")
def my_projects(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return {"items": service.list_my_projects(conn, user.id, user.role == "admin")}


@router.get("/api/feedback")
def list_feedback(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    projectId: int = Query(ge=1),
) -> dict:
    project = service.get_project_for_authz(conn, projectId)
    if project is None:
        raise not_found("Project not found")
    assert_can(user, Abilities.FEEDBACK_VIEW, {"type": "feedbackProject", **project}, conn)
    return service.list_feedback(conn, user.id, user.role == "admin", projectId)


@router.post("/api/feedback", status_code=201)
def create_feedback(
    body: FeedbackBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    project = service.get_project_for_authz(conn, body.projectId)
    if project is None:
        raise not_found("Project not found")
    assert_can(user, Abilities.FEEDBACK_CREATE, {"type": "feedbackProject", **project}, conn)
    return service.create_feedback(conn, user.id, body.model_dump())


@router.patch("/api/feedback/{id}")
def update_feedback(
    id: FeedbackId,
    body: FeedbackPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_UPDATE, {"type": "feedbackItem", **item}, conn)
    return service.update_feedback(conn, id, body.model_dump(exclude_none=True))


@router.delete("/api/feedback/{id}")
def delete_feedback(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_DELETE, {"type": "feedbackItem", **item}, conn)
    service.delete_feedback(conn, user.id, id)
    return {"ok": True}


@router.post("/api/feedback/{id}/status")
def set_feedback_status(
    id: FeedbackId,
    body: StatusBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_MANAGE, {"type": "feedbackItem", **item}, conn)
    return service.set_feedback_status(conn, id, body.status)


@router.get("/api/feedback/{id}/comments")
def list_comments(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_VIEW, {"type": "feedbackItem", **item}, conn)
    return {"items": service.list_comments(conn, id)}


@router.post("/api/feedback/{id}/comments", status_code=201)
def create_comment(
    id: FeedbackId,
    body: CommentBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_COMMENT_CREATE, {"type": "feedbackComment", "projectId": item["projectId"]}, conn)
    return service.create_comment(conn, user.id, id, body.body, body.parentCommentId)


@router.patch("/api/feedback/comments/{comment_id}")
def update_comment(
    comment_id: FeedbackId,
    body: CommentPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    comment = service.get_comment_for_authz(conn, comment_id)
    if comment is None:
        raise not_found("Comment not found")
    assert_can(user, Abilities.FEEDBACK_COMMENT_UPDATE, {"type": "feedbackComment", **comment}, conn)
    return service.update_comment(conn, comment_id, body.body)


@router.delete("/api/feedback/comments/{comment_id}")
def delete_comment(
    comment_id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    comment = service.get_comment_for_authz(conn, comment_id)
    if comment is None:
        raise not_found("Comment not found")
    assert_can(user, Abilities.FEEDBACK_COMMENT_DELETE, {"type": "feedbackComment", **comment}, conn)
    service.delete_comment(conn, comment_id)
    return {"ok": True}


# ================================================================ 项目管理(admin)


@router.get("/api/feedback/projects")
def list_projects(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    return {"items": service.list_projects_for_admin(conn)}


@router.post("/api/feedback/projects", status_code=201)
def create_project(
    body: ProjectBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    return service.create_project(conn, user.id, body.name, body.description)


@router.patch("/api/feedback/projects/{id}")
def update_project(
    id: FeedbackId,
    body: ProjectPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    service.update_project(conn, id, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/api/feedback/projects/{id}")
def delete_project(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    service.delete_project(conn, user.id, id)
    return {"ok": True}


@router.put("/api/feedback/projects/{id}/members")
def set_project_members(
    id: FeedbackId,
    body: MembersBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    service.set_project_members(conn, id, [m.model_dump() for m in body.members])
    return {"ok": True}


# ================================================================ 备份 (admin)


def _require_admin_view(request: Request, user: CurrentUser) -> None:
    db = request.app.state.db
    with db.request_conn() as conn:
        assert_can(user, Abilities.ADMIN_VIEW, None, conn)


@router.get("/api/admin/feedback/backups")
def list_backups(request: Request, user: CurrentUser = Depends(require_active_user)) -> dict:
    _require_admin_view(request, user)
    db = request.app.state.db
    with db.request_conn() as conn:
        settings = backup.get_backup_settings(conn)
    return {"backups": backup.list_backups(db), "settings": settings}


@router.post("/api/admin/feedback/backups/create")
def create_backup(request: Request, user: CurrentUser = Depends(require_active_user)) -> dict:
    _require_admin_view(request, user)
    return {"backup": backup.create_backup(request.app.state.db)}


@router.post("/api/admin/feedback/backups/restore")
def restore_backup(
    body: RestoreBackupBody,
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    _require_admin_view(request, user)
    backup.restore_backup(request.app.state.db, body.name)
    return {"ok": True, "restartRequired": True}


@router.put("/api/admin/feedback/backups/settings")
def set_backup_settings(
    body: BackupSettingsBody,
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    _require_admin_view(request, user)
    db = request.app.state.db
    with db.request_conn() as conn:
        backup.set_backup_settings(conn, body.backupCron, body.backupKeep)
    backup_scheduler = getattr(request.app.state, "backup_scheduler", None)
    if backup_scheduler is not None:
        backup_scheduler.start()
    return {"ok": True}


# ================================================================ Agent 密钥 (admin 鉴权)


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=64)
    role: Literal["read", "write"]
    projectIds: list[int] = Field(default_factory=list)


class KeyEnabledBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool


# 密钥管理属敏感操作：必须登录且具备 admin 权限（镜像 feedback/agent.ts 的 requireAdmin）
def _require_admin(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CurrentUser:
    assert_can(user, Abilities.ADMIN_VIEW, None, conn)
    return user


@router.get("/api/admin/feedback/keys")
def list_keys(conn: DbConn, _user: CurrentUser = Depends(_require_admin)) -> dict:
    return {"items": service.list_keys(conn)}


@router.post("/api/admin/feedback/keys", status_code=201)
def create_key(
    body: KeyBody,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> dict:
    valid = {p["id"] for p in service.list_projects_for_admin(conn)}
    project_ids = [x for x in body.projectIds if x in valid]
    key, key_row = service.create_key(conn, body.name, body.role, project_ids)
    return {"key": key, "keyRow": key_row}


@router.put("/api/admin/feedback/keys/{id}")
def set_key_enabled(
    id: FeedbackId,
    body: KeyEnabledBody,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> dict:
    service.set_key_enabled(conn, id, body.enabled)
    return {"ok": True}


@router.delete("/api/admin/feedback/keys/{id}")
def delete_key(
    id: FeedbackId,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> dict:
    service.delete_key(conn, id)
    return {"ok": True}


# ================================================================ Agent API


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header
    auth = request.headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]
    return None


def _require_agent(request: Request, conn) -> dict:
    key = service.verify_key(conn, _extract_key(request))
    if key is None:
        raise auth_required("Invalid or missing API key")
    return key


def _require_write(request: Request, conn) -> dict:
    key = _require_agent(request, conn)
    if key["role"] != "write":
        raise forbidden("This API key is read-only")
    return key


def _access_projects(conn) -> list[dict]:
    return [
        {"id": p["id"], "name": p["name"], "description": p["description"]}
        for p in service.list_projects_for_admin(conn)
    ]


def _summary(items: list[dict]) -> dict:
    return {
        "open": sum(1 for i in items if i["status"] == "open"),
        "done": sum(1 for i in items if i["status"] == "done"),
        "expired": sum(1 for i in items if i["status"] == "expired"),
    }


@router.get("/api/agent/v1")
def agent_index() -> dict:
    return {
        "name": "Samryetha Feedback Agent API",
        "version": "v1",
        "auth": 'Header "X-Api-Key: <key>" (or Authorization: Bearer <key>)',
        "endpoints": {
            "index": {"method": "GET", "path": "/api/agent/v1", "auth": "none"},
            "readme": {"method": "GET", "path": "/api/agent/v1/README", "auth": "none"},
            "projects": {"method": "GET", "path": "/api/agent/v1/projects", "auth": "any key"},
            "tasks": {"method": "GET", "path": "/api/agent/v1/tasks", "auth": "any key", "query": "?projectId=&status=&type="},
            "task": {"method": "GET", "path": "/api/agent/v1/tasks/:id", "auth": "any key"},
            "status": {"method": "POST", "path": "/api/agent/v1/tasks/:id/status", "auth": "write key", "body": '{"status":"done"|"open"}'},
        },
    }


@router.get("/api/agent/v1/README")
def agent_readme() -> str:
    return "\n".join(
        [
            "# Samryetha Feedback Agent API",
            "",
            "GET /api/agent/v1              端点索引（免 key）",
            "GET /api/agent/v1/projects     该 key 可访问的项目",
            "GET /api/agent/v1/tasks        任务列表 + open/done/expired 汇总，支持 ?projectId=&status=&type=",
            "GET /api/agent/v1/tasks/:id    单任务详情",
            "POST /api/agent/v1/tasks/:id/status  {status: \"done\"|\"open\"}（需 write 权限）",
            "",
            "鉴权头：X-Api-Key: <key>",
        ]
    )


@router.get("/api/agent/v1/projects")
def agent_projects(request: Request, conn: DbConn) -> dict:
    key = _require_agent(request, conn)
    all_projects = _access_projects(conn)
    return {"items": [p for p in all_projects if service.agent_can_access_project(key, p["id"])]}


@router.get("/api/agent/v1/tasks")
def agent_tasks(
    request: Request,
    conn: DbConn,
    projectId: int | None = Query(default=None, ge=1),
    status: Literal["open", "done", "expired"] | None = Query(default=None),
    type: Literal["bug", "suggestion"] | None = Query(default=None),
) -> dict:
    key = _require_agent(request, conn)
    if projectId is not None and not service.agent_can_access_project(key, projectId):
        raise forbidden("This API key cannot access this project")
    items = service.list_feedback_for_agent(conn, projectId)
    items = [i for i in items if service.agent_can_access_project(key, i["projectId"])]
    if status:
        items = [i for i in items if i["status"] == status]
    if type:
        items = [i for i in items if i["type"] == type]
    return {"items": items, "summary": _summary(items)}


@router.get("/api/agent/v1/tasks/{id}")
def agent_task(id: FeedbackId, request: Request, conn: DbConn) -> dict:
    key = _require_agent(request, conn)
    item = service.item_by_id(conn, id)
    if item is None:
        raise not_found("Task not found")
    if not service.agent_can_access_project(key, item["projectId"]):
        raise forbidden("This API key cannot access this project")
    return item


class AgentStatusBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["done", "open"]


@router.post("/api/agent/v1/tasks/{id}/status")
def agent_set_status(
    id: FeedbackId,
    body: AgentStatusBody,
    request: Request,
    conn: DbConn,
) -> dict:
    key = _require_write(request, conn)
    item = service.item_by_id(conn, id)
    if item is None:
        raise not_found("Task not found")
    if not service.agent_can_access_project(key, item["projectId"]):
        raise forbidden("This API key cannot access this project")
    return service.set_feedback_status(conn, id, body.status)
