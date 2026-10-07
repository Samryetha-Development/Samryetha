# Samryetha REST API 契约

完整 OpenAPI 3.1 文档见 **`docs/openapi.json`**（由 `uv run python scripts/export_openapi.py` 导出），本地后端运行时可访问 `/docs`（Swagger UI）。翻译提交 `/api/i18n/*` 已移除。

附件的配置、presign、详情与删除接口均由 Pydantic response model 生成契约；附件状态固定为
`pending | uploaded | attached | orphaned`。上传和下载签名仅授权访问 URL，最终仍会根据上传者状态、
父讨论删除状态及当前用户权限进行校验。

私信的发送结果、会话列表、消息列表、已读操作和未读数均使用生成的 HTTP contract。待审消息仅发件人
可见，被拒消息双方均不可见；会话预览和未读统计与消息列表共用同一可见性条件。

认证配置、密码登录、OIDC 认领、扫码登录和紧急登录均使用 Pydantic response model。
所有成功建立论坛会话的端点统一返回 `AuthSessionResponse`：`{ user, sessionExpiresAt }`；
二维码开始端点为兼容现有客户端继续使用 `ticket_id/approve_url/qr_data_uri`，其余响应字段
保持原有 camelCase。OIDC discovery/token/JWKS 属于外部不可信 JSON，验证后才进入业务层。

本文档补充 OpenAPI 无法承载的**行为约定**。

## 通用

- 前缀：`/api`。端口 **3001**。
- 鉴权：本地登录或 OIDC callback 成功后下发站点独立的 `samryetha_session`（HttpOnly + SameSite=Lax）。OIDC token 不返回前端。
- 时间戳：毫秒（`Date.now()`）。
- 分页：游标（keyset），返回 `{ items, nextCursor }`，`nextCursor=null` 表示到底；请求传 `?cursor=<值>`。
- 错误：统一 `{ error: { code, message, requestId, details? } }`（见 `error-model.md`）。
- 权限标注：⚡ 需登录，🔒 需能力（角色），公开读无需登录。

## 认证 `/api/auth`

| 端点 | 说明 |
|------|------|
| `GET /config` | 返回 `{ oidcEnabled }`，供登录页渐进启用 OIDC |
| `GET /login?returnTo=/path` | 启动 OIDC authorization code + PKCE 登录；`returnTo` 只允许本站路径 |
| `GET /callback` | 校验 state、nonce、签名、issuer、audience、exp，绑定身份并下发论坛 session |
| `POST /register` | 注册（邮箱域名 allowlist 校验，422）。body: `email` / `username` / `displayName` / `password`。返回 201 |
| `POST /verify-email` | 验证码激活。body: `email` / `code`(6位)。成功下发 session cookie |
| `POST /resend-verification` | 重发验证码 |
| `POST /login` | 备用本地密码登录，下发 session cookie |
| `POST /logout` ⚡ | 登出（204） |
| `GET /oidc/logout` | 删除论坛 session，并在可用时跳转身份中心全局登出 |
| `GET /me` ⚡ | 当前用户 |
| `POST /forgot-password` | 发送重置邮件 |
| `POST /reset-password` | 重置密码 |
| `POST /change-password` ⚡ | 改密码 |

**验证码获取（开发）**：验证码经 outbox → console 邮件，dev 环境在服务器日志可见。

### 扫码登录的邮箱二次确认

`GET /api/auth/qr/info` 会带上 `emailConfirmationRequired` 与 `emailHint`（掩码地址）。为 `true` 时，
手机端必须先 `POST /api/auth/qr/confirm/request`（`{ticket_id}`）取一封 6 位码邮件，再
`POST /api/auth/qr/approve`（`{ticket_id, code}`）。缺 `code` 返回 403 `EMAIL_CODE_REQUIRED`，
码错误/过期/已用返回 400 通用文案；`POST /api/auth/qr/deny` 任何情况下都不需要码。
触发条件与安全细节见 `oauth-migration.md` 的「邮箱验证码二次确认」。

## 内容

| 端点 | 说明 |
|------|------|
| `GET /discussions?feed=latest\|followed&sort=date\|replies&board=&cursor=` | 帖子流。置顶帖优先；`date` 按发帖时间倒序（默认），`replies` 按回复数倒序。`followed` = 关注的用户发的 + 关注的讨论（无任何关注返回空，**不退化为全量**） |
| `POST /discussions` ⚡ | 发帖。body: `boardSlug` / `title` / `bodyMarkdown` / `bodyFormat` / `attachmentIds`；可选 `draftId` 在同一事务中移除本人的已保存草稿 |
| `POST /discussions/preview` ⚡ | 正文预览。body: `bodyMarkdown`（0–40000 字符）/ `bodyFormat`（`markdown` 默认或 `text`）；返回 `{ bodyHtml }`，复用发帖的渲染与净化，不创建帖子或回复 |
| `GET /discussions/:id` | 详情（软删返回 404） |
| `PATCH /discussions/:id` 🔒 | 编辑（作者/全局mod） |
| `DELETE /discussions/:id` 🔒 | 软删 |
| `POST /discussions/:id/save` / `DELETE .../save` ⚡ | 收藏/取消 |
| `POST /discussions/:id/follow` / `DELETE .../follow` ⚡ | 关注/取消 |
| `POST /discussions/:id/pin` / `lock` 🔒 | 置顶/锁定（mod） |
| `GET/POST /discussions/:id/replies` ⚡ | 回复列表/发布（`parentReplyId` 支持线程） |
| `PATCH/DELETE /replies/:id` 🔒 | 编辑/软删回复 |

