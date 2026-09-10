# Samryetha REST API 契约

完整 OpenAPI 3.0 文档见 **`docs/openapi.json`**（从 `/docs/json` 实时导出归档，共 46 个端点），本地调试可开 `pnpm dev` 后访问 `/docs`（Swagger UI）。

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

## 内容

| 端点 | 说明 |
|------|------|
| `GET /discussions?feed=latest\|followed&board=&cursor=` | 帖子流。`followed` = 关注的用户发的 + 关注的讨论（无任何关注返回空，**不退化为全量**） |
| `POST /discussions` ⚡ | 发帖。body: `boardSlug` / `title` / `bodyMarkdown`（Markdown，服务端渲染净化） |
| `GET /discussions/:id` | 详情（软删返回 404） |
| `PATCH /discussions/:id` 🔒 | 编辑（作者/全局mod） |
| `DELETE /discussions/:id` 🔒 | 软删 |
| `POST /discussions/:id/save` / `DELETE .../save` ⚡ | 收藏/取消 |
| `POST /discussions/:id/follow` / `DELETE .../follow` ⚡ | 关注/取消 |
| `POST /discussions/:id/pin` / `lock` 🔒 | 置顶/锁定（mod） |
| `GET/POST /discussions/:id/replies` ⚡ | 回复列表/发布（`parentReplyId` 支持线程） |
| `PATCH/DELETE /replies/:id` 🔒 | 编辑/软删回复 |

## 用户与互动

| 端点 | 说明 |
|------|------|
| `GET /users/:username` | 公开主页 |
| `PATCH /me/profile` ⚡ | 更新资料 |
| `POST/DELETE /users/:username/follow` ⚡ | 关注/取消用户 |

## 板块

| 端点 | 说明 |
|------|------|
| `GET /boards` | 可见板块列表（按 visibility） |
| `GET/POST /boards` / `PATCH/DELETE /boards/:slug` 🔒 | 板块 CRUD（软删） |
| `GET /boards/:slug/discussions` | 板块帖子流 |
| `POST/DELETE /boards/:slug/join` / `leave` ⚡ | 加入/退出 |
| `GET/PATCH /boards/:slug/members` / `members/:userId` 🔒 | 成员管理 |

## 通知

| 端点 | 说明 |
|------|------|
| `GET /notifications?unreadOnly=&cursor=` ⚡ | 通知列表（降序，含 `unreadCount`） |
| `GET /notifications/unread-count` ⚡ | 未读数 |
| `POST /notifications/:id/read` ⚡ | 标记已读 |
| `POST /notifications/read-all` ⚡ | 全部已读 |

## 搜索 / 实时 / 在线

| 端点 | 说明 |
|------|------|
| `GET /search?q=&board=` | 帖子搜索。SQLite 无 FTS5，当前为 **LIKE 子串匹配**（中文逐字符命中）；`total` 给出命中数 |
| `GET /events` ⚡ | **SSE**：连接即收 `event: connected`；后续收 `event: notification.created`（仅本用户）。客户端断线重连后拉 `/notifications` 兜底 |
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
