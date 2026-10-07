"""/api/feedback + /api/admin/feedback/* + /api/agent/v1 — 镜像 feedback/routes.ts + agent.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..feedback import backup
from ..feedback.service import FeedbackService
from ..authz import Abilities, AuthorizationService
from ..core.deps import CurrentUser, DbConn, require_active_user
from ..core.errors import auth_required, forbidden
from ..feedback.models import (
    AgentKeyCreatedResponse,
    AgentKeyResponse,
    AgentKeysListResponse,
    AgentEndpointResponse,
    AgentIndexResponse,
    AgentProjectsResponse,
    AgentRole,
    AgentStatusBody,
    AgentTasksResponse,
    BackupCreatedResponse,
    BackupFileResponse,
    BackupsResponse,
    CommentBody,
    CommentPatch,
    CommentResponse,
    CommentsListResponse,
    FeedbackBody,
    FeedbackItemResponse,
    FeedbackListResponse,
    FeedbackPatch,
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
    return MyProjectsResponse(items=FeedbackService(conn).list_my_projects(user.id, user.role == "admin"))


@router.get("/api/feedback", response_model=FeedbackListResponse)
def list_feedback(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    projectId: int = Query(ge=1),
) -> FeedbackListResponse:
    return FeedbackService(conn).list_feedback(user, projectId)


@router.post("/api/feedback", status_code=201, response_model=FeedbackItemResponse)
def create_feedback(
    body: FeedbackBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FeedbackItemResponse:
    return FeedbackService(conn).create_feedback(user, body)


@router.patch("/api/feedback/{id}", response_model=FeedbackItemResponse)
def update_feedback(
    id: FeedbackId,
    body: FeedbackPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FeedbackItemResponse:
    return FeedbackService(conn).update_feedback(user, id, body)


@router.delete("/api/feedback/{id}", response_model=OperationOkResponse)
def delete_feedback(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    FeedbackService(conn).delete_feedback(user, id)
    return OperationOkResponse(ok=True)


@router.post("/api/feedback/{id}/status", response_model=FeedbackItemResponse)
def set_feedback_status(
    id: FeedbackId,
    body: StatusBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> FeedbackItemResponse:
    return FeedbackService(conn).set_feedback_status(user, id, body.status)


@router.get("/api/feedback/{id}/comments", response_model=CommentsListResponse)
def list_comments(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CommentsListResponse:
    return CommentsListResponse(items=FeedbackService(conn).list_comments(user, id))


@router.post("/api/feedback/{id}/comments", status_code=201, response_model=CommentResponse)
def create_comment(
    id: FeedbackId,
    body: CommentBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CommentResponse:
    return FeedbackService(conn).create_comment(user, id, body.body, body.parent_comment_id)


@router.patch("/api/feedback/comments/{comment_id}", response_model=CommentResponse)
def update_comment(
    comment_id: FeedbackId,
    body: CommentPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CommentResponse:
    return FeedbackService(conn).update_comment(user, comment_id, body.body)


@router.delete("/api/feedback/comments/{comment_id}", response_model=OperationOkResponse)
def delete_comment(
    comment_id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    FeedbackService(conn).delete_comment(user, comment_id)
    return OperationOkResponse(ok=True)


# ================================================================ 项目管理(admin)


@router.get("/api/feedback/projects", response_model=ProjectsAdminListResponse)
def list_projects(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> ProjectsAdminListResponse:
    AuthorizationService(conn).assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None)
    return ProjectsAdminListResponse(items=FeedbackService(conn).list_projects_for_admin())


@router.post("/api/feedback/projects", status_code=201, response_model=ProjectAdminResponse)
def create_project(
    body: ProjectBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> ProjectAdminResponse:
    AuthorizationService(conn).assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None)
    return FeedbackService(conn).create_project(user.id, body.name, body.description)


@router.patch("/api/feedback/projects/{id}", response_model=OperationOkResponse)
def update_project(
    id: FeedbackId,
    body: ProjectPatch,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    AuthorizationService(conn).assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None)
    FeedbackService(conn).update_project(id, body)
    return OperationOkResponse(ok=True)


@router.delete("/api/feedback/projects/{id}", response_model=OperationOkResponse)
def delete_project(
    id: FeedbackId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    AuthorizationService(conn).assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None)
    FeedbackService(conn).delete_project(user.id, id)
    return OperationOkResponse(ok=True)


@router.put("/api/feedback/projects/{id}/members", response_model=OperationOkResponse)
def set_project_members(
    id: FeedbackId,
    body: MembersBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    AuthorizationService(conn).assert_can(user, Abilities.FEEDBACK_PROJECT_MANAGE, None)
    FeedbackService(conn).set_project_members(id, body.members)
    return OperationOkResponse(ok=True)


# ================================================================ 备份 (admin)


def _require_admin_view(request: Request, user: CurrentUser) -> None:
    db = request.app.state.db
    with db.request_conn() as conn:
        AuthorizationService(conn).assert_can(user, Abilities.ADMIN_VIEW, None)


@router.get("/api/admin/feedback/backups", response_model=BackupsResponse)
def list_backups(request: Request, user: CurrentUser = Depends(require_active_user)) -> BackupsResponse:
    _require_admin_view(request, user)
    return backup.BackupService(request.app.state.db).overview()


@router.post("/api/admin/feedback/backups/create", response_model=BackupCreatedResponse)
def create_backup(request: Request, user: CurrentUser = Depends(require_active_user)) -> BackupCreatedResponse:
    _require_admin_view(request, user)
    return BackupCreatedResponse(
        backup=BackupFileResponse.model_validate(backup.BackupService(request.app.state.db).create_backup())
    )


@router.post("/api/admin/feedback/backups/restore", response_model=RestartRequiredResponse)
def restore_backup(
    body: RestoreBackupBody,
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> RestartRequiredResponse:
    _require_admin_view(request, user)
    backup.BackupService(request.app.state.db).restore_backup(body.name)
    return RestartRequiredResponse(ok=True, restart_required=True)


@router.put("/api/admin/feedback/backups/settings", response_model=OperationOkResponse)
def set_backup_settings(
    body: BackupSettingsBody,
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    _require_admin_view(request, user)
    backup.BackupService(
        request.app.state.db, scheduler=getattr(request.app.state, "backup_scheduler", None)
    ).configure_schedule(body.backupCron, body.backupKeep)
    return OperationOkResponse(ok=True)


# ================================================================ Agent 密钥 (admin 鉴权)


# 密钥管理属敏感操作：必须登录且具备 admin 权限（镜像 feedback/agent.ts 的 requireAdmin）
def _require_admin(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> CurrentUser:
    AuthorizationService(conn).assert_can(user, Abilities.ADMIN_VIEW, None)
    return user


@router.get("/api/admin/feedback/keys", response_model=AgentKeysListResponse)
def list_keys(conn: DbConn, _user: CurrentUser = Depends(_require_admin)) -> AgentKeysListResponse:
    return AgentKeysListResponse(items=FeedbackService(conn).list_keys())


@router.post("/api/admin/feedback/keys", status_code=201, response_model=AgentKeyCreatedResponse)
def create_key(
    body: KeyBody,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> AgentKeyCreatedResponse:
    key, key_row = FeedbackService(conn).create_key(body.name, body.role, body.project_ids)
    return AgentKeyCreatedResponse(key=key, key_row=key_row)


@router.put("/api/admin/feedback/keys/{id}", response_model=OperationOkResponse)
def set_key_enabled(
    id: FeedbackId,
    body: KeyEnabledBody,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> OperationOkResponse:
    FeedbackService(conn).set_key_enabled(id, body.enabled)
    return OperationOkResponse(ok=True)


@router.delete("/api/admin/feedback/keys/{id}", response_model=OperationOkResponse)
def delete_key(
    id: FeedbackId,
    conn: DbConn,
    _user: CurrentUser = Depends(_require_admin),
) -> OperationOkResponse:
    FeedbackService(conn).delete_key(id)
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
    key = FeedbackService(conn).verify_key(_extract_key(request))
    if key is None:
        raise auth_required("Invalid or missing API key")
    return key


def _require_write(request: Request, conn: DbConn) -> AgentKeyResponse:
    key = _require_agent(request, conn)
    if key.role is not AgentRole.Write:
        raise forbidden("This API key is read-only")
    return key


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
            "tasks": AgentEndpointResponse(
                method="GET", path="/api/agent/v1/tasks", auth="any key", query="?projectId=&status=&type="
            ),
            "task": AgentEndpointResponse(method="GET", path="/api/agent/v1/tasks/:id", auth="any key"),
            "status": AgentEndpointResponse(
                method="POST", path="/api/agent/v1/tasks/:id/status", auth="write key", body='{"status":"done"|"open"}'
            ),
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
            'POST /api/agent/v1/tasks/:id/status  {status: "done"|"open"}（需 write 权限）',
            "",
            "鉴权头：X-Api-Key: <key>",
        ]
    )


@router.get("/api/agent/v1/projects", response_model=AgentProjectsResponse)
def agent_projects(request: Request, conn: DbConn) -> AgentProjectsResponse:
    key = _require_agent(request, conn)
    return FeedbackService(conn).agent_projects(key)


@router.get("/api/agent/v1/tasks", response_model=AgentTasksResponse)
def agent_tasks(
    request: Request,
    conn: DbConn,
    projectId: int | None = Query(default=None, ge=1),
    status: Literal["open", "done", "expired"] | None = Query(default=None),
    type: Literal["bug", "suggestion"] | None = Query(default=None),
) -> AgentTasksResponse:
    key = _require_agent(request, conn)
    return FeedbackService(conn).agent_tasks(key, project_id=projectId, status=status, type_=type)


@router.get("/api/agent/v1/tasks/{id}", response_model=FeedbackItemResponse)
def agent_task(id: FeedbackId, request: Request, conn: DbConn) -> FeedbackItemResponse:
    key = _require_agent(request, conn)
    return FeedbackService(conn).agent_task(key, id)


@router.post("/api/agent/v1/tasks/{id}/status", response_model=FeedbackItemResponse)
def agent_set_status(
    id: FeedbackId,
    body: AgentStatusBody,
    request: Request,
    conn: DbConn,
) -> FeedbackItemResponse:
    key = _require_write(request, conn)
    return FeedbackService(conn).agent_set_status(key, id, body.status)
