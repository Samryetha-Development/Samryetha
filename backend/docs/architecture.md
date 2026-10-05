# Samryetha 后端架构

## 1. 概览

Samryetha 是学校内部论坛/社区产品的后端。采用 **modular monolith**（模块化单体）：一个进程、一个数据库，按领域模块清晰切分，模块间通过已声明的 service 接口调用，禁止跨模块直接 import 私有表。

技术栈（实际落地的版本见 `pyproject.toml`，uv 管理 venv）：

| 层 | 选型 |
|----|------|
| 运行时 | Python 3.14（uv） |
| Web 框架 | FastAPI + Starlette |
| 数据库 | SQLite（WAL 模式）+ SQLAlchemy 2.0 Core（显式 Table，唯一 schema 真源在 `schema.py`；存量库直接打开无需迁移） |
| 密码 | Argon2id（argon2-cffi，`m=19456,t=2,p=1`；存量 TS 哈希直接可验） |
| 会话 | 服务端 session，DB 存 sha256 哈希 token，HttpOnly + SameSite=Lax cookie |
| 联合身份 | OIDC authorization code + PKCE；JWT/JWKS 校验使用 joserfc；业务请求仍使用本地 session |
| 校验 | Pydantic v2（`extra='ignore'` 复刻 zod strip） |
| 任务 | transactional outbox + 进程内 worker 线程（`main()` 启动，轮询 SQLite） |
| 实时 | 进程内 EventBus → SSE 通道（StreamingResponse） |
| 附件 | 本地磁盘 + HMAC 签名 URL（presigned-URL 语义） |
| 邮件 | Console 打日志（Mailer 接口预留 SMTP 实现） |
| 定时备份 | apscheduler（VACUUM INTO） |
| 测试 | pytest（FastAPI TestClient；SSE 用真实 uvicorn + httpx 流式） |

> 契约与 TS 版 1:1（路径/方法/错误包络/DTO/SSE 事件），前端零改动。HTTP 面以 `docs/openapi.json`（FastAPI 导出）为准。

## 2. 模块划分

```
src/samryetha/
  main.py        # FastAPI 装配、中间件、统一错误、lifespan；main() 起 worker/备份
  schema.py      # 显式 Table 定义（唯一 schema 真源）
  db.py          # 引擎/PRAGMA/请求级事务
  errors.py      # ApiError + 422/400/429/500 处理器
  authz.py       # can() 能力矩阵——全站授权唯一入口
  deps.py        # require_user / require_active_user / DbConn
  auth.py users.py follows.py boards.py discussions.py attachments.py search.py
  notifications.py moderation.py admin.py feedback.py feedback_backup.py
  presence.py events.py outbox.py outbox_worker.py mailer.py markdown.py security.py storage.py
  routers/       # 每特性一组 APIRouter（路径与 TS 对齐）
```
  attachments/    # presign→上传→绑定→下载
  moderation/     # 举报/封禁/审计/内容恢复
  feedback/       # 反馈：项目/成员(程序员)/条目 + Agent API + 备份恢复
  infrastructure/ # db / cache / presence / queue / storage / email / events
  config/         # 环境变量校验（Zod）
  scripts/        # seed 兜底脚本（确保内置账号；mock 已停用）
```

**依赖规则**：
- 模块通过 `container.ts` 注入的 service 接口互相调用。
- 业务模块不 import 其他模块的私有表；表只在 `infrastructure/db/schema.ts` 声明一次。
- **业务代码零 `user.role ===` 判断**，授权唯一入口 `can(user, ability, resource)`。
- **异步副作用绝不写在业务事务内**，一律经 outbox 事件。

## 3. 分层与数据流

```
HTTP 请求
  → Fastify 插件栈（CORS / cookie / rate-limit / swagger / CSRF Origin / request-id）
  → zod 校验（422）
  → preHandler 解析 session cookie → request.currentUser
  → 路由 handler → 模块 service
      → can() 授权（403）
      → 业务事务 db.tx()
          ├─ 业务行写入
          └─ outbox 行写入（同事务原子提交）
  → 响应序列化
