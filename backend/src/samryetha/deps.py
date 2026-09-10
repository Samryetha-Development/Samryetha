"""请求作用域依赖：DB 连接(单事务)、会话用户注入、鉴权守卫。

镜像 backend/src/app/auth-hook.ts：全局把 cookie 解析成 currentUser，路由用 require_* 取用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Iterator

from fastapi import Depends, Request
from sqlalchemy.engine import Connection

from . import moderation
from .db import Database
from .errors import auth_required, banned, forbidden
from .schema import users
from .security import SESSION_COOKIE, get_session_user


@dataclass
class CurrentUser:
    """与 TS SessionUser 对齐（camelCase 字段）。"""

    id: int
    username: str
    display_name: str
    email: str
    role: str
    status: str


def to_session_user(row: dict) -> CurrentUser:
    return CurrentUser(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        email=row["email"],
        role=row["role"],
        status=row["status"],
    )


def get_db(request: Request) -> Iterator[Connection]:
    db: Database = request.app.state.db
    with db.request_conn() as conn:
        yield conn


def get_storage(request: Request):
    return request.app.state.storage


def get_current_user(request: Request, conn: Annotated[Connection, Depends(get_db)]) -> CurrentUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = get_session_user(conn, token)
    if row is None:
        return None
    # 临时封禁到期 → 自动解封(防御面：封禁会删会话，正常路径走 login 已处理)
    if row["status"] == "banned" and moderation.lift_ban_if_expired(conn, row["id"]):
        row["status"] = "active"
    return to_session_user(row)


def require_user(user: Annotated[CurrentUser | None, Depends(get_current_user)]) -> CurrentUser:
    if user is None:
        raise auth_required()
    return user


def require_active_user(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
    if user.status == "banned":
        raise banned()
    if user.status != "active":
        raise forbidden("Your account is not active")
    return user


# 便捷别名：路由直接用 DbConn / CurrentUserDep
DbConn = Annotated[Connection, Depends(get_db)]
CurrentUserDep = Annotated[CurrentUser | None, Depends(get_current_user)]


def users_row_columns():
    return [c for c in users.c]
