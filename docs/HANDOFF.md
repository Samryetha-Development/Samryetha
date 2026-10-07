# 交接文档（Handoff）

**交接时间**：本次会话结束时
**仓库**：`/Users/tyc/Documents/AI/论坛/Samryetha`
**分支**：`dev`（有大量未提交改动，见第三节）

> 这份文档是给接手的 AI 或开发者看的。所有命令、路径、数字都在交接前实际验证过。

---

## 一、项目结构（与本次改动相关）

```
Samryetha/
├── backend/          FastAPI 论坛后端（Python 3.12+ / uv / SQLite）
│   ├── src/samryetha/    业务代码；schema.py 是数据库唯一真源
│   ├── tests/            pytest，194 passed
│   └── docs/             架构/API 文档
├── frontend/         React 19 SSR 前端（pnpm / Vite）
├── lako/             自建 OIDC 身份提供方（FastAPI + Next.js）
│   ├── api/              身份服务
│   ├── web/              授权界面
│   └── packages/ui/      共享 UI 组件
```

**三条必须知道的约定**：

1. **`backend/src/samryetha/schema.py` 是数据库唯一真源**。运行时直接打开既有 SQLite，不跑 DDL；新增列靠 `Database.ensure_schema_drift()` 幂等补列（`db.py`）。补列有限制：不能是 PK/UNIQUE，NOT NULL 必须有 `server_default`，否则会主动报错。
2. **翻译随前端打包，只剩英文 + 简体中文**：词条真源是 `frontend/src/lib/locales/en.ts` 与 `zh-CN.ts`，由 `frontend/src/lib/i18n.tsx` 直接 import，构建时进 bundle。独立 i18n 服务、翻译站、seed 与生成/同步脚本都已移除，没有运行时 catalog 拉取。

---

## 二、本次会话完成的工作（四块，均已验证）

### A. OAuth 链路接入邮箱验证码（Lako + 论坛）

**问题**：OAuth 链路上有多处「适合邮箱验证码但没有接」的地方。

**已完成**：
- Lako 新增邮箱验证码（6 位），作为**第二因素**与**免密登录**手段。
- 邮箱未验证时 `/oauth/authorize` 会跳到新的 `/verify-email` 页（开关 `OIDC_REQUIRE_VERIFIED_EMAIL`，**默认关闭**）。
- 论坛扫码登录加邮箱二次确认。
- 修了一个真实缺陷：`id_token` 的 `amr` 原来用 `authentication_method.split("_")`，会把 `EMAIL_CODE` 拆成 `["email","code"]`（谎称用了 email 因素）。现改为显式映射。

**关键文件**：
- `lako/api/app/authentication/email_codes.py`（新增，共享验证码模块）
- `lako/api/app/oauth/routes.py`（`amr_claim()`、`verify_email` 判定分支）
- `lako/api/alembic/versions/0007_email_codes.py`（新增迁移，已验 upgrade/downgrade）
- `lako/web/app/verify-email/page.tsx`（新增拦截页）
- `backend/src/samryetha/qr_login.py` + `routers/auth.py`（扫码邮箱确认）
- 测试：`lako/api/tests/test_email_codes.py`、`backend/tests/test_qr_email_code.py`

### B. Markdown / LaTeX 渲染整体重做

**问题**：用户给的「Markdown Prism 级别」样例无法完整渲染。

**根因（三个真 bug）**：
1. **多行 `$$…$$` 完全不渲染**：后端 `breaks=True` 把公式内的换行变成 `<br>`，一个公式被拆进多个文本节点，客户端逐节点匹配永远拼不回来。
2. **`\[…\]` 定界符被吃掉**：markdown-it 把反斜杠当转义，客户端根本看不到定界符。
3. **未知语言标签吞掉整段代码**。