```

**两条副作用通道**：

1. **outbox（持久、可靠）**——事务内写 `outbox_events` 行，worker 每 500ms 原子 claim，处理完成后写 `processed`。失败指数退避（上限 10 次转 `failed`）。用途：发验证码邮件、生成通知、重索引。
2. **进程内 EventBus（瞬时）**——outbox 处理完成后 `events.publish()`，SSE hub 订阅做实时推送。断线重连靠客户端重拉通知兜底。多实例时换成 Redis pub/sub，业务代码不变。

## 4. 核心横切关注点

### 自动审核（公测期）

三段式：**规则 → 语义模型 → 人工确认**。机器**只标记、不定案**：封禁必须有人经手，
或者由复审在确认窗口结束后落定，且人工随时可以推翻。

```
写入路径 ──▶ automod.submit()
                ├─ automod_rules.evaluate_rules()   确定性：关键词/查重/版块位置
                ├─ automod_providers (OpenAI 兼容)   语义：隐晦表达、变体绕过
                └─ merge_verdicts()                  取更严者
                        │
        allow ──────────┼────────── review / block
        │               │                    │
   moderation_status=   入队 moderation_queue + moderation_status=pending
   approved（可见）      │                    （机器只标记，先压住；作者可见）
                         │
                         ├─ 版主在 AUTOMOD_CONFIRM_WINDOW_SECONDS 内定案 ──▶ 放行 / 封禁
                         │
                         └─ 窗口超时无人定案 ──▶ AI 复审（提示词见 _RECHECK_PROMPT）
                                                  ├─ 复审放行 ──▶ 先行公开（published_by_ai）
                                                  └─ 复审不放行 ─▶ 先行封禁（blocked）
                                                         │
                                          人工事后「维持」或「推翻」（overturned=1）
