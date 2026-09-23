"""Environment configuration.

Mirrors backend/src/config/env.ts key-for-key so the TS ``.env`` can be reused.
Timestamp/cursor units: epoch MILLISECONDS as integers (same as the TS/DB layer).
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    node_env: str = "development"  # NODE_ENV
    port: int = 3001  # PORT
    app_origin: str = "http://localhost:3000"  # APP_ORIGIN
    tasks_origin: str = "http://localhost:5300"  # TASKS_ORIGIN（独立 Tasks 前端）
    database_url: str = "./data/app.db"  # DATABASE_URL
    cookie_secure: bool = False  # COOKIE_SECURE ("true"/"false"/"1"/"0")
    cookie_domain: str = ""  # COOKIE_DOMAIN：跨子域共享会话时设为 .samryetha.com；留空 = host-only
    trust_proxy: bool = False  # TRUST_PROXY：是否信任反向代理的 X-Forwarded-For（直连公网保持 false）
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
    upload_dir: str = "./uploads"  # UPLOAD_DIR
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
    # 登录后允许跳回的**站外** origin（逗号分隔，形如 https://i18n.samryetha.com）。
    # 给翻译站那类兄弟站点用：登录入口统一走 Lako，签完要能回到自己的域名。
    # 只做 origin 精确匹配（见 oidc.safe_return_to），留空 = 仅允许站内路径。
    signin_return_origins: str = ""  # SIGNIN_RETURN_ORIGINS
    # 登录弹层的承载方式：
    #   "redirect"（默认）= 跨源 iframe 嵌 Lako 的 /login 与 /select-account
    #   "json"           = 在论坛弹层里原生渲染 @lako/ui 组件，走 Lako 的 JSON authorize
    # 留这个开关是为了能一键退回 iframe，不用重新部署前端。
    oidc_mode: str = "redirect"  # OIDC_MODE

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
        if self.tasks_origin.strip():
            origins.append(self.tasks_origin.strip().rstrip("/"))
        return list(dict.fromkeys(origins))

    @property
    def browser_origin_list(self) -> list[str]:
        """允许携带论坛会话调用 API 的精确浏览器 origin。"""
        origins = [self.app_origin.strip().rstrip("/")]
        if self.tasks_origin.strip():
            origins.append(self.tasks_origin.strip().rstrip("/"))
        return list(dict.fromkeys(origin for origin in origins if origin))


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
