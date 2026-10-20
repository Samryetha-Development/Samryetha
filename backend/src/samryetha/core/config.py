"""Environment configuration.

Mirrors backend/src/config/env.ts key-for-key so the TS ``.env`` can be reused.
Timestamp/cursor units: epoch MILLISECONDS as integers (same as the TS/DB layer).
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict



logger = logging.getLogger("samryetha.config")


def default_data_dir() -> str:
    """可写数据（上传 + SQLite）的默认根目录，固定落在**仓库之外**。
    The default root for mutable data (uploads + SQLite), always OUTSIDE the repo.

    上传文件与数据库绝不能放进仓库目录：任何 `git clean -fdx` / `git reset --hard` /
    重新 clone / 用压缩包覆盖 的更新流程，都会把它们当未跟踪文件一并删掉。默认取
    各 OS 的数据目录，可用环境变量 `SAMRYETHA_DATA_DIR` 覆盖。
    Uploads and the database must never live inside the repo: any update flow that runs
    `git clean -fdx` / `git reset --hard` / a fresh clone / an archive overwrite deletes
    them as untracked files. The default is a per-OS data directory; override it with the
    SAMRYETHA_DATA_DIR environment variable.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Samryetha")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Samryetha")
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "samryetha")
    return os.path.expanduser("~/.local/share/samryetha")


def find_git_root(start: str) -> str | None:
    """从 start 向上查找最近的含 .git 的目录；不在任何仓库内则返回 None。
    Walk up from start to the nearest directory containing .git; return None when
    start is not inside any git work tree.
    """
    cur = os.path.abspath(os.path.expanduser(start))
    while True:
        marker = os.path.join(cur, ".git")
        if os.path.isdir(marker) or os.path.isfile(marker):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _warn_if_inside_repo(name: str, path: str) -> None:
    """若 path 解析到某个 git 仓库内，打显著告警：更新流程可能把它清空。
    Emit a prominent warning when path resolves inside a git work tree, because an
    update flow (git clean/reset) could wipe it.
    """
    git_root = find_git_root(path)
    if git_root is not None:
        logger.warning(
            "%s (%s) 位于 git 仓库 (%s) 内：git clean/reset 等更新流程可能删除上传文件与数据库，"
            "请改用仓库之外的绝对路径",
            name,
            path,
            git_root,
        )


