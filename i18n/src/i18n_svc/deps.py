"""请求作用域依赖：DB 连接、auth 连接、用户注入、鉴权守卫。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Iterator

from fastapi import Depends, Request
from sqlalchemy.engine import Connection

from .db import AuthDatabase, Database
from .errors import auth_required, forbidden
from .security import SESSION_COOKIE, get_session_user


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str
    email: str
    role: str
    status: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_db(request: Request) -> Iterator[Connection]:
    db: Database = request.app.state.db
    with db.request_conn() as conn:
        yield conn


def get_auth_db(request: Request) -> Iterator[Connection]:
    auth_db: AuthDatabase = request.app.state.auth_db
    with auth_db.request_conn() as conn:
        yield conn


def get_current_user(
    request: Request,
    auth_conn: Annotated[Connection, Depends(get_auth_db)],
) -> CurrentUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = get_session_user(auth_conn, token)
    if row is None:
        return None
    return CurrentUser(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        email=row["email"],
        role=row["role"],
        status=row["status"],
    )


def require_user(user: Annotated[CurrentUser | None, Depends(get_current_user)]) -> CurrentUser:
    if user is None:
        raise auth_required()
    return user


def require_active_user(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
    if user.status not in ("active",):
        raise forbidden("Your account is not active")
    return user


def require_admin(user: Annotated[CurrentUser, Depends(require_active_user)]) -> CurrentUser:
    if not user.is_admin:
        raise forbidden("Admin only")
    return user


# 便捷别名
DbConn = Annotated[Connection, Depends(get_db)]
AuthConn = Annotated[Connection, Depends(get_auth_db)]
CurrentUserDep = Annotated[CurrentUser | None, Depends(get_current_user)]
ActiveUser = Annotated[CurrentUser, Depends(require_active_user)]
AdminUser = Annotated[CurrentUser, Depends(require_admin)]
