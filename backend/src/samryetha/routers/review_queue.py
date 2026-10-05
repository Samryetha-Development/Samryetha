"""`/api/admin/moderation` — 自动审核队列的人工处置接口。

放在 /api/admin 下（require_moderator）而不是 /api/moderation：队列里可能有待审的
私信与用户资料，能看这些的只有版主/管理员，而 /api/moderation 目前是"MVP 版主面板"，
两者权限等级一致但语义不同——审核队列属于后台，不该出现在普通用户也能命中的前缀下。

**权限分两层**（用户要求"审核失败的记录只有管理员可访问"）：

| 接口 | 权限 | 理由 |
|---|---|---|
| `GET /queue` | moderator/admin | 版主要能干活：窗口内定案、追认/推翻 AI |
| `POST /queue/{id}/approve` 与 `/reject` | moderator/admin | 同上；推翻 AI 就是在这里发生 |
| `POST /finalize` | admin | 手动催一轮逾期复审（运维/排障） |
| `GET /retained` | **admin** | 审核失败内容的**全文**留存库，含违禁原文，不下放给版主 |
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import review_queue as service
from ..automod import finalize_pending
from ..deps import DbConn, get_settings_dep, require_admin, require_moderator

router = APIRouter()

QueueId = Annotated[int, Path(ge=1)]

ContentType = Literal["discussion", "reply", "profile", "message", "attachment"]


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note: str | None = Field(default=None, max_length=1000)


@router.get("/api/admin/moderation/queue")
def list_queue(
    conn: DbConn,
    _settings=Depends(get_settings_dep),
    _mod=Depends(require_moderator),
    status: Literal["pending", "approved", "rejected", "all"] = Query(default="pending"),
    type: ContentType | None = Query(default=None),
    # awaiting = 还在确认窗口内；published_by_ai = AI 复审已先行公开、等追认；
    # blocked = 已封禁（含 AI 先行封禁与人工驳回）。
    resolution: Literal["awaiting", "published_by_ai", "published_by_human", "blocked"] | None = Query(
        default=None
    ),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return service.list_queue(
        conn, viewer=_mod, status=status, content_type=type, resolution=resolution, cursor=cursor, limit=limit
    )


@router.post("/api/admin/moderation/queue/{queue_id}/approve")
def approve(
    queue_id: QueueId,
    body: DecideBody,
    conn: DbConn,
    settings=Depends(get_settings_dep),
    mod=Depends(require_moderator),
) -> dict:
    """放行。对 AI 先行封禁的记录来说，这一步就是"推翻 AI、重新放行"。"""
    result = service.decide(conn, mod, queue_id, approve=True, note=body.note)
    row = result.pop("_row")
    should_notify = result.pop("_notify", True)
    if should_notify and settings.automod_notify_author:
        service.notify_author(conn, row, approved=True, note=body.note)
    return result


@router.post("/api/admin/moderation/queue/{queue_id}/reject")
def reject(
    queue_id: QueueId,
    body: DecideBody,
    conn: DbConn,
    settings=Depends(get_settings_dep),
    mod=Depends(require_moderator),
) -> dict:
    """封禁。对 AI 已先行公开的记录来说，这一步就是"推翻 AI、改为封禁"。"""
    result = service.decide(conn, mod, queue_id, approve=False, note=body.note)
    row = result.pop("_row")
    should_notify = result.pop("_notify", True)
    if should_notify and settings.automod_notify_author:
        service.notify_author(conn, row, approved=False, note=body.note)
    return result


@router.post("/api/admin/moderation/finalize")
def finalize_now(
    conn: DbConn,
    settings=Depends(get_settings_dep),
    _admin=Depends(require_admin),
) -> dict:
    """立刻跑一轮"逾期未确认 → AI 复审落定"。

    生产环境有 `ModerationWorker` 定时跑，这个接口是给运维/排障用的：部署完不必等
    轮询间隔，也能确认开关与模型是否真的通了。
    """
    finalized = finalize_pending(conn, settings)
    return {
        "ok": True,
        "count": len(finalized),
        "published": sum(1 for item in finalized if item.get("published")),
        "blocked": sum(1 for item in finalized if not item.get("published")),
        "items": [
            {
                "id": item["id"],
                "contentType": item["contentType"],
                "contentId": item["contentId"],
                "resolution": item["resolution"],
                "published": item["published"],
            }
            for item in finalized
        ],
    }


@router.get("/api/admin/moderation/retained")
def list_retained(
    conn: DbConn,
    _settings=Depends(get_settings_dep),
    _admin=Depends(require_admin),
    type: ContentType | None = Query(default=None),
    cursor: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    """审核失败内容的留存库（含正文全文）。

    只给管理员：这些是被封禁的原文（可能包含血腥、色情、人身攻击内容），
    内容本身从未删除，但除管理员外任何角色（含版主与作者）都看不到。
    """
    return service.list_retained(conn, content_type=type, cursor=cursor, limit=limit)
