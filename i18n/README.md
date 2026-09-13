# Samryetha i18n Service

独立 FastAPI 微服务，管理 Samryetha 的翻译条目（catalog）并接收社区翻译建议（submissions）。

## 功能

| 端点 | 认证 | 说明 |
|------|------|------|
| `GET /health` | 公开 | 健康检查 |
| `GET /api/catalog/{locale}` | 公开 | 获取某 locale 的全部翻译条目（含 id/description/时间戳） |
| `GET /api/catalog/{locale}/translations` | 公开 | 获取扁平 `{key: value}` 字典，供主站 SSR 预取 |
| `GET /api/source` | 公开 | 获取所有 locale 的对照视图 |
| `GET /api/submissions` | 登录 | 列出翻译建议（普通用户只见自己的，管理员见全部） |
| `POST /api/submissions` | 登录 | 提交翻译建议 |
| `POST /api/submissions/{id}/review` | 管理员 | 审核建议（approve 同时 upsert catalog） |
| `PUT /api/catalog/{locale}/{key}` | 管理员 | 管理员直接写入/更新翻译条目 |
| `DELETE /api/catalog/{locale}/{key}` | 管理员 | 删除翻译条目 |

## 快速启动

```bash
cd i18n
cp .env.example .env
# 按需编辑 .env

# 安装依赖（需要 uv）
uv sync

# 导入 seed 数据
uv run python seed.py

# 启动服务（默认端口 3002）
./start.sh
```

Swagger UI 在开发模式下可通过 http://localhost:3002/docs 访问。

## 配置

配置项通过环境变量或 `.env` 文件注入，见 `.env.example`。

### auth DB 模式说明

`I18N_AUTH_DB_URL` 控制会话验证来源：

- **留空（默认）**：与 `I18N_DATABASE_URL` 同库。适用于开发/测试，需在 auth DB 中手工建 users/sessions 表，或使用 `seed.py` 之外的工具注入。
- **设为主站 DB 路径**（如 `../backend/data/app.db`）：直接读取主站的 `samryetha_session`，零配置接入真实用户体系。

本服务**只读** auth DB，不写入 users 或 sessions。

## 数据库

i18n 服务使用独立 SQLite（`data/i18n.db`），包含两张表：

- `catalog_entries` — 标准翻译条目 (`locale`, `key`, `value`, `description`)
- `submissions` — 用户提交记录，带审核状态

## 校验规则

- **locale**：BCP-47 格式（如 `zh-CN`、`en`），且须在 `I18N_SUPPORTED_LOCALES` 白名单中
- **key**：点分命名空间，各段限字母/数字/下划线/连字符，最长 128 字符（如 `nav.home`）

## 测试

```bash
cd i18n
uv run pytest -v
```

## Seed 数据

`seed/` 目录包含全量翻译，覆盖前端支持的 8 个 locale（`en`、`zh-CN`、`zh-TW`、`ja`、`ko`、`es`、`fr`、`de`），每文件 635 个条目，与 `frontend/src/lib/locales/*.json` 保持同步。

```bash
# 导入全部 locale
uv run python seed.py

# 只导入 zh-CN
uv run python seed.py --locale zh-CN

# 干跑（仅打印，不写入）
uv run python seed.py --dry-run
```

## 与主站的关系

- **不修改主站代码**：本服务完全独立运行，不 import `samryetha` 包。
- **共用 auth**：通过 `samryetha_session` cookie + auth DB 读取验证用户身份（主站写入会话，本服务只读）。
- **端口**：默认 3002，不与主站（3001）冲突。
- **样式**：遵循主站 Python 编码规范（`pydantic-settings`、SQLAlchemy Core、FastAPI `Depends`、`request_conn()` 事务语义）。