def _is_file_path(url: str) -> bool:
    """判断 database_url 是否按文件路径处理（排除 :memory: 与 postgres:// 等连接串）。
    Whether database_url is treated as a file path (excludes :memory: and postgres:// URLs).
    """
    return url != ":memory:" and "://" not in url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    node_env: str = "development"  # NODE_ENV
    port: int = 3001  # PORT
    app_origin: str = "http://localhost:3000"  # APP_ORIGIN
    # 留空则由 `_resolve_data_paths` 派生为 {samryetha_data_dir}/app.db（仓库之外）。
    # When empty, `_resolve_data_paths` derives it as {samryetha_data_dir}/app.db (outside the repo).
    database_url: str = ""  # DATABASE_URL
    cookie_secure: bool = False  # COOKIE_SECURE ("true"/"false"/"1"/"0")
    cookie_domain: str = ""  # COOKIE_DOMAIN：跨子域共享会话时设为 .samryetha.com；留空 = host-only
    # TRUST_PROXY：是否信任反向代理的 X-Forwarded-For（直连公网保持 false）。
    # 布尔开关有意保持：未来升级为 CIDR allowlist（仅受控网段可信），当前部署先维持现状。
    trust_proxy: bool = False  # TRUST_PROXY
    # 会话绝对有效期：7 天（原 30 天）。缩短令牌被窃后的可用窗口；无空闲过期时 7 天是更稳的默认。
    # Session absolute TTL: 7 days (was 30). Narrows the window after token theft; 7d is safer without idle expiry.
    session_ttl_ms: int = 7 * 24 * 3600 * 1000  # SESSION_TTL_MS
    qr_login_ttl_ms: int = 2 * 60 * 1000  # QR_LOGIN_TTL_MS（扫码票据有效期，PC 等待上限）
    # 密码链路总开关：True 则注册/密码登录/改密/找回全部 410，仅 OAuth（含认领）可用。
    # Password auth kill-switch: True retires register/password-login/change/forgot/reset.
    password_auth_disabled: bool = False  # PASSWORD_AUTH_DISABLED
    # IdP 故障时的 admin 紧急入口令牌（POST /api/auth/emergency-login 用户名+令牌直接建会话，
    # 仅 admin 生效）。为空则该入口关闭。unset = emergency login disabled.
    emergency_login_token: str | None = None  # EMERGENCY_LOGIN_TOKEN
    allowed_email_domains: str = "example.edu.cn"  # ALLOWED_EMAIL_DOMAINS
    storage_secret: str = "dev-storage-secret-change-me"  # STORAGE_SECRET
    # 可写数据根目录：默认在仓库之外（见 default_data_dir）。upload_dir 与 database_url
    # 留空时都从它派生；显式 UPLOAD_DIR / DATABASE_URL 仍可覆盖（向后兼容）。
    # Mutable-data root: defaults outside the repo (see default_data_dir). upload_dir and
    # database_url derive from it when left empty; explicit UPLOAD_DIR / DATABASE_URL win.
    samryetha_data_dir: str = Field(default_factory=default_data_dir)  # SAMRYETHA_DATA_DIR
    upload_dir: str = ""  # UPLOAD_DIR（留空则 = {samryetha_data_dir}/uploads）
    admin_password: str = "SamryethaAdmin@NeatAvocado2026!"  # ADMIN_PASSWORD
    dev_password: str = "NeatAvocadoOnTop2026"  # DEV_PASSWORD
    smtp_url: str | None = None  # SMTP_URL
    smtp_from: str = "Samryetha <no-reply@samryetha.local>"  # SMTP_FROM
    outbox_poll_interval_ms: int = 500  # OUTBOX_POLL_INTERVAL_MS
    oidc_issuer: str | None = None  # OIDC_ISSUER
    oidc_client_id: str | None = None  # OIDC_CLIENT_ID
    oidc_client_secret: str | None = None  # OIDC_CLIENT_SECRET
    oidc_redirect_uri: str | None = None  # OIDC_REDIRECT_URI
    oidc_post_logout_redirect_uri: str | None = None  # OIDC_POST_LOGOUT_REDIRECT_URI
    oidc_allowed_groups: str = ""  # OIDC_ALLOWED_GROUPS (comma-separated; empty allows all)
    oidc_admin_group: str = "samryetha-admins"  # OIDC_ADMIN_GROUP
    # 登录后允许跳回的**站外** origin（逗号分隔，形如 https://tasks.samryetha.com）。
    # 给兄弟站点用：登录入口统一走 Lako，签完要能回到自己的域名。
    # 只做 origin 精确匹配（见 oidc.safe_return_to），留空 = 仅允许站内路径。
    signin_return_origins: str = ""  # SIGNIN_RETURN_ORIGINS
    # 登录弹层的承载方式：
    #   "redirect"（默认）= 跨源 iframe 嵌 Lako 的 /login 与 /select-account
    #   "json"           = 在论坛弹层里原生渲染 @lako/ui 组件，走 Lako 的 JSON authorize
    # 留这个开关是为了能一键退回 iframe，不用重新部署前端。
    oidc_mode: str = "redirect"  # OIDC_MODE

    @model_validator(mode="after")
    def _resolve_data_paths(self) -> Self:
        # 归一化数据根目录为绝对路径；留空的 upload_dir / database_url 从它派生。
        # Normalize the data root to an absolute path; derive upload_dir / database_url
        # from it when they were left empty.
        data_dir = os.path.abspath(os.path.expanduser(self.samryetha_data_dir))
        self.samryetha_data_dir = data_dir
        if not self.upload_dir:
            self.upload_dir = os.path.join(data_dir, "uploads")
        if not self.database_url:
            self.database_url = os.path.join(data_dir, "app.db")
        # 来源：env（含 .env / 构造参数）还是代码默认，便于运维一眼判断路径为何变了。
        # Source: env (including .env / constructor) vs code default, so ops can tell at a
        # glance why the path changed.
        source = "env" if "samryetha_data_dir" in self.model_fields_set else "default"
        logger.info("data root: %s (source=%s)", data_dir, source)
        # 三者任一解析到 git 仓库内都告警（不拒绝启动）：默认已外置，这里兜住显式覆盖的旧
        # .env（如 UPLOAD_DIR=./uploads）或误把 SAMRYETHA_DATA_DIR 指回仓库内的情况。
        # Warn (not reject) when any of the three resolves inside a git repo: the default is
        # already external, so this only catches an explicit legacy .env (e.g. UPLOAD_DIR=./uploads)
        # or a SAMRYETHA_DATA_DIR accidentally pointed back into the repo.
        _warn_if_inside_repo("SAMRYETHA_DATA_DIR", data_dir)
        _warn_if_inside_repo("UPLOAD_DIR", os.path.abspath(self.upload_dir))
        if _is_file_path(self.database_url):
            _warn_if_inside_repo("DATABASE_URL", os.path.abspath(self.database_url))
        return self

    @property
    def is_production(self) -> bool:
        return self.node_env == "production"

    @property
    def email_domain_allowlist(self) -> list[str]:
        return [
            d.strip().lower()
            for d in self.allowed_email_domains.split(",")
            if d.strip()
        ]

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_redirect_uri)

    @property
    def oidc_allowed_group_list(self) -> list[str]:
        return [group.strip() for group in self.oidc_allowed_groups.split(",") if group.strip()]

    @property
    def signin_return_origin_list(self) -> list[str]:
        """白名单 origin，统一去掉尾部斜杠，便于逐字符比对。"""
        origins = [origin.strip().rstrip("/") for origin in self.signin_return_origins.split(",") if origin.strip()]
        return list(dict.fromkeys(origins))

    @property
    def browser_origin_list(self) -> list[str]:
        """允许携带论坛会话调用 API 的精确浏览器 origin。"""
        return list(dict.fromkeys(origin for origin in [self.app_origin.strip().rstrip("/")] if origin))