**修法（架构性）**：**服务端切结构，客户端只排版**。
- `backend/src/samryetha/markdown.py` 新增 markdown-it 规则，把公式切成空的 `<span class="math-{inline,block}" data-tex="…">`，由 KaTeX 在浏览器填充。这样结构不会被 `<br>` 拆散，KaTeX 的 MathML 也不必进 SSR（`nh3` 会剥掉 `<math>`）。
- 同时补齐：**Pygments 代码高亮**、**GFM 任务列表**、**标题锚点**（GitHub 风格 id）、**Mermaid**（懒加载，120MB 不进主 chunk）。
- `renderMathInHtml` 仍保留按文本节点扫描的旧路径，只服务于本次改动之前入库的 `body_html`（那些行不重算）。

**关键文件**：
- `backend/src/samryetha/markdown.py`（`_math_rule` / `_highlight_code` / `_add_heading_ids` / `_add_task_list_items`）
- `frontend/src/lib/math-text.tsx`（消费 `data-tex`）、`markdown-lite.ts`、`markdown-text.tsx`、`mermaid-block.ts`、`rich-body.tsx`
- 测试：`backend/tests/test_markdown_render.py`（20 条，每条对应一个踩过的坑）
- 文档：`backend/docs/architecture.md` 的「Markdown / LaTeX 渲染分工」一节

### 内容审核已移除

内容直接发布；旧库升级按 `backend/docs/schema.md` 恢复历史内容。最新架构与接口以 `backend/docs/architecture.md` 和 `api-contract.md` 为准。

## 三、未提交的改动

**全部改动未提交**（用户未要求提交）。`git status` 有 40+ 个已修改文件 + 18 个未跟踪文件。

未跟踪的关键新文件：
```
backend/src/samryetha/review_queue.py
backend/src/samryetha/routers/review_queue.py
frontend/src/lib/{markdown-lite.ts,markdown-text.tsx,mermaid-block.ts,rich-body.tsx}
lako/api/alembic/versions/0007_email_codes.py
lako/api/app/authentication/email_codes.py
lako/api/tests/test_email_codes.py
lako/web/app/verify-email/
```

**依赖变化**：`frontend/package.json` 新增 `mermaid`；`backend/pyproject.toml` 把 `pygments` 从传递依赖提升为显式依赖。

---

## 五、验证命令

```bash
# 后端（215 passed 是本次实现后的基线；交接时是 194）
cd backend && uv run pytest -q


# 前端
cd frontend && pnpm typecheck && pnpm build

# 另一个包
cd lako/api && uv run pytest -q        # 106 passed

# openapi（改了路由之后）
cd backend && uv run python scripts/export_openapi.py

# schema 漂移兜底（新增列后务必验证既有库能升级）
cd backend && uv run pytest tests/test_db_schema_drift.py -q
```

---

## 六、需要留意的坑（按重要性）

4. **`ensure_schema_drift` 的两个渲染坑（本次已修，别再写回去）**：
   `server_default=""` 必须按字面量加引号（否则生成 `DEFAULT  NOT NULL`，存量库启动崩）；
   无类型的 FK 列（NullType）不能调 `col.type.compile()`。索引也要一并补（IF NOT EXISTS）。
5. **可见性有五个出口**：详情、列表、回复列表、私信、**搜索**。搜索曾经漏过滤，
   封禁内容能从搜索漏出去——改可见性时五个出口一起想。
6. **KaTeX 输出含 MathML，`nh3` 会剥掉**：所以公式排版必须在客户端做，不能进 SSR。
7. **SSR 与首帧必须一致**：`MarkdownText` 在服务端输出纯文本，挂载后才渲染 Markdown/公式，
   这是刻意的（否则 hydration mismatch）。
8. **`tests/` 不是包**：`from conftest import Api`。
9. **mermaid 很大（120MB）**：必须保持懒加载，不要 import 进主 bundle。

---

## 七、用户偏好（沟通层面）

- 中文交流。
- 要求**先说明问题再动手**：发现 bug 时要讲清根因，不要直接改。
- 不接受「假装完成」：如果某处没做（比如未接入的功能），要明确说出来。
- 文档要**正式文风**（用户明确要求）。
- 密钥由用户自己填进 `.env`，**不要索取或写入对话**。
- 用户会提供外部 API 的文档，但**文档可能有笔误**（例如把 Model ID 写成版本名），需要对照官方文档核实。