Discussion 与 Reply 的请求、feed、详情、预览和写操作响应均由 Pydantic contract
生成 OpenAPI；前端 `BodyFormat`、`ThreadSummary`、
`DiscussionDetail`、`ReplyDTO` 以及相应请求体直接引用生成类型。router 会在返回前
验证 service 结果，防止数据库字段或手写映射漂移后静默污染 wire contract。数据库行
只在 `discussion_repository.py` 转为不可变 record；用户的 posts、replies、saved 三个
feed 也声明对应 response model，其中 authored reply 额外包含 `discussionTitle`。

正文编辑器可展开实时预览，停止输入 300ms 后刷新；切换格式、收起预览或离开编辑器时取消旧请求。预览需要 active 会话，沿用站点请求来源校验与限流。

Markdown 中的 LaTeX 支持 `$...$` / `\(...\)` 行内公式，以及 `$$...$$` / `\[...\]` 独立公式（可多行）。服务端先提取公式 token，再渲染并净化 Markdown，避免下划线、反斜杠或换行破坏 TeX；沿用已部署的空 `<span class="math-inline|math-block" data-tex="…">` 容器，将 TeX 作为转义属性交给浏览器现有 KaTeX（`trust: false`）渲染。代码块、行内代码和转义美元符号保持字面文本。已存储的正文 HTML 无需迁移，客户端同时兼容旧的裸文本公式和早期 `.math-source` 节点。

## 用户与互动

### Feedback `/api/feedback`、`/api/admin/feedback/*`、`/api/agent/v1`

Feedback 项目、成员、条目、评论、Agent Key 和备份端点均由 Pydantic contract 生成
OpenAPI。字段继续使用既有 camelCase wire 名称；`type` 固定为 `bug | suggestion`，
`urgency` 固定为 `urgent | normal`，`status` 固定为 `open | done | expired`，Agent Key
角色固定为 `read | write`。条目和评论的 `author` 明确允许为 null，以覆盖历史用户记录
缺失时的兼容行为。前端 `Feedback*` 类型直接引用生成 schema。

### 私人发帖草稿 `/api/drafts`

所有端点要求 active 会话；草稿只允许作者访问，管理员也不能查看或修改他人的草稿（与不存在统一返回 404）。

| 端点 | 说明 |
|------|------|
| `GET /api/drafts?cursor=&limit=20` | 本人草稿列表，按创建 ID 倒序，`limit` 为 1–50；返回 `{ items, nextCursor }`。摘要包含 `id` / `title` / `preview` / `boardSlug` / `bodyFormat` / `attachmentCount` / `createdAt` / `updatedAt` |
| `POST /api/drafts` | 新建，返回 201 及草稿详情 |
| `GET /api/drafts/:id` | 详情，包含完整 `bodyMarkdown` 和带新签名下载 URL 的 `attachments` |
| `PUT /api/drafts/:id` | 替换保存内容，返回更新后的详情；重复保存沿用同一个草稿 ID |
| `DELETE /api/drafts/:id` | 删除，返回 `{ ok: true }`；解除附件引用，之后由常规孤儿回收处理未发布附件 |

保存请求：`title`（0–100 字符）、`bodyMarkdown`（0–40000 字符）、`bodyFormat`（`text` 默认或 `markdown`）、`boardSlug`（可空，非空时必须是本人可见板块）、`attachmentIds`（最多 10 个已上传、本人拥有、尚未发布的附件）。标题和正文保留原始空白；允许仅写标题、未选择板块等未完成状态。保存不渲染或发布正文，不写讨论、通知或 outbox 事件。

附件一次只能归属一篇草稿。被草稿引用的已上传附件不受 7 天孤儿回收期限影响；读取详情重新生成下载 URL。普通发帖不能占用其他草稿的附件。从草稿发布时，`POST /api/discussions` 仍须发送当前编辑的完整发帖字段及 `draftId`：校验、创建讨论、绑定附件、写事件和删除草稿在同一请求事务中提交。任何失败都会保留已保存草稿；重复使用已消费的 `draftId` 返回 404，避免重复创建帖子。发布权限按发布时的板块策略重新校验。

