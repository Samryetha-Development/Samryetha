"""Environment configuration.

Mirrors backend/src/config/env.ts key-for-key so the TS ``.env`` can be reused.
Timestamp/cursor units: epoch MILLISECONDS as integers (same as the TS/DB layer).
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("samryetha.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    node_env: str = "development"  # NODE_ENV
    port: int = 3001  # PORT
    app_origin: str = "http://localhost:3000"  # APP_ORIGIN
    database_url: str = "./data/app.db"  # DATABASE_URL
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
    # 登录后允许跳回的**站外** origin（逗号分隔，形如 https://tasks.samryetha.com）。
    # 给兄弟站点用：登录入口统一走 Lako，签完要能回到自己的域名。
    # 只做 origin 精确匹配（见 oidc.safe_return_to），留空 = 仅允许站内路径。
    signin_return_origins: str = ""  # SIGNIN_RETURN_ORIGINS
    # 登录弹层的承载方式：
    #   "redirect"（默认）= 跨源 iframe 嵌 Lako 的 /login 与 /select-account
    #   "json"           = 在论坛弹层里原生渲染 @lako/ui 组件，走 Lako 的 JSON authorize
    # 留这个开关是为了能一键退回 iframe，不用重新部署前端。
    oidc_mode: str = "redirect"  # OIDC_MODE

    # ---------------------------------------------------------------- 自动审核
    # 总开关。关闭时所有内容直接 approved，审核队列为空——本地开发与既有部署不受影响。
    automod_enabled: bool = False  # AUTOMOD_ENABLED
    # 语义审核（OpenAI 兼容接口）。未配 base_url/model 时只跑确定性规则层，
    # 这是刻意的降级路径：模型可选，规则层永远在。
    automod_base_url: str | None = None  # AUTOMOD_BASE_URL（如 http://localhost:11434/v1）
    automod_api_key: str | None = None  # AUTOMOD_API_KEY
    automod_model: str | None = None  # AUTOMOD_MODEL
    automod_timeout_seconds: int = 12  # AUTOMOD_TIMEOUT_SECONDS
    # 采样温度。留空 = 不发送该字段（用服务端默认值）。
    # 注意：Kimi 的 coding 模型只接受 1，其它值一律 400；审核只需要确定性判定，
    # 用 1 也不会让结果发散（判定是分类而非创作，且提示词给了明确档位）。
    automod_temperature: float | None = 0.0  # AUTOMOD_TEMPERATURE
    # 自定义请求头（JSON）。给需要识别客户端的网关用，例：
    #   {"user-agent": "samryetha-automod/1.0", "x-opencode-session": "samryetha-automod"}
    # OpenCode Go 要求客户端自报身份并带稳定会话 ID，见 docs/opencode.ai/docs/go。
    automod_extra_headers: str = ""  # AUTOMOD_EXTRA_HEADERS
    # 规则层判定为 review/block 时，内容是否对普通用户隐藏（True=先审后发）。
    # False 时只入队列，内容仍然可见——用于"先发后审"的过渡部署。
    automod_hold_pending: bool = True  # AUTOMOD_HOLD_PENDING
    # 驳回时是否给作者发私信说明（公测期建议开，减少"我帖子怎么没了"的困惑）。
    automod_notify_author: bool = True  # AUTOMOD_NOTIFY_AUTHOR
    # 人工确认窗口（秒）。机器只标记，内容先压住；版主在这段时间内定案即为最终结果。
    # 逾期未定案则由 AI 复审先行处置（放行或不公开），人工之后仍可推翻。
    # 这是"宁可漏放"与"内容不能无限期待审"之间的折中：窗口越短，越偏向放行。
    automod_confirm_window_seconds: int = 60  # AUTOMOD_CONFIRM_WINDOW_SECONDS
    # 逾期自动复审总开关。关闭时待审内容一直等人，不做先行处置（旧行为）。
    automod_auto_finalize: bool = True  # AUTOMOD_AUTO_FINALIZE
    # 复审 worker 的轮询间隔（毫秒）。窗口以秒计，没必要轮询得太密。
    automod_finalize_interval_ms: int = 5_000  # AUTOMOD_FINALIZE_INTERVAL_MS
    # 单次复审扫描处理的最大条数，防止一次积压把请求拖死。
    automod_finalize_batch: int = 50  # AUTOMOD_FINALIZE_BATCH

    @property
    def automod_llm_enabled(self) -> bool:
        return bool(self.automod_base_url and self.automod_model)

    @property
    def automod_header_map(self) -> dict[str, str]:
        """AUTOMOD_EXTRA_HEADERS 解析结果。写错 JSON 只告警不拦启动——审核是增强功能，
        不该因为一个 header 拼错就让整个服务起不来。"""
        if not self.automod_extra_headers.strip():
            return {}
        try:
            parsed = json.loads(self.automod_extra_headers)
        except ValueError:
            logger.warning("AUTOMOD_EXTRA_HEADERS is not valid JSON; ignoring it")
            return {}
        if not isinstance(parsed, dict):
            logger.warning("AUTOMOD_EXTRA_HEADERS must be a JSON object; ignoring it")
            return {}
        return {str(k): str(v) for k, v in parsed.items()}

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
