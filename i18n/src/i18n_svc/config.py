"""i18n 服务配置。

默认从 .env 读取（与主站同一个 .env 文件共用，extra="ignore" 忽略未声明字段）。
auth_db_url 缺省时与 database_url 相同（独立 i18n DB 模式）；
设置为主站 database_url 时即可复用 shared auth DB 读取 samryetha_session。
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # populate_by_name：同名字段（如构造时传 database_url=）与别名 env（I18N_DATABASE_URL）都可用
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    node_env: str = "development"         # NODE_ENV
    port: int = Field(default=3002, validation_alias=AliasChoices("I18N_PORT", "PORT"))
    app_origin: str = "http://localhost:3000"  # APP_ORIGIN（CORS 白名单，逗号分隔可配多个）
    i18n_site_origin: str = "http://localhost:5200"  # I18N_SITE_ORIGIN（翻译站 CORS）
    tasks_site_origin: str = "http://localhost:5300"  # TASKS_SITE_ORIGIN（Tasks 读取翻译 catalog）

    # i18n 自己的数据库（存翻译条目 + 提交记录）
    database_url: str = Field(default="./data/i18n.db", validation_alias=AliasChoices("I18N_DATABASE_URL", "DATABASE_URL"))

    # auth DB：读 samryetha_session / users / sessions；缺省与 database_url 同库（独立模式）
    # 生产时设为主站 database_url（例如 ./data/app.db）以复用真实会话
    auth_db_url: str = Field(default="", validation_alias=AliasChoices("I18N_AUTH_DB_URL", "AUTH_DB_URL"))

    cookie_secure: bool = False           # COOKIE_SECURE

    # 翻译站静态产物目录（构建后由本服务托管，SPA fallback 到 index.html）；
    # 留空则只提供 API（site 单独部署）。
    site_dir: str = Field(default="", validation_alias=AliasChoices("I18N_SITE_DIR", "SITE_DIR"))

    # 支持的 locale 白名单（逗号分隔）；与前端 LOCALES 常量保持一致（8 个）
    supported_locales: str = Field(default="en,zh-CN,zh-TW,ja,ko,es,fr,de", validation_alias=AliasChoices("I18N_SUPPORTED_LOCALES", "SUPPORTED_LOCALES"))

    @property
    def is_production(self) -> bool:
        return self.node_env == "production"

    @property
    def allowed_origins(self) -> list[str]:
        """CORS 白名单：主站、翻译站与独立 Tasks 站，精确 origin 去重。"""
        origins = [o.strip() for o in self.app_origin.split(",") if o.strip()]
        if self.i18n_site_origin.strip():
            origins.append(self.i18n_site_origin.strip())
        if self.tasks_site_origin.strip():
            origins.append(self.tasks_site_origin.strip())
        return list(dict.fromkeys(origins))  # 保序去重

    @property
    def supported_locale_list(self) -> list[str]:
        return [lc.strip() for lc in self.supported_locales.split(",") if lc.strip()]

    @property
    def effective_auth_db_url(self) -> str:
        """auth DB 路径：未配置时退化为与 i18n DB 同库（测试/开发独立模式）。"""
        return self.auth_db_url.strip() or self.database_url


_PROD_FORBIDDEN_DEFAULTS: dict[str, str] = {}  # 本服务暂无需保护的默认凭据


def load_settings() -> Settings:
    return Settings()