创建与编辑直接发布；草稿消费、附件绑定、内容及 outbox 事件仍在同一事务内提交或回滚。

前端个人下拉菜单的“草稿”进入 `/drafts`，详情编辑在 `/drafts/:id`，新帖仍在 `/post`。保存成功显示提示；加载、保存或发布失败显示错误并允许重试。原板块不可用时保留文字与附件并要求重新选择板块后发布。

Draft HTTP contract 由 Pydantic 模型生成到 `docs/openapi.json`，前端的
`DraftInput`、`DraftSummary` 和 `DraftDetail` 均直接引用生成类型。Python 内部使用
`DraftID`、`AttachmentID`、`DraftBodyFormat` 及不可变 dataclass；SQLAlchemy
`RowMapping` 只在 `drafts/repository.py` 出现。

| 端点 | 说明 |
|------|------|
| `GET /users/:username` | 公开主页 |
| `PATCH /me/profile` ⚡ | 更新资料。`bio` 可为空或纯空白（归一为空串），用于清空简介；`displayName`/`username` 仍须非空 |
| `POST/DELETE /users/:username/follow` ⚡ | 关注/取消用户 |

编辑期间内容版本改变返回 `409/CONFLICT`。草稿发布在最终写入事务内复核草稿、版块权限、账号状态和角色。

## 板块

板块、用户资料、举报/封禁/恢复和后台管理端点均声明 Pydantic response model；字段通过
camelCase alias 保持现有 wire contract。`BoardVisibility`、`PostingPolicy`、账号状态/角色、
举报状态与目标类型等枚举在 Python 中使用 PascalCase 成员名，实际 JSON 值不变。前端
`BoardSummary`、`UserDTO`、`PublicProfile`、`ReportDTO`、`ModerationAction`、`AdminUser`
及删除内容 DTO 全部引用 `src/lib/generated/openapi.ts`。

| 端点 | 说明 |
|------|------|
| `GET /boards` | 可见板块列表（按 visibility） |
| `GET/POST /boards` / `PATCH/DELETE /boards/:slug` 🔒 | 板块 CRUD（软删） |
| `GET /boards/:slug/discussions` | 板块帖子流 |
| `POST/DELETE /boards/:slug/join` / `leave` ⚡ | 加入/退出 |
| `GET/PATCH /boards/:slug/members` / `members/:userId` 🔒 | 成员管理 |

## 通知

通知 HTTP contract 由后端 Pydantic 模型生成，并归档在 `docs/openapi.json`。前端通过
`pnpm generate:api` 从该文件生成 `src/lib/generated/openapi.ts`；提交前运行
`pnpm check:api` 可检测生成类型漂移。内部枚举成员名使用 PascalCase；HTTP wire 通知类型固定为
`reply | mention | follow | system | moderation | ban`。

| 端点 | 说明 |
|------|------|
| `GET /notifications?unreadOnly=&cursor=` ⚡ | 通知列表（降序，含 `unreadCount`） |
| `GET /notifications/unread-count` ⚡ | 未读数 |
| `POST /notifications/:id/read` ⚡ | 标记已读 |
| `POST /notifications/read-all` ⚡ | 全部已读 |

已删除的讨论不能继续回复。回复与 mention 通知在内容提交时生成，发送、读取及未读计数仍校验删除状态与收件人的板块权限。

## 搜索 / 实时 / 在线

| 端点 | 说明 |
|------|------|
| `GET /search?q=&board=` | 帖子搜索。SQLite 无 FTS5，当前为 **LIKE 子串匹配**（中文逐字符命中）；`total` 给出命中数 |
| `GET /events` ⚡ | **SSE**：连接即收 `event: connected`（`{ userId, at }`）；后续收 `event: notification.created`（`{ userId, seq }`，仅本用户）。队列溢出时收 `event: gap`（`{ seq }`），客户端重新拉 `/notifications` 兜底 |
| `POST /presence/heartbeat` ⚡ | 在线心跳（TTL 60s，客户端每 45s 上报） |
| `GET /presence` | 在线用户列表 |

## 附件（presigned 语义）

| 端点 | 说明 |
|------|------|
| `POST /attachments/presign` ⚡ | 申请上传会话，返回 `objectKey` + 签名 URL |
| `PUT /attachments/upload/:key` ⚡ | 直传二进制（`addContentTypeParser("*")` 流式） |
| `GET /attachments/serve/:key` | 带签名下载 |
| `GET/DELETE /attachments/:id` ⚡ | 元数据/删除 |

上传/下载 URL 带 HMAC 签名（`?expires&sig`），语义对齐 S3 presigned URL。dev 为本地磁盘实现。

签发和下载检查父讨论的删除状态与当前板块权限；旧签名不能绕过权限变化。响应使用 `Cache-Control: private, no-store`。

