"""用户 service — 镜像 backend/src/users/service.ts。

操作对象是 dict 行（users 表列，snake_case）；DTO 输出 camelCase。
时间戳都是毫秒 int。settings 存 JSON TEXT，读时解析、写时 dumps。
"""

from __future__ import annotations

import json
import secrets
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.config import Settings
from ..core.db import now_ms
from ..core.errors import conflict, internal_error, not_found
from ..core.ids import UserID
from ..core.schema import discussions, replies, user_follows, users
from ..core.records import opt_int, opt_str, require_int, require_str
from .models import (
    AccountRole, AccountStatus, ProfilePatch, PublicProfileResponse, ProfileStats,
    UserResponse, UserRow,
)

FAKE_EMAIL_DOMAIN = "samryetha.local"
_settings_adapter = TypeAdapter(dict[str, object])

def user_row_from_mapping(row: RowMapping) -> UserRow:
    return {
        "id": require_int(row["id"], "id"), "username": require_str(row["username"], "username"),
        "email": require_str(row["email"], "email"), "recovery_email": opt_str(row["recovery_email"], "recovery_email"),
        "display_name": require_str(row["display_name"], "display_name"), "bio": require_str(row["bio"], "bio"),
        "profile_moderation_status": require_str(row["profile_moderation_status"], "profile_moderation_status"),
        "pending_display_name": opt_str(row["pending_display_name"], "pending_display_name"),
        "pending_bio": opt_str(row["pending_bio"], "pending_bio"),
        "password_hash": require_str(row["password_hash"], "password_hash"), "role": require_str(row["role"], "role"),
        "status": require_str(row["status"], "status"), "discriminator": opt_int(row["discriminator"], "discriminator"),
        "email_domain": opt_str(row["email_domain"], "email_domain"),
        "email_verified_at": opt_int(row["email_verified_at"], "email_verified_at"),
        "avatar_object_key": opt_str(row["avatar_object_key"], "avatar_object_key"),
        "last_seen_at": opt_int(row["last_seen_at"], "last_seen_at"), "settings": require_str(row["settings"], "settings"),
        "created_at": opt_int(row["created_at"], "created_at"), "updated_at": opt_int(row["updated_at"], "updated_at"),
        "deleted_at": opt_int(row["deleted_at"], "deleted_at"),
    }

def _settings(raw: str) -> dict[str, object]:
    try:
        return _settings_adapter.validate_json(raw or "{}")
    except ValidationError:
        return {}

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

def to_dto(row: UserRow) -> UserResponse:
    return UserResponse(
        id=UserID(row["id"]), username=row["username"], handle=make_handle(row["username"], row["discriminator"]),
        display_name=row["display_name"], email=row["email"], recovery_email=row["recovery_email"],
        role=AccountRole(row["role"]), status=AccountStatus(row["status"]), bio=row["bio"],
        # 有新版资料压着待审（此时 displayName/bio 仍是旧值，见 _stage_and_check_profile）。
        # 只给一个布尔量，待审原文不下发给任何人——失败原文只有管理员能从留存库看到。
        profile_pending=row["profile_moderation_status"] != "approved",
        email_verified=row["email_verified_at"] is not None, avatar_object_key=row["avatar_object_key"],
        settings=_settings(row["settings"]), created_at=row["created_at"], last_seen_at=row["last_seen_at"],
    )

# ---------------------------------------------------------------- queries

def get_by_id(conn: Connection, user_id: int) -> UserRow | None:
    row = conn.execute(
        select(users).where(and_(users.c.id == user_id, users.c.deleted_at.is_(None)))
    ).mappings().first()
    return user_row_from_mapping(row) if row is not None else None

def get_by_username(conn: Connection, username: str) -> UserRow | None:
    row = conn.execute(
        select(users).where(
            and_(users.c.username == normalize_username(username), users.c.deleted_at.is_(None))
        )
    ).mappings().first()
    return user_row_from_mapping(row) if row is not None else None

# ---------------------------------------------------------------- profile ops

def promote_pending_profile(conn: Connection, user_id: int) -> None:
    """把待审资料提升为正式资料（判定放行 / 人工批准）。"""
    from ..core.schema import users

    current = get_by_id(conn, user_id)
    if current is None:
        return
    values: dict[str, object] = {
        "profile_moderation_status": "approved",
        "pending_display_name": None,
        "pending_bio": None,
        "updated_at": max(now_ms(), (current["updated_at"] or 0) + 1),
    }
    if current.get("pending_display_name") is not None:
        values["display_name"] = current["pending_display_name"]
    if current.get("pending_bio") is not None:
        values["bio"] = current["pending_bio"]
    conn.execute(users.update().where(users.c.id == user_id).values(**values))

