import logging
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    app_origin: str = "http://localhost:3000"
    api_origin: str = "http://localhost:8000"
    database_url: str = "sqlite+aiosqlite:///./lako.db"
    cookie_secure: bool = False
    oidc_issuer: str = "http://localhost:3000"
    jwt_private_key: str | None = None
    jwt_key_id: str = "lako-dev-1"
    credential_encryption_secret: str = "development-only-change-this-credential-secret"
    session_ttl_seconds: int = 60 * 60 * 24 * 30
    auth_code_ttl_seconds: int = 300
    access_token_ttl_seconds: int = 900
    allowed_origins: str = "http://localhost:3000,http://localhost:4000"
    # Comma-separated networks allowed to supply X-Forwarded-For. Keep empty
    # unless the API is reachable only through a controlled reverse proxy.
    trusted_proxy_cidrs: str = ""
    samryetha_client_id: str = "samryetha"
    samryetha_client_secret: str | None = None
    samryetha_redirect_uris: str = (
        "http://localhost:3000/auth/callback,http://localhost:4000/auth/callback,"
        "https://samryetha.com/api/auth/callback"
    )
    # Service token for POST /api/admin/users/import (bulk provisioning for forum
    # migration). Unset = endpoint fail-closed with 403. Never expose publicly.
    admin_import_token: str | None = None
    # Outgoing mail (password reset / verification / invites). Unset host =
    # log-only NullMailer (local dev). Production requires a real server.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    smtp_timeout_seconds: int = 10

    @field_validator("app_origin", "api_origin", "oidc_issuer")
    @classmethod
    def no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.environment == "production":
            insecure = [
                name
                for name, value in {
                    "APP_ORIGIN": self.app_origin,
                    "API_ORIGIN": self.api_origin,
                    "OIDC_ISSUER": self.oidc_issuer,
                }.items()
                if not value.startswith("https://")
            ]
            if insecure:
                raise ValueError("Production origins must use HTTPS: " + ", ".join(insecure))
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if not self.jwt_private_key:
                raise ValueError("JWT_PRIVATE_KEY is required in production")
            if self.credential_encryption_secret == "development-only-change-this-credential-secret":
                raise ValueError("CREDENTIAL_ENCRYPTION_SECRET must be replaced in production")
            if not self.samryetha_client_secret:
                raise ValueError("SAMRYETHA_CLIENT_SECRET is required in production")
            # SMTP 只告警不拦：无邮件时 Lako 仍要能用（管理员可代设密码），
            # 只是自助重置/验证/邀请这类功能静默不可用。启动日志里必须看得见。
            if not self.smtp_host:
                logger.warning(
                    "SMTP_HOST is not set: Lako will start, but password reset, "
                    "email verification, and invite emails will NOT be delivered."
                )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def samryetha_redirect_uri_list(self) -> list[str]:
        return [uri.strip() for uri in self.samryetha_redirect_uris.split(",") if uri.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
