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
    database_url: str = "./data/app.db"  # DATABASE_URL
    cookie_secure: bool = False  # COOKIE_SECURE ("true"/"false"/"1"/"0")
    trust_proxy: bool = False  # TRUST_PROXY：是否信任反向代理的 X-Forwarded-For（直连公网保持 false）
    session_ttl_ms: int = 30 * 24 * 3600 * 1000  # SESSION_TTL_MS
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