def _stage_and_check_profile(conn: Connection, settings: Settings | None, user_id: int, patch: ProfilePatch) -> None:
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
    from ..core.schema import users

    current = get_by_id(conn, user_id)
    if current is None:
        return
    pending_display = patch["displayName"] if "displayName" in patch else current["display_name"]
    pending_bio = patch["bio"] if "bio" in patch else current["bio"]
    text = "\n".join(part for part in (pending_display, pending_bio) if part).strip()
    from ..automod import CONTENT_PROFILE, prepare_submission, submit as submit_for_review, supersede_content

    verdict = prepare_submission(
        conn, settings, author_id=user_id, text=text, context="user profile",
    ) if text else None
    changed = conn.execute(
        users.update()
        .where(
            users.c.id == user_id, users.c.updated_at == current["updated_at"],
            users.c.status == current["status"], users.c.role == current["role"],
            users.c.profile_moderation_status == current["profile_moderation_status"],
            users.c.display_name == current["display_name"], users.c.bio == current["bio"],
            users.c.pending_display_name == current["pending_display_name"],
            users.c.pending_bio == current["pending_bio"], users.c.deleted_at.is_(None),
        )
        .values(
            pending_display_name=pending_display,
            pending_bio=pending_bio,
            # 先记待审；判定放行会立刻改回 approved 并提升。
            profile_moderation_status="pending",
            updated_at=max(now_ms(), (current["updated_at"] or 0) + 1),
        )
    )
    if changed.rowcount != 1:
        raise conflict("Profile changed during review; reload and try again")
    if not text:
        promote_pending_profile(conn, user_id)
        return
    if verdict is None:
        raise RuntimeError("profile review did not produce a verdict")

    submit_for_review(
        conn,
        settings,
        content_type=CONTENT_PROFILE,
        content_id=user_id,
        author_id=user_id,
        text=text,
        verdict=verdict,
    )
    if verdict.decision == "allow":
        supersede_content(conn, content_type=CONTENT_PROFILE, content_id=user_id)
        promote_pending_profile(conn, user_id)
        return

    # 非 allow 时用 held_status 统一决定状态：block → rejected、review → pending。
    # 不这样做的话，被机器直接封禁的资料会停在 "pending"，与帖子/回复/私信的
    # "rejected" 语义不一致（虽然对外可见性一样——两者都只看主字段，公开面读不到新版）。
    from ..automod import held_status

    status = held_status(settings, verdict)
    if status is not None:
        conn.execute(
            users.update().where(users.c.id == user_id).values(profile_moderation_status=status)
        )

def update_profile(conn: Connection, user_id: int, patch: ProfilePatch, settings: Settings | None = None) -> UserResponse:
    updates: dict[str, object] = {"updated_at": now_ms()}
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
    settings_patch = patch.get("settings")
    if settings_patch is not None:
        current = get_by_id(conn, user_id)
        merged = _settings(current["settings"] if current is not None else "{}")
        merged.update({k: v for k, v in settings_patch.items() if k != "role_source"})
        updates["settings"] = json.dumps(merged, ensure_ascii=False)
    if "displayName" in patch:
        # 本地改名后展示名不再跟随 IdP：必须在 settings 合并**之后**清标记，
        # 否则 patch 自带的 display_name_source 会把刚清掉的标记又盖回来
        # （见 oidc.maybe_sync_display_name）。patch 里的该键显式丢弃。
        raw = updates.get("settings")
        if raw is None:
            current = get_by_id(conn, user_id)
            merged = _settings(current["settings"] if current is not None else "{}")
        else:
            merged = _settings(require_str(raw, "settings"))
        if merged.pop("display_name_source", None) is not None:
            updates["settings"] = json.dumps(merged, ensure_ascii=False)
    _stage_and_check_profile(conn, settings, user_id, patch)
    if len(updates) > 1:  # 至少 updated_at 之外有字段
        current = get_by_id(conn, user_id)
        updates["updated_at"] = max(now_ms(), ((current["updated_at"] or 0) if current is not None else 0) + 1)
        conn.execute(update(users).where(users.c.id == user_id).values(**updates))
    row = get_by_id(conn, user_id)
    if row is None:
        raise not_found("User not found")
    return to_dto(row)

def get_public_profile(conn: Connection, viewer_id: int | None, username: str) -> PublicProfileResponse:
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
    return PublicProfileResponse(
        id=UserID(uid), username=row["username"], handle=make_handle(row["username"], row["discriminator"]),
        display_name=row["display_name"], bio=row["bio"], avatar_object_key=row["avatar_object_key"],
        joined_at=row["created_at"], last_seen_at=row["last_seen_at"],
        stats=ProfileStats(discussions=int(d_count), replies=int(r_count), followers=int(follower_count), following=int(following_count)),
        is_following=is_following,
    )

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
    primary_key = res.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("user insert did not return a primary key")
    return require_int(primary_key[0], "inserted user id")
