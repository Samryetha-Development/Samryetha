"""用户 service — 镜像 backend/src/users/service.ts。

操作对象是 dict 行（users 表列，snake_case）；DTO 输出 camelCase。
时间戳都是毫秒 int。settings 存 JSON TEXT，读时解析、写时 dumps。
"""

from __future__ import annotations

import json
import secrets

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection

from .db import now_ms
from .errors import conflict, internal_error, not_found
from .schema import discussions, replies, user_follows, users

# 内测期无真实邮箱：注册生成假邮箱（username 唯一 → 邮箱唯一）
FAKE_EMAIL_DOMAIN = "samryetha.local"

# ---------------------------------------------------------------- helpers

def normalize_username(username: str) -> str:
    return username.strip().lower().lstrip("@")


def make_handle(username: str, discriminator: int | None) -> str:
    return f"{username}#{discriminator}" if discriminator else username


def next_discriminator(conn: Connection) -> int:
    for _ in range(50):
        # 密码学安全随机（镜像 users/service.ts 的 randomInt），避免可预测身份号
        candidate = 1000 + secrets.randbelow(9000)
        row = conn.execute(
            select(users.c.id).where(users.c.discriminator == candidate)
        ).first()
        if row is None:
            return candidate
    raise internal_error()


def to_dto(row: dict) -> dict:
    settings = {}
    try:
        if row.get("settings"):
            settings = json.loads(row["settings"])
    except (TypeError, ValueError):
        settings = {}
    return {
        "id": row["id"],
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
        "email": row["email"],
        "recoveryEmail": row.get("recovery_email"),
        "role": row["role"],
        "status": row["status"],
        "bio": row["bio"],
        "emailVerified": row.get("email_verified_at") is not None,
        "avatarObjectKey": row["avatar_object_key"],
        "settings": settings,
        "createdAt": row["created_at"],
        "lastSeenAt": row["last_seen_at"],
    }


# ---------------------------------------------------------------- queries

def get_by_id(conn: Connection, user_id: int) -> dict | None:
    row = conn.execute(
        select(users).where(and_(users.c.id == user_id, users.c.deleted_at.is_(None)))
    ).first()
    return dict(row._mapping) if row else None


def get_by_username(conn: Connection, username: str) -> dict | None:
    row = conn.execute(
        select(users).where(
            and_(users.c.username == normalize_username(username), users.c.deleted_at.is_(None))
        )
    ).first()
    return dict(row._mapping) if row else None


# ---------------------------------------------------------------- profile ops

def update_profile(conn: Connection, user_id: int, patch: dict) -> dict:
    updates: dict = {"updated_at": now_ms()}
    if "username" in patch:
        wanted = normalize_username(patch["username"])
        dup = conn.execute(
            select(users.c.id).where(
                and_(users.c.username == wanted, users.c.id != user_id)
            )
        ).first()
        if dup:
            raise conflict("That username is already taken")
        patch["username"] = wanted
        updates["username"] = wanted
    if "displayName" in patch:
        updates["display_name"] = patch["displayName"]
    if "recoveryEmail" in patch:
        updates["recovery_email"] = patch["recoveryEmail"].strip().lower()
    if "bio" in patch:
        updates["bio"] = patch["bio"]
    if "avatarObjectKey" in patch:
        updates["avatar_object_key"] = patch["avatarObjectKey"]
    if patch.get("settings"):
        current = get_by_id(conn, user_id) or {}
        merged = {}
        try:
            merged = json.loads(current.get("settings") or "{}")
        except (TypeError, ValueError):
            merged = {}
        merged.update(patch["settings"])
        updates["settings"] = json.dumps(merged, ensure_ascii=False)
    if len(updates) > 1:  # 至少 updated_at 之外有字段
        conn.execute(update(users).where(users.c.id == user_id).values(**updates))
    row = get_by_id(conn, user_id)
    if row is None:
        raise not_found("User not found")
    return to_dto(row)


def get_public_profile(conn: Connection, viewer_id: int | None, username: str) -> dict:
    row = get_by_username(conn, username)
    if row is None:
        raise not_found("User not found")
    uid = row["id"]
    d_count = conn.execute(
        select(func.count()).select_from(discussions).where(
            and_(discussions.c.author_id == uid, discussions.c.deleted_at.is_(None))
        )
    ).scalar() or 0
    r_count = conn.execute(
        select(func.count()).select_from(replies).where(
            and_(replies.c.author_id == uid, replies.c.deleted_at.is_(None))
        )
    ).scalar() or 0
    follower_count = conn.execute(
        select(func.count()).select_from(user_follows).where(user_follows.c.followee_id == uid)
    ).scalar() or 0
    following_count = conn.execute(
        select(func.count()).select_from(user_follows).where(user_follows.c.follower_id == uid)
    ).scalar() or 0
    is_following = False
    if viewer_id is not None:
        follows_row = conn.execute(
            select(user_follows.c.followee_id).where(
                and_(
                    user_follows.c.follower_id == viewer_id,
                    user_follows.c.followee_id == uid,
                )
            )
        ).first()
        is_following = follows_row is not None
    return {
        "id": uid,
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
        "bio": row["bio"],
        "avatarObjectKey": row["avatar_object_key"],
        "joinedAt": row["created_at"],
        "lastSeenAt": row["last_seen_at"],
        "stats": {
            "discussions": d_count,
            "replies": r_count,
            "followers": follower_count,
            "following": following_count,
        },
        "isFollowing": is_following,
    }


def register_user_row(conn: Connection, username: str, display_name: str, password_hash: str) -> int:
    """建行并返回 id。注册默认 status=pending。"""
    disc = next_discriminator(conn)
    email = f"{username}@{FAKE_EMAIL_DOMAIN}"
    _now = now_ms()
    res = conn.execute(
        users.insert().values(
            username=username,
            display_name=display_name,
            email=email,
            email_domain=FAKE_EMAIL_DOMAIN,
            password_hash=password_hash,
            discriminator=disc,
            status="pending",
            settings="{}",
            bio="",
            created_at=_now,
            updated_at=_now,
        )
    )
    return res.inserted_primary_key[0]
