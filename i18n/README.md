# Samryetha i18n Service

独立 FastAPI 微服务，管理 Samryetha 的翻译条目（catalog）并接收社区翻译建议（submissions）。

## 功能

| 端点 | 认证 | 说明 |
|------|------|------|
| `GET /health` | 公开 | 健康检查 |
| `GET /api/me` | 公开（读会话） | 当前登录用户，未登录 `{"user": null}`；翻译站登录态判断 |
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

### 静态托管翻译站（可选）

设置 `I18N_SITE_DIR`（如 `./site/dist`）后，本服务会在 API 之外把 `site/` Vite 构建产物一并托管：
`/assets/*` 走静态文件，其余非 API 路径 SPA fallback 到 `index.html`。这样翻译站与 API 同源部署，
浏览器端请求 `/api/catalog/...`、`/api/submissions` 时自带会话 cookie，无需单独配跨域。

### 跨子域共享登录

要在 `i18n.<主域>` 上共用主站的登录态，两点缺一不可：

1. **主站**设置 `COOKIE_DOMAIN=.samryetha.com`（forum 与 sub 同域，session cookie 共享给 i18n 子域）；
2. **本服务** `I18N_AUTH_DB_URL` 指向主站 `app.db`，读取 `samryetha_session` 完成身份识别。

主站 SSR 侧的 i18n 预取用一个地址（`I18N_API_ORIGIN`，如内网 `http://127.0.0.1:3002`），
注入浏览器用的则是公网地址（`I18N_CLIENT_ORIGIN`，如 `https://i18n.samryetha.com`），见 `frontend/README` 与 `server.mjs`。

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

`seed/` 目录包含全量翻译，覆盖前端支持的 8 个 locale（`en`、`zh-CN`、`zh-TW`、`ja`、`ko`、`es`、`fr`、`de`），每文件 675 个条目，与前端保持同步。

**翻译源（单向两跳）**：以 `frontend/src/lib/locales/*.ts` 为唯一真源（运行时实际 import）。
链路为 `.ts` → `.json` → `seed/*.json` → DB，其中 `.json` 是生成的中间产物，不得手工改；
`seed.py` 只进不出（seed → DB），不会回写前端。
CI 卡两段一致：`gen_locale_json.py --check`（.ts→.json）与 `check_sync.py`（.json→seed）。

```bash
# 第 1 跳：.ts 改动后必跑（在 frontend/ 目录）
python3 ../frontend/scripts/gen_locale_json.py
python3 ../frontend/scripts/gen_locale_json.py --check   # 只对比不写入

# 第 2 跳：前端 → seed 单向同步
uv run python sync_from_frontend.py

# 只对比不写入
uv run python sync_from_frontend.py --check

# CI 校验：对比两边 key 集合，不一致则 exit 1
uv run python check_sync.py

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