```

设计约束（每条都有理由，改动前先读）：

- **机器只标记**：`automod.held_status()` 只为 `allow` 放行，`review` 与
  `block` 一律先写成 `pending`。机器命中不等于封禁成立——要么人工定案，要么复审落定。
- **1 分钟确认窗口**：入队时写 `hold_until = now + AUTOMOD_CONFIRM_WINDOW_SECONDS`。
  窗口内版主处置即终局；窗口只约束"自动落定"，不限制人工随时处置。
- **逾期复审由复审单独决定**：用户确认的规则是"初审和复审都放行才放行"，而进入窗口的
  内容初审必然未放行，因此**复审判通过才先行公开**，否则封禁。复审用独立提示词
  （`automod_providers._RECHECK_PROMPT`）明确要求"忽略曾被标记、独立重判"，
  否则模型会锚定前一次判定，复审退化成复读。
- **落定是"先行"的**：`resolution` 记 `published_by_ai` / `published_by_human` / `blocked`，
  `review_state` 保持 `pending`，`reviewer_id` 为空表示机器处置。人工维持 = 同向确认，
  推翻 = 反向改变（AI 放行→封禁，或 AI 封禁→放行），后者记 `overturned = 1`。
- **审核失败（`resolution=blocked`）仅管理员可访问**：原文在队列的 `submitted_text` 快照中留存，编辑不会覆盖历史版本，
  但版主与作者都看不到（`discussions.moderation_visible()` 对 moderator 过滤 `rejected`）。
  完整记录与正文走 `GET /api/admin/moderation/retained`（`require_admin`）。
  队列里的**摘要**同样是正文的一部分，所以对非管理员一并隐去（`excerptRestricted`），
  并且封禁条目**只有管理员能处置**（`decide()` 对非 admin 抛 403）——否则版主会对着
  自己看不到原文的记录做放行/封禁决定。待审与 AI 已放行的条目不受影响（本来就不是秘密）。
- **判定绑定送审版本**：每次被标记的编辑新增队列行，并将旧行的 `superseded_at` 设为当前时间。新版本直接放行也会停用旧行。worker 和人工处置都只抢占当前版本，旧版本的失败快照仍进入管理员留存库。
- **角色迁移覆盖所有入口**：`create_app()` 的 lifespan 执行旧全局 `moderator` 到 `admin` 的幂等迁移，ASGI 工厂与生产启动使用同一角色模型。
- **判定与可见性解耦**：`moderation_queue.decision` 是机器初次判定，`review_state` 是人的决定，
  分开存才能事后统计"模型判错了多少"。内容表另有 `moderation_status` 表达实际可见性。
- **allow 不入队**。队列只装 review/block。否则正常内容会把队列淹没，版主三天后就
  再也不看了——这是所有"先审后发"系统烂掉的同一个原因。
- **模型永远不直接 `block`**：它的 block 会被降级为 review。自动拒绝是不可逆的用户伤害，
  而模型会误判。只有确定性规则（违法/色情关键词）能直接驳回。
- **模型不可用 → 降级到规则层**（`automod_providers.AutomodUnavailable`）。绝不能出现
  "模型超时 = 全站发不了帖"；同时会在 signals 里记一条 `llm_unavailable`，让人工知道
  这条没经过语义审核。复审失败时按封禁收口（宁可误封不可漏放），并提示人工确认。
- **待审内容的可见性**：作者本人（要看到自己的帖子在审核中，否则会反复重发）与版主可见，
  其他人**返回 404 而不是 403**——403 等于告诉外人"这里有个被审的东西"。
  可见性出口有六个，改的时候要一起想：详情页、列表（含按作者）、回复列表、
  私信、**搜索**、**收藏**（`list_saved` 曾经漏过，封禁帖能从"我的收藏"读出来）。
  统一走 `discussions.moderation_visible()`。
- **个人资料走"待审副本"**：资料改动先写 `users.pending_display_name` / `pending_bio`，
  `display_name`/`bio` 始终保持**上一次通过**的值。这样三件事同时成立：
  §31「待审期间对外展示旧资料」；失败原文不在公开字段里（`get_public_profile` 读的就是
  主字段，所以只有管理员能从留存库看到）；判定放行时 `_promote_pending_profile()` 立刻提升。
  资料是**原地更新**，不能在改前先写主字段——那样一来待审期间旧资料就被顶掉了。
  `to_dto()` 只多给一个布尔量 `profilePending`，待审原文不下发。
- **发布接口必须回显刚创建的内容**，即使它被拦：否则前端在"发布成功"后立刻查不到，
  看起来就是发布失败。
- **规则层的 `block` 权重只有极少数条目能触及**（100 分档）。多数关键词落在 30-70，
  靠累积到 `REVIEW_THRESHOLD=40` 才转人工：宁可多送人工，不可自动误杀。

后台线程与运维入口：

- `automod_worker.ModerationWorker` 每 `AUTOMOD_FINALIZE_INTERVAL_MS` 扫一轮到期项，
  只由生产 `main()` 启动；`finalize_once()` 是纯同步函数，测试直接调用（可注入 `now`）。
- `POST /api/admin/moderation/finalize`（管理员）手动催一轮，用于部署后确认开关与模型通了。
- **落定要抢占**：`UPDATE ... WHERE resolution IS NULL AND superseded_at IS NULL`，只有 `rowcount == 1` 才回写可见性
  并发通知。定时 worker 与手动 `POST /finalize` 可能同时扫到同一条，没有这个条件就会
  重复落定、重复给作者发通知。
- **单条失败要隔离**：`finalize_pending` 对每条用 savepoint 包住，失败只回滚这一条并跳过。
  否则队首一条坏数据会让整批回滚，而它下一轮还排在队首——所有逾期内容永远落定不了。
  回滚到 savepoint 后该行仍是 `resolution IS NULL`，修好后下一轮能补上。
- **升级要回填**：`hold_until` 是后加的列，补列前就在队里的 pending 行是 NULL，而 NULL 的
  语义是"不设窗口、一直等人"，会被 worker 跳过而永久卡住。`ensure_schema_drift` 在
  **刚补上这一列时**按默认窗口回填一次（只在刚补列时执行，所以不会覆盖之后刻意用
  `AUTOMOD_CONFIRM_WINDOW_SECONDS=0` 入队的行）。

配置见 `.env.example` 的"自动审核"与"人工确认窗口"两段。关闭总开关时整条链路是空操作，
与改动前行为一致。

### Markdown / LaTeX 渲染分工

正文渲染是**服务端切结构、客户端排版**的两段式，两边职责不能互换：

- `markdown.py`（服务端，markdown-it + nh3 + Pygments）负责一切结构：段落、标题（带
  GitHub 风格 `id` 锚点）、列表、GFM 任务列表、表格、围栏代码块（按语言做 token 级
  高亮）、以及**把 `$…$` / `$$…$$` / `\(…\)` / `\[…\]` 切成空的
  `<span class="math-{inline,block}" data-tex="…">`**。产出存 `body_html`。
- 浏览器（`frontend/src/lib/math-text.tsx`）只做两件事：把 `data-tex` 交给 KaTeX
  排版，以及把 ```` ```mermaid ```` 块交给按需加载的 mermaid 画图。

