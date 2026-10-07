"""Transactional outbox — 镜像 infra/db/client.ts 的 emitEvent + queue。

业务侧在请求事务内调用 :func:`emit_event`，与业务行同事务原子落库（pending）。
S4 的 worker 会消费 pending 事件执行副作用（通知/邮件/SSE）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import BaseModel

from sqlalchemy import insert
from sqlalchemy.engine import Connection

from ..db import now_ms
from ..schema import outbox_events


def emit_event(
    conn: Connection,
    event_type: str,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    payload: BaseModel | Mapping[str, object] | None = None,
) -> None:
    """必须在请求事务/事务连接内调用（与业务行原子提交）。"""
    serialized = (
        payload.model_dump(by_alias=True, mode="json") if isinstance(payload, BaseModel) else dict(payload or {})
    )
    conn.execute(
        insert(outbox_events).values(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=json.dumps(serialized),
            available_at=now_ms(),
            created_at=now_ms(),
        )
    )
