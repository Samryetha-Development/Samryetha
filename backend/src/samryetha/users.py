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
        # 有新版资料压着待审（此时 displayName/bio 仍是旧值，见 _stage_and_check_profile）。
        # 只给一个布尔量，待审原文不下发给任何人——失败原文只有管理员能从留存库看到。
        "profilePending": (row.get("profile_moderation_status") or "approved") != "approved",
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

def _promote_pending_profile(conn: Connection, user_id: int) -> None:
    """把待审资料提升为正式资料（判定放行 / 人工批准）。"""
    from .schema import users

    current = get_by_id(conn, user_id)
    if current is None:
        return
    values: dict = {
        "profile_moderation_status": "approved",
        "pending_display_name": None,
        "pending_bio": None,
        "updated_at": now_ms(),
    }
    if current.get("pending_display_name") is not None:
        values["display_name"] = current["pending_display_name"]
    if current.get("pending_bio") is not None:
        values["bio"] = current["pending_bio"]
    conn.execute(users.update().where(users.c.id == user_id).values(**values))


def _stage_and_check_profile(conn: Connection, settings, user_id: int, patch: dict) -> None:
    """资料文本过审：显示名与简介。

    公测期最常见的滥用就是把引流信息（微信号/QQ/网址）塞进简介——它出现在每个帖子
    旁边，曝光量比正文还高。这里只审文本字段；头像、用户名等不受影响。

    关键设计：**新资料先落 `pending_*`，`display_name`/`bio` 始终是"上一次通过"的值**。
    这样三件事同时成立：

    1. §31「待审期间对外展示旧资料」——资料被标记不会让用户看起来"隐身"；
    2. 「审核失败仅管理员可访问」——被驳回的原文不在主字段里，公开面读不到，
       但仍留在 `pending_*`（没有删除），管理员可从留存库调取；
    3. 判定放行时立刻提升，正常改简介没有额外延迟感。

    关闭总开关时这里是空操作：`update_profile` 已经把主字段直接写掉了（旧行为）。
    """
    if settings is None or not getattr(settings, "automod_enabled", False):
        return
    if "displayName" not in patch and "bio" not in patch:
        return
    from .schema import users

    current = get_by_id(conn, user_id)
    if current is None:
        return
    pending_display = patch["displayName"] if "displayName" in patch else current["display_name"]
    pending_bio = patch["bio"] if "bio" in patch else current["bio"]
    conn.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            pending_display_name=pending_display,
            pending_bio=pending_bio,
            # 先记待审；判定放行会立刻改回 approved 并提升。
            profile_moderation_status="pending",
        )
    )
    text = "\n".join(part for part in (pending_display, pending_bio) if part).strip()
    if not text:
        _promote_pending_profile(conn, user_id)
        return

    from .automod import CONTENT_PROFILE, submit as submit_for_review

    verdict = submit_for_review(
        conn,
        settings,
        content_type=CONTENT_PROFILE,
        content_id=user_id,
        author_id=user_id,
        text=text,
        context="user profile",
    )
    if verdict.decision == "allow":
        _promote_pending_profile(conn, user_id)


def update_profile(conn: Connection, user_id: int, patch: dict, settings=None) -> dict:
    updates: dict = {"updated_at": now_ms()}
    # 开了自动审核时，资料文本不直接写主字段：先进 pending_*，判定放行才提升
    # （见 `_stage_and_check_profile`）。display_name/bio 因此始终是"上一次通过"的值。
    profile_moderated = settings is not None and getattr(settings, "automod_enabled", False)
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
    if "displayName" in patch and not profile_moderated:
        updates["display_name"] = patch["displayName"]
    if "recoveryEmail" in patch:
        updates["recovery_email"] = patch["recoveryEmail"].strip().lower()
    if "bio" in patch and not profile_moderated:
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
        if isinstance(merged, dict):
            # role_source is owned by OIDC/admin flows, never by profile updates.
            merged.update({k: v for k, v in patch["settings"].items() if k != "role_source"})
            updates["settings"] = json.dumps(merged, ensure_ascii=False)
    if "displayName" in patch:
        # 本地改名后展示名不再跟随 IdP：必须在 settings 合并**之后**清标记，
        # 否则 patch 自带的 display_name_source 会把刚清掉的标记又盖回来
        # （见 oidc.maybe_sync_display_name）。patch 里的该键显式丢弃。
        raw = updates.get("settings")
        if raw is None:
            current = get_by_id(conn, user_id) or {}
            try:
                merged = json.loads(current.get("settings") or "{}")
            except (TypeError, ValueError):
                merged = {}
        else:
            try:
                merged = json.loads(raw)
            except (TypeError, ValueError):
                merged = {}
        if isinstance(merged, dict) and merged.pop("display_name_source", None) is not None:
            updates["settings"] = json.dumps(merged, ensure_ascii=False)
    if len(updates) > 1:  # 至少 updated_at 之外有字段
        conn.execute(update(users).where(users.c.id == user_id).values(**updates))
    _stage_and_check_profile(conn, settings, user_id, patch)
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