为什么公式必须由服务端切：

1. `breaks=True` 会把 `$$…$$` 里的换行变成 `<br>`，一个公式被拆进多个文本节点，客户端
   再怎么扫也拼不回来——多行展示公式曾因此**完全不渲染**。
2. `\[…\]` 的反斜杠会被 markdown-it 当转义吃掉，客户端根本看不到定界符。
3. KaTeX 的输出含 MathML（`<math>`），要保住它就得整片放行净化白名单；只放行一个纯
   文本 `data-tex` 属性，攻击面小得多。

因此 `renderMathInHtml` 里仍保留一段**按文本节点扫描**的逻辑，它只服务于本次改动之前
入库的 `body_html`（那些行不重算），新数据一律走 `data-tex`。

同一份渲染能力也被 `MarkdownText`（简介、个人页预览、反馈/任务评论与备注）复用；
那段文本没有服务端 HTML 列，所以 Markdown 在浏览器侧用一份最小实现
（`frontend/src/lib/markdown-lite.ts`）解析，公式切分复用同一份 `splitMath`。
评论/简介的公式因此**不经过**服务端 `data-tex` 容器，两条路径的公式行为必须保持一致。

### OIDC 身份边界

`auth.samryetha.com` 只负责证明用户身份。论坛以 `(issuer, subject)` 作为不可变外部身份键，在 callback 完整校验 ID token 后创建自己的 `samryetha_session`。state 仅以 SHA-256 形式保存于服务端的一次性事务表，事务同时保存 nonce 和 PKCE verifier；浏览器只持有短期 HttpOnly state cookie。access token 和 ID token 均不写入 localStorage、sessionStorage 或论坛数据库。

首次登录时，只有 OIDC 声明明确包含 `email_verified=true` 才会按完全匹配的 email 关联存量用户；否则创建新的论坛资料。`OIDC_ALLOWED_GROUPS` 控制准入，`OIDC_ADMIN_GROUP` 可将用户提升为论坛 `admin`，具体 API 权限仍由后端能力矩阵执行。

- **request-id**：`genReqId` 生成 `req_<uuid>`，贯穿日志与错误响应。
- **日志**：pino，dev 用 `pino-pretty`。
- **限频**：`@fastify/rate-limit` 全局 300 req/min。
- **CSRF**：非安全方法若带 Origin 必须等于 `APP_ORIGIN`；cookie `SameSite=Lax` 兜底。
- **统一错误处理**：`AppError` / ZodError / Ajv 校验 / 429 全部归一为 `{ error: { code, message, requestId, details? } }`，见 `error-model.md`。

