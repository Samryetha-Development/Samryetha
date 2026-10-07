"""用户 service — 镜像 backend/src/users/service.ts。

操作对象是 dict 行（users 表列，snake_case）；DTO 输出 camelCase。
时间戳都是毫秒 int。settings 存 JSON TEXT，读时解析、写时 dumps。
"""

from __future__ import annotations

from samryetha.users.repository import UserRepository

import json
import secrets
from typing import TYPE_CHECKING
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.engine import Connection

from ..core.config import Settings
from ..core.db import now_ms
from ..core.errors import conflict, internal_error, not_found
from ..core.ids import UserID
from ..core.records import require_str
from .models import (
    AccountRole,
    AccountStatus,
    ProfilePatch,
    PublicProfileResponse,
    ProfileStats,
    UserResponse,
    UserRow,
)

if TYPE_CHECKING:
    from ..authz import Actor
    from ..discussions.models import AuthoredReplyListResponse, DiscussionListResponse, PageQuery

FAKE_EMAIL_DOMAIN = "samryetha.local"
_settings_adapter = TypeAdapter(dict[str, object])


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


def to_dto(row: UserRow) -> UserResponse:
    return UserResponse(
        id=UserID(row["id"]),
        username=row["username"],
        handle=make_handle(row["username"], row["discriminator"]),
        display_name=row["display_name"],
        email=row["email"],
        recovery_email=row["recovery_email"],
        role=AccountRole(row["role"]),
        status=AccountStatus(row["status"]),
        bio=row["bio"],
        # 有新版资料压着待审（此时 displayName/bio 仍是旧值，见 _stage_and_check_profile）。
        # 只给一个布尔量，待审原文不下发给任何人——失败原文只有管理员能从留存库看到。
        profile_pending=row["profile_moderation_status"] != "approved",
        email_verified=row["email_verified_at"] is not None,
        avatar_object_key=row["avatar_object_key"],
        settings=_settings(row["settings"]),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


# ---------------------------------------------------------------- queries


# ---------------------------------------------------------------- profile ops


class UserService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, settings: Settings | None = None) -> None:
        self._conn = conn
        self._settings = settings
        self._repository = UserRepository(self._conn)

    def next_discriminator(self) -> int:
        for _ in range(50):
            # 密码学安全随机（镜像 users/service.ts 的 randomInt），避免可预测身份号
            candidate = 1000 + secrets.randbelow(9000)
            if not self._repository.discriminator_exists(candidate):
                return candidate
        raise internal_error()

    def get_by_id(self, user_id: int) -> UserRow | None:
        return self._repository.get_by_id(UserID(user_id))

    def get_by_username(self, username: str) -> UserRow | None:
        return self._repository.get_by_username(normalize_username(username))

    def _require_user_id(self, username: str) -> UserID:
        user = self.get_by_username(username)
        if user is None:
            raise not_found("User not found")
        return UserID(user["id"])

    def posts(self, viewer: Actor | None, username: str, query: PageQuery) -> DiscussionListResponse:
        from ..discussions import DiscussionService

        return DiscussionService(self._conn).list_by_author(viewer, self._require_user_id(username), query)

    def replies(self, viewer: Actor | None, username: str, query: PageQuery) -> AuthoredReplyListResponse:
        from ..discussions import DiscussionService

        return DiscussionService(self._conn).list_replies_by_author(viewer, self._require_user_id(username), query)

    def saved(self, viewer: Actor | None, username: str, query: PageQuery) -> DiscussionListResponse:
        from ..discussions import DiscussionService

        return DiscussionService(self._conn).list_saved(viewer, self._require_user_id(username), query)

    def promote_pending_profile(self, user_id: int) -> None:
        """把待审资料提升为正式资料（判定放行 / 人工批准）。"""
        current = self.get_by_id(user_id)
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
        self._repository.update_user(UserID(user_id), values)

    def _stage_and_check_profile(self, user_id: int, patch: ProfilePatch) -> None:
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
        settings = self._settings
        if settings is None or not getattr(settings, "automod_enabled", False):
            return
        if "displayName" not in patch and "bio" not in patch:
            return
        current = self.get_by_id(user_id)
        if current is None:
            return
        pending_display = patch["displayName"] if "displayName" in patch else current["display_name"]
        pending_bio = patch["bio"] if "bio" in patch else current["bio"]
        text = "\n".join(part for part in (pending_display, pending_bio) if part).strip()
        from ..automod import AutomodService, CONTENT_PROFILE

        automod = AutomodService(self._conn, settings)
        verdict = (
            automod.prepare_submission(
                author_id=user_id,
                text=text,
                context="user profile",
            )
            if text
            else None
        )
        changed = self._repository.stage_profile(
            UserID(user_id),
            current,
            pending_display_name=pending_display,
            pending_bio=pending_bio,
            updated_at=max(now_ms(), (current["updated_at"] or 0) + 1),
        )
        if not changed:
            raise conflict("Profile changed during review; reload and try again")
        if not text:
            self.promote_pending_profile(user_id)
            return
        if verdict is None:
            raise RuntimeError("profile review did not produce a verdict")

        automod.submit(
            content_type=CONTENT_PROFILE,
            content_id=user_id,
            author_id=user_id,
            text=text,
            verdict=verdict,
        )
        if verdict.decision == "allow":
            automod.supersede_content(content_type=CONTENT_PROFILE, content_id=user_id)
            self.promote_pending_profile(user_id)
            return

        # 非 allow 时用 held_status 统一决定状态：block → rejected、review → pending。
        # 不这样做的话，被机器直接封禁的资料会停在 "pending"，与帖子/回复/私信的
        # "rejected" 语义不一致（虽然对外可见性一样——两者都只看主字段，公开面读不到新版）。
        from ..automod import held_status

        status = held_status(settings, verdict)
        if status is not None:
            self._repository.update_user(UserID(user_id), {"profile_moderation_status": status})

    def update_profile(self, user_id: int, patch: ProfilePatch) -> UserResponse:
        settings = self._settings
        updates: dict[str, object] = {"updated_at": now_ms()}
        # 开了自动审核时，资料文本不直接写主字段：先进 pending_*，判定放行才提升
        # （见 `_stage_and_check_profile`）。display_name/bio 因此始终是"上一次通过"的值。
        profile_moderated = settings is not None and getattr(settings, "automod_enabled", False)
        if "username" in patch:
            wanted = normalize_username(patch["username"])
            if self._repository.username_exists_except(wanted, UserID(user_id)):
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
            current = self.get_by_id(user_id)
            merged = _settings(current["settings"] if current is not None else "{}")
            merged.update({k: v for k, v in settings_patch.items() if k != "role_source"})
            updates["settings"] = json.dumps(merged, ensure_ascii=False)
        if "displayName" in patch:
            # 本地改名后展示名不再跟随 IdP：必须在 settings 合并**之后**清标记，
            # 否则 patch 自带的 display_name_source 会把刚清掉的标记又盖回来
            # （见 oidc.maybe_sync_display_name）。patch 里的该键显式丢弃。
            raw = updates.get("settings")
            if raw is None:
                current = self.get_by_id(user_id)
                merged = _settings(current["settings"] if current is not None else "{}")
            else:
                merged = _settings(require_str(raw, "settings"))
            if merged.pop("display_name_source", None) is not None:
                updates["settings"] = json.dumps(merged, ensure_ascii=False)
        self._stage_and_check_profile(user_id, patch)
        if len(updates) > 1:  # 至少 updated_at 之外有字段
            current = self.get_by_id(user_id)
            updates["updated_at"] = max(now_ms(), ((current["updated_at"] or 0) if current is not None else 0) + 1)
            self._repository.update_user(UserID(user_id), updates)
        row = self.get_by_id(user_id)
        if row is None:
            raise not_found("User not found")
        return to_dto(row)

    def get_public_profile(self, viewer_id: int | None, username: str) -> PublicProfileResponse:
        row = self.get_by_username(username)
        if row is None:
            raise not_found("User not found")
        uid = row["id"]
        stats = self._repository.profile_stats(UserID(uid))
        is_following = viewer_id is not None and self._repository.is_following(UserID(viewer_id), UserID(uid))
        return PublicProfileResponse(
            id=UserID(uid),
            username=row["username"],
            handle=make_handle(row["username"], row["discriminator"]),
            display_name=row["display_name"],
            bio=row["bio"],
            avatar_object_key=row["avatar_object_key"],
            joined_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            stats=ProfileStats(
                discussions=stats.discussions,
                replies=stats.replies,
                followers=stats.followers,
                following=stats.following,
            ),
            is_following=is_following,
        )

    def register_user_row(self, username: str, display_name: str, password_hash: str) -> int:
        """建行并返回 id。注册默认 status=pending。"""
        disc = self.next_discriminator()
        email = f"{username}@{FAKE_EMAIL_DOMAIN}"
        _now = now_ms()
        return self._repository.insert_user(
            username=username,
            display_name=display_name,
            email=email,
            email_domain=FAKE_EMAIL_DOMAIN,
            password_hash=password_hash,
            discriminator=disc,
            created_at=_now,
        )