## 治理（mod 以上）🔒

| 端点 | 权限 |
|------|------|
| `POST /moderation/reports` ⚡ | 任何 active 用户举报 |
| `GET /moderation/reports` 🔒 | 列表（mod/admin） |
| `PATCH /moderation/reports/:id` 🔒 | 更新状态（resolved/dismissed...） |
| `POST /moderation/bans` 🔒 | 封禁（mod/admin），`durationHours` 可选 |
| `DELETE /moderation/bans/:username` 🔒 | 解封（**仅 admin**） |
| `GET /moderation/actions` 🔒 | 审计日志 |
| `POST /moderation/restore` 🔒 | 恢复已软删的讨论/回复 |

## 反馈 `/api/feedback`（会员制，程序员/admin 可管理）

| 端点 | 权限 |
|------|------|
| `GET /feedback/projects/mine` | active 用户，返回所属项目（admin 全部） |
| `GET /feedback?projectId=` | 项目成员；返回 `{ items, canManage }` |
| `POST /feedback` | 项目成员提交 `{ projectId, title, detail?, type, urgency? }` |
| `PATCH /feedback/:id` | 作者本人 / 程序员 / admin |
| `DELETE /feedback/:id` | 作者本人 / 程序员 / admin |
| `POST /feedback/:id/status` | 程序员 / admin，`{ status: done\|expired\|open }` |
| `GET/POST/PATCH/DELETE /feedback/projects...` | **仅 admin**（项目 CRUD） |
| `PUT /feedback/projects/:id/members` | **仅 admin** `{ members: [{ userId, isProgrammer }] }` |
| `GET/POST/PUT/DELETE /admin/feedback/keys` | **仅 admin**（Agent 密钥，POST 返回完整 key 一次） |
| `GET/POST /admin/feedback/backups...` | **仅 admin**（备份 create/list/restore/settings） |

## 任务 `/api/tasks`（仅 admin，论坛内任务页）

任务与嵌套评论使用 Pydantic HTTP contracts；SQLAlchemy 行只在
`task_repository.py` 中转换为不可变 records。`TaskPriority`、`TaskStatus` 使用
PascalCase 枚举成员名，wire/storage 值仍为 `urgent|normal` 与 `open|done`。

| 端点 | 权限 |
|------|------|
| `GET /tasks` | **仅 admin**，返回 `{ items, categories, canWrite }`（`canWrite` 恒为 true） |
| `POST /tasks` | **仅 admin** `{ category?, title, notes?, priority? }` |
| `PATCH /tasks/:id` | **仅 admin**（分组/标题/备注/优先级） |
| `POST /tasks/:id/status` | **仅 admin** `{ status: open\|done }` |
| `DELETE /tasks/:id` | **仅 admin**（同时删除其评论） |
| `GET/POST /tasks/:id/comments` | **仅 admin**（嵌套评论，`parentCommentId`） |
| `PATCH/DELETE /tasks/comments/:comment_id` | **仅 admin** |

> 任务原为独立公开看板；现收归论坛内 `/tasks` 页面，仅管理员可见。授权不经 `can()`，直接 `deps.require_admin`。

## Agent API `/api/agent/v1`（api-key，`X-Api-Key` 头）

| 端点 | 权限 |
|------|------|
| `GET /agent/v1`、`GET /agent/v1/README` | 免 key（超媒体索引 / 说明书） |
| `GET /agent/v1/projects` | 任意 key（按 `projectIds` 过滤，空=全部） |
| `GET /agent/v1/tasks?projectId&status&type` | 任意 key，返回列表 + open/done/expired 汇总 |
| `GET /agent/v1/tasks/:id` | 任意 key（需在授权项目内） |
| `POST /agent/v1/tasks/:id/status` | **write 角色**，`{ status: done\|open }` |

## 分页游标示例

```http
GET /api/discussions?feed=latest&limit=10
→ { "items": [...], "nextCursor": "1788022289371_11" }

GET /api/discussions?feed=latest&limit=10&cursor=1788022289371_11
→ { "items": [...], "nextCursor": null }
```

通知游标为通知 id（`nextCursor: 4`）。

## 系统与搜索 contract

`GET /api/health`、用户关注、在线人数与 `GET /api/search` 均声明 Pydantic response model，
前端的 `FollowResponse`、`Presence` 和 `SearchResult` 直接引用 OpenAPI 生成类型。搜索结果包含
渲染讨论列表所需的完整字段；内容审核状态字段已移除。

## 内容审核移除

自动审核、审核队列、留存库和复审接口已移除，原 `/api/admin/moderation/*` 路由返回 404。内容 DTO 不再包含 `moderationStatus`，用户 DTO 不再包含 `profilePending`。账号身份验证、举报、人工封禁、恢复和治理审计接口保持不变。