## 5. 目录结构与运行

```
backend/
  package.json  tsconfig.json  .env.example  drizzle.config.ts
  data/            # SQLite 文件（gitignore）
  uploads/         # 本地附件（gitignore）
  docs/            # 本目录 + openapi.json 归档
  tests/           # vitest
  src/
    app/           # server.ts / container.ts / error.ts / auth-hook.ts
    authz/  auth/  users/  schools/  boards/  discussions/  follows/
    notifications/  search/  presence/  realtime/  attachments/  moderation/
    infrastructure/
      db/          # schema.ts + client.ts（WAL pragma、tx 封装、node:sqlite adapter）
      cache/  presence/  queue/  storage/  email/  events/
```

**运行命令**：

```bash
pnpm install
pnpm db:migrate       # 建表
pnpm seed             # 确保内置 admin/dev 账号（启动时也会自动创建）
pnpm dev              # tsx watch，端口 3001，/docs 出 OpenAPI
pnpm test             # vitest（SQLite :memory:）
pnpm build            # tsc 编译到 dist/
```

端口用 **3001**（前端 Vite dev 占 3000）。

## 6. 一键部署（Ubuntu / pm2 / nginx）

仓库根 `./deploy.sh` 从代码到可访问全程自动化。前置要求：`node >= 20`、`pnpm`、`pm2`、`nginx`（脚本只检查不自动安装系统包）。

```bash
./deploy.sh                                # 用本机 IP，http
DOMAIN=forum.example.com ./deploy.sh       # 带域名，http
DOMAIN=forum.example.com SSL=1 ./deploy.sh # 域名 + certbot HTTPS
```

**可覆盖变量**：

| 变量 | 缺省 | 说明 |
| --- | --- | --- |
| `DOMAIN` | 本机 IP | 对外域名；IP 时跳过 SSL |
| `SSL` | `0` | `1` 时用 certbot 自动签发 HTTPS |
| `APP_ORIGIN` | `http://$DOMAIN` | 前端来源校验（CORS/CSRF） |
| `ALLOWED_EMAIL_DOMAINS` | `example.edu.cn` | 注册邮箱域名白名单 |
| `ADMIN_PASSWORD` / `DEV_PASSWORD` | 随机生成并打印 | 内置账号密码 |
| `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | 空 | 可选 Authentik OIDC；启用时必须成套提供，详见 `docs/oidc.md` |

**流程**：检查环境 → 解析变量 → `pnpm install` → 生成 `backend/.env`（已存在则保留）→ 构建前后端 → `pm2` 启动 `samryetha-backend` / `samryetha-frontend`（`pm2 save`）→ 写 nginx 反代 → 可选 SSL → 健康检查。

**nginx 只转发到前端 3000**：前端 `server.mjs` 生产模式自带 `/api` 代理到后端 3001，因此后端端口不对外暴露。SSE 经 `proxy_buffering off` 透传。

**安全提醒**：生产务必在 `backend/.env` 覆盖 `STORAGE_SECRET`、内置账号密码；`COOKIE_SECURE=true`（脚本已默认）。数据库迁移与内置账号在服务启动时自动完成。

## 7. 部署与扩展方向

- 数据库换 PG：`infrastructure/db` 换 drizzle 的 pg 方言 + 迁移脚本；`node:sqlite` adapter 丢弃。
- 缓存/在线/限频：实现 `CacheProvider` / `PresenceStore` / `RateLimiter` 的 Redis 版。
- 实时：EventBus 换 Redis pub/sub。
- 附件：`StorageProvider` 实现 S3 版（presign 语义天然对齐）。
- 邮件：`Mailer` 实现 SMTP 版。
- 搜索：PG 用 `to_tsvector` + GIN；SQLite 用 FTS5 trigram（本机 `node:sqlite` 未编译 FTS5，回退 LIKE）。
