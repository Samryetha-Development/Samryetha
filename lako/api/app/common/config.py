from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
