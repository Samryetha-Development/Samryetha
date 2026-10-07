"""/api/feedback + /api/admin/feedback/* + /api/agent/v1 — 镜像 feedback/routes.ts + agent.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import feedback as service
from ..feedback import backup
from ..authz import Abilities, assert_can
from ..core.deps import CurrentUser, DbConn, require_active_user
from ..core.errors import auth_required, forbidden, not_found
from ..feedback.models import (
    AgentKeyCreatedResponse,
    AgentKeyResponse,
    AgentKeysListResponse,
    AgentEndpointResponse,
    AgentIndexResponse,
    AgentProjectResponse,
    AgentProjectsResponse,
    AgentRole,
    AgentStatusBody,
    AgentSummaryResponse,
    AgentTasksResponse,
    BackupCreatedResponse,
    BackupFileResponse,
    BackupSettingsResponse,
    BackupsResponse,
    CommentBody,
    CommentPatch,
    CommentResponse,
    CommentsListResponse,
    FeedbackBody,
    FeedbackItemResponse,
    FeedbackListResponse,
    FeedbackPatch,
    FeedbackStatus,
    KeyBody,
    KeyEnabledBody,
    MembersBody,
    MyProjectsResponse,
    OperationOkResponse,
    ProjectAdminResponse,
    ProjectBody,
    ProjectPatch,
    ProjectsAdminListResponse,
    StatusBody,
    RestartRequiredResponse,
)

router = APIRouter()

FeedbackId = Annotated[int, Path(ge=1)]


class RestoreBackupBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=100)


class BackupSettingsBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    backupCron: str = Field(max_length=100)
    backupKeep: int = Field(ge=1, le=500)


# ================================================================ 用户反馈


@router.get("/api/feedback/projects/mine", response_model=MyProjectsResponse)
def my_projects(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> MyProjectsResponse:
    return MyProjectsResponse(items=service.list_my_projects(conn, user.id, user.role == "admin"))


@router.get("/api/feedback", response_model=FeedbackListResponse)
def list_feedback(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    projectId: int = Query(ge=1),
) -> FeedbackListResponse:
    project = service.get_project_for_authz(conn, projectId)
    if project is None:
        raise not_found("Project not found")
    assert_can(user, Abilities.FEEDBACK_VIEW, {"type": "feedbackProject", **project}, conn)
    return service.list_feedback(conn, user.id, user.role == "admin", projectId)


@router.post("/api/feedback", status_code=201, response_model=FeedbackItemResponse)
def create_feedback(
    body: FeedbackBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FeedbackItemResponse:
    project = service.get_project_for_authz(conn, body.project_id)
    if project is None:
        raise not_found("Project not found")
    assert_can(user, Abilities.FEEDBACK_CREATE, {"type": "feedbackProject", **project}, conn)
    return service.create_feedback(conn, user.id, body)


@router.patch("/api/feedback/{id}", response_model=FeedbackItemResponse)
def update_feedback(
    id: FeedbackId,
    body: FeedbackPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FeedbackItemResponse:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_UPDATE, {"type": "feedbackItem", **item}, conn)
    return service.update_feedback(conn, id, body)


@router.delete("/api/feedback/{id}", response_model=OperationOkResponse)
def delete_feedback(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_DELETE, {"type": "feedbackItem", **item}, conn)
    service.delete_feedback(conn, user.id, id)
    return OperationOkResponse(ok=True)


@router.post("/api/feedback/{id}/status", response_model=FeedbackItemResponse)
def set_feedback_status(
    id: FeedbackId,
    body: StatusBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FeedbackItemResponse:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_MANAGE, {"type": "feedbackItem", **item}, conn)
    return service.set_feedback_status(conn, id, body.status)


@router.get("/api/feedback/{id}/comments", response_model=CommentsListResponse)
def list_comments(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CommentsListResponse:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_VIEW, {"type": "feedbackItem", **item}, conn)
    return CommentsListResponse(items=service.list_comments(conn, id))


@router.post("/api/feedback/{id}/comments", status_code=201, response_model=CommentResponse)
def create_comment(
    id: FeedbackId,
    body: CommentBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CommentResponse:
    item = service.get_item_for_authz(conn, id)
    if item is None:
        raise not_found("Feedback item not found")
    assert_can(user, Abilities.FEEDBACK_COMMENT_CREATE, {"type": "feedbackComment", "projectId": item["projectId"]}, conn)
    return service.create_comment(conn, user.id, id, body.body, body.parent_comment_id)


@router.patch("/api/feedback/comments/{comment_id}", response_model=CommentResponse)
def update_comment(
    comment_id: FeedbackId,
    body: CommentPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CommentResponse:
    comment = service.get_comment_for_authz(conn, comment_id)
    if comment is None:
        raise not_found("Comment not found")
    assert_can(user, Abilities.FEEDBACK_COMMENT_UPDATE, {"type": "feedbackComment", **comment}, conn)
    return service.update_comment(conn, comment_id, body.body)


@router.delete("/api/feedback/comments/{comment_id}", response_model=OperationOkResponse)
def delete_comment(
    comment_id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    comment = service.get_comment_for_authz(conn, comment_id)
    if comment is None:
        raise not_found("Comment not found")
    assert_can(user, Abilities.FEEDBACK_COMMENT_DELETE, {"type": "feedbackComment", **comment}, conn)
    service.delete_comment(conn, comment_id)
    return OperationOkResponse(ok=True)


# ================================================================ 项目管理(admin)


@router.get("/api/feedback/projects", response_model=ProjectsAdminListResponse)
def list_projects(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> ProjectsAdminListResponse:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    return ProjectsAdminListResponse(items=service.list_projects_for_admin(conn))


@router.post("/api/feedback/projects", status_code=201, response_model=ProjectAdminResponse)
def create_project(
    body: ProjectBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> ProjectAdminResponse:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    return service.create_project(conn, user.id, body.name, body.description)


@router.patch("/api/feedback/projects/{id}", response_model=OperationOkResponse)
def update_project(
    id: FeedbackId,
    body: ProjectPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    service.update_project(conn, id, body)
    return OperationOkResponse(ok=True)


@router.delete("/api/feedback/projects/{id}", response_model=OperationOkResponse)
def delete_project(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    service.delete_project(conn, user.id, id)
    return OperationOkResponse(ok=True)


@router.put("/api/feedback/projects/{id}/members", response_model=OperationOkResponse)
def set_project_members(
    id: FeedbackId,
    body: MembersBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None, conn)
    service.set_project_members(conn, id, body.members)
    return OperationOkResponse(ok=True)


# ================================================================ 备份 (admin)


def _require_admin_view(request: Request, user: CurrentUser) -> None:
    db = request.app.state.db
    with db.request_conn() as conn:
        assert_can(user, Abilities.ADMIN_VIEW, None, conn)


@router.get("/api/admin/feedback/backups", response_model=BackupsResponse)
def list_backups(request: Request, user: CurrentUser = Depends(require_active_user)) -> BackupsResponse:
    _require_admin_view(request, user)
    db = request.app.state.db
    with db.request_conn() as conn:
        settings = backup.get_backup_settings(conn)
    return BackupsResponse(
        backups=[BackupFileResponse.model_validate(item) for item in backup.list_backups(db)],
        settings=BackupSettingsResponse.model_validate(settings),
    )


@router.post("/api/admin/feedback/backups/create", response_model=BackupCreatedResponse)
def create_backup(request: Request, user: CurrentUser = Depends(require_active_user)) -> BackupCreatedResponse:
    _require_admin_view(request, user)
    return BackupCreatedResponse(backup=BackupFileResponse.model_validate(backup.create_backup(request.app.state.db)))


@router.post("/api/admin/feedback/backups/restore", response_model=RestartRequiredResponse)
def restore_backup(
    body: RestoreBackupBody,
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> RestartRequiredResponse:
    _require_admin_view(request, user)
    backup.restore_backup(request.app.state.db, body.name)
    return RestartRequiredResponse(ok=True, restart_required=True)


@router.put("/api/admin/feedback/backups/settings", response_model=OperationOkResponse)
def set_backup_settings(
    body: BackupSettingsBody,
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    _require_admin_view(request, user)
    db = request.app.state.db
    with db.request_conn() as conn:
        backup.set_backup_settings(conn, body.backupCron, body.backupKeep)
    backup_scheduler = getattr(request.app.state, "backup_scheduler", None)
    if backup_scheduler is not None:
        backup_scheduler.start()
    return OperationOkResponse(ok=True)


# ================================================================ Agent 密钥 (admin 鉴权)


# 密钥管理属敏感操作：必须登录且具备 admin 权限（镜像 feedback/agent.ts 的 requireAdmin）
def _require_admin(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CurrentUser:
    assert_can(user, Abilities.ADMIN_VIEW, None, conn)
    return user


@router.get("/api/admin/feedback/keys", response_model=AgentKeysListResponse)
def list_keys(conn: DbConn, _user: CurrentUser = Depends(_require_admin)) -> AgentKeysListResponse:
    return AgentKeysListResponse(items=service.list_keys(conn))


@router.post("/api/admin/feedback/keys", status_code=201, response_model=AgentKeyCreatedResponse)
def create_key(
    body: KeyBody,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> AgentKeyCreatedResponse:
    valid = {project.id for project in service.list_projects_for_admin(conn)}
    project_ids = [value for value in body.project_ids if value in valid]
    key, key_row = service.create_key(conn, body.name, body.role, project_ids)
    return AgentKeyCreatedResponse(key=key, key_row=key_row)


@router.put("/api/admin/feedback/keys/{id}", response_model=OperationOkResponse)
def set_key_enabled(
    id: FeedbackId,
    body: KeyEnabledBody,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> OperationOkResponse:
    service.set_key_enabled(conn, id, body.enabled)
    return OperationOkResponse(ok=True)


@router.delete("/api/admin/feedback/keys/{id}", response_model=OperationOkResponse)
def delete_key(
    id: FeedbackId,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> OperationOkResponse:
    service.delete_key(conn, id)
    return OperationOkResponse(ok=True)


# ================================================================ Agent API


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header
    auth = request.headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]
    return None


def _require_agent(request: Request, conn: DbConn) -> AgentKeyResponse:
    key = service.verify_key(conn, _extract_key(request))
    if key is None:
        raise auth_required("Invalid or missing API key")
    return key


def _require_write(request: Request, conn: DbConn) -> AgentKeyResponse:
    key = _require_agent(request, conn)
    if key.role is not AgentRole.Write:
        raise forbidden("This API key is read-only")
    return key


def _access_projects(conn: DbConn) -> list[AgentProjectResponse]:
    return [
        AgentProjectResponse(id=project.id, name=project.name, description=project.description)
        for project in service.list_projects_for_admin(conn)
    ]


def _summary(items: list[FeedbackItemResponse]) -> AgentSummaryResponse:
    return AgentSummaryResponse(
        open=sum(1 for item in items if item.status is FeedbackStatus.Open),
        done=sum(1 for item in items if item.status is FeedbackStatus.Done),
        expired=sum(1 for item in items if item.status is FeedbackStatus.Expired),
    )


@router.get("/api/agent/v1", response_model=AgentIndexResponse)
def agent_index() -> AgentIndexResponse:
    return AgentIndexResponse(
        name="Samryetha Feedback Agent API",
        version="v1",
        auth='Header "X-Api-Key: <key>" (or Authorization: Bearer <key>)',
        endpoints={
            "index": AgentEndpointResponse(method="GET", path="/api/agent/v1", auth="none"),
            "readme": AgentEndpointResponse(method="GET", path="/api/agent/v1/README", auth="none"),
            "projects": AgentEndpointResponse(method="GET", path="/api/agent/v1/projects", auth="any key"),
            "tasks": AgentEndpointResponse(method="GET", path="/api/agent/v1/tasks", auth="any key", query="?projectId=&status=&type="),
            "task": AgentEndpointResponse(method="GET", path="/api/agent/v1/tasks/:id", auth="any key"),
            "status": AgentEndpointResponse(method="POST", path="/api/agent/v1/tasks/:id/status", auth="write key", body='{"status":"done"|"open"}'),
        },
    )


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


@router.get("/api/agent/v1/projects", response_model=AgentProjectsResponse)
def agent_projects(request: Request, conn: DbConn) -> AgentProjectsResponse:
    key = _require_agent(request, conn)
    all_projects = _access_projects(conn)
    return AgentProjectsResponse(items=[project for project in all_projects if service.agent_can_access_project(key, project.id)])


@router.get("/api/agent/v1/tasks", response_model=AgentTasksResponse)
def agent_tasks(
    request: Request,
    conn: DbConn,
    projectId: int | None = Query(default=None, ge=1),
    status: Literal["open", "done", "expired"] | None = Query(default=None),
    type: Literal["bug", "suggestion"] | None = Query(default=None),
) -> AgentTasksResponse:
    key = _require_agent(request, conn)
    if projectId is not None and not service.agent_can_access_project(key, projectId):
        raise forbidden("This API key cannot access this project")
    items = service.list_feedback_for_agent(conn, projectId)
    items = [item for item in items if service.agent_can_access_project(key, item.project_id)]
    if status:
        items = [item for item in items if item.status.value == status]
    if type:
        items = [item for item in items if item.type.value == type]
    return AgentTasksResponse(items=items, summary=_summary(items))


@router.get("/api/agent/v1/tasks/{id}", response_model=FeedbackItemResponse)
def agent_task(id: FeedbackId, request: Request, conn: DbConn) -> FeedbackItemResponse:
    key = _require_agent(request, conn)
    item = service.item_by_id(conn, id)
    if item is None:
        raise not_found("Task not found")
    if not service.agent_can_access_project(key, item.project_id):
        raise forbidden("This API key cannot access this project")
    return item


@router.post("/api/agent/v1/tasks/{id}/status", response_model=FeedbackItemResponse)
def agent_set_status(
    id: FeedbackId,
    body: AgentStatusBody,
    request: Request,
    conn: DbConn,
) -> FeedbackItemResponse:
    key = _require_write(request, conn)
    item = service.item_by_id(conn, id)
    if item is None:
        raise not_found("Task not found")
    if not service.agent_can_access_project(key, item.project_id):
        raise forbidden("This API key cannot access this project")
    return service.set_feedback_status(conn, id, body.status)