# 生产环境禁止使用的默认凭据/密钥（代码兜底默认值，防误用公开已知默认凭据上线）
_PROD_FORBIDDEN_DEFAULTS = {
    "ADMIN_PASSWORD": "SamryethaAdmin@NeatAvocado2026!",
    "DEV_PASSWORD": "NeatAvocadoOnTop2026",
    "STORAGE_SECRET": "dev-storage-secret-change-me",
}


def load_settings() -> Settings:
    settings = Settings()
    oidc_required = {
        "OIDC_ISSUER": settings.oidc_issuer,
        "OIDC_CLIENT_ID": settings.oidc_client_id,
        "OIDC_REDIRECT_URI": settings.oidc_redirect_uri,
    }
    if any(oidc_required.values()) and not all(oidc_required.values()):
        missing = [name for name, value in oidc_required.items() if not value]
        raise RuntimeError("Incomplete OIDC configuration: " + ", ".join(missing) + " must be set")
    if settings.is_production:
        offenders = [
            name for name, default in _PROD_FORBIDDEN_DEFAULTS.items()
            if getattr(settings, name.lower()) == default
        ]
        if offenders:
            raise RuntimeError(
                "Insecure defaults detected in production: "
                + " / ".join(offenders)
                + " must be overridden"
            )
        # 生产无 HTTPS：只告警、不拒绝启动，避免误伤已有 http 部署（OIDC 相关 URL 仍强制）。
        if urlparse(settings.app_origin).scheme != "https":
            logger.warning(
                "APP_ORIGIN is not HTTPS in production — session cookies should be Secure; "
                "front with TLS before going public"
            )
        if settings.oidc_enabled:
            oidc_urls = {
                "OIDC_ISSUER": settings.oidc_issuer,
                "OIDC_REDIRECT_URI": settings.oidc_redirect_uri,
                "OIDC_POST_LOGOUT_REDIRECT_URI": settings.oidc_post_logout_redirect_uri,
            }
            insecure = [name for name, value in oidc_urls.items() if value and urlparse(value).scheme != "https"]
            if insecure:
                raise RuntimeError("Production OIDC URLs must use HTTPS: " + ", ".join(insecure))
            if not settings.oidc_client_secret:
                raise RuntimeError("OIDC_CLIENT_SECRET must be set in production")
            if not settings.oidc_allowed_group_list:
                raise RuntimeError("OIDC_ALLOWED_GROUPS must contain at least one group in production")
    return settings
