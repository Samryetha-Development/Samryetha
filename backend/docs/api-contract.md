# Samryetha REST API 契约

完整 OpenAPI 3.0 文档见 **`docs/openapi.json`**（由 `uv run python scripts/export_openapi.py` 导出），本地后端运行时可访问 `/docs`（Swagger UI）。翻译提交 `/api/i18n/*` 已移除。

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

正文编辑器可展开实时预览，停止输入 300ms 后刷新；切换格式、收起预览或离开编辑器时取消旧请求。预览需要 active 会话，沿用站点请求来源校验与限流。

Markdown 中的 LaTeX 支持 `$...$` / `\(...\)` 行内公式，以及 `$$...$$` / `\[...\]` 独立公式（可多行）。服务端先提取公式 token，再渲染并净化 Markdown，避免下划线、反斜杠或换行破坏 TeX；沿用已部署的空 `<span class="math-inline|math-block" data-tex="…">` 容器，将 TeX 作为转义属性交给浏览器现有 KaTeX（`trust: false`）渲染。代码块、行内代码和转义美元符号保持字面文本。已存储的正文 HTML 无需迁移，客户端同时兼容旧的裸文本公式和早期 `.math-source` 节点。

## 用户与互动

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

自动审核判为 `pending` 或 `rejected` 时，创建请求仍返回 201 和刚提交的讨论详情，草稿与附件也完成上述事务转换；后续读取继续遵守审核可见性规则。若创建请求在审核后失败，审核队列、讨论、附件绑定和草稿删除一起回滚。

前端个人下拉菜单的“草稿”进入 `/drafts`，详情编辑在 `/drafts/:id`，新帖仍在 `/post`。保存成功显示提示；加载、保存或发布失败显示错误并允许重试。原板块不可用时保留文字与附件并要求重新选择板块后发布。

| 端点 | 说明 |
|------|------|
| `GET /users/:username` | 公开主页 |
| `PATCH /me/profile` ⚡ | 更新资料。`bio` 可为空或纯空白（归一为空串），用于清空简介；`displayName`/`username` 仍须非空 |
| `POST/DELETE /users/:username/follow` ⚡ | 关注/取消用户 |

自动审核在首个数据库写入前完成模型判定。讨论、回复、资料编辑期间若内容版本或审核
状态改变，返回 `409/CONFLICT`；调用方应重新读取后再提交。草稿发布也会在最终写入
事务中复核草稿及版块权限；审核期间被修改的草稿不会被旧发布请求消耗。审核等待
期间账号状态或角色变化也返回 `409/CONFLICT`，不会沿用请求开始时的写入权限。

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

讨论被封后，普通作者不能继续向该讨论创建回复；待审父回复也须具备阅读权限。
回复及 mention 通知在内容和父帖批准后才产生，并在发送、读取、未读计数时复核
审核状态与收件人的版块权限。待审/被封后的普通通知不会暴露隐藏标题；审核结果
通知仍可正常送达作者。

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

签发和下载都会检查父讨论当前的审核状态。`rejected` 附件只有管理员能签发和下载；
`pending` 下载须带有能读取父讨论的有效会话。封禁前的签名也不能绕过新状态。
创建/编辑接口不给无权访问附件的作者回显新下载链接。正常已批准附件保留带签匿名
下载行为；下载响应使用 `Cache-Control: private, no-store`，避免受限文件被缓存外传。
审核封禁不会把关联文件变为 orphaned，原文件留存供管理员复核；人工放行后恢复
正常附件访问。

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

### 审核队列 `/api/admin/moderation`

**发布即审核**：规则层确定性命中 → 直接封禁（不调模型）；**其余全部交给模型**，
模型结论直接生效（`allow` 公开 / `review` 转人工 / `risk≥85` 直接封禁）。
只有"已经确定"的结论才短路——规则层权重全是 100，没有中间态，若"零信号直接放行"，
变体写法会连模型都不过。机器封禁一律进队列并标 `resolution=blocked_by_machine`，
管理员随时可维持或推翻。不设确认窗口。
详见 `architecture.md` 的「自动审核」。

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET /admin/moderation/queue` | mod/admin | 待办列表。`status=pending\|approved\|rejected\|all`；`type=` 内容类型；`resolution=awaiting\|published_by_ai\|published_by_human\|blocked\|blocked_by_machine`。返回项含 `holdUntil`、`resolution`、`resolvedByAi`、`overturned`、`recheck`、`awaitingHuman`/`needsUphold`/`needsRelease`，以及 `counts`（含 `awaiting`/`aiPublished`/`aiBlocked`/`blocked`）。**封禁条目的 `excerpt` 对非管理员返回空串**并置 `excerptRestricted=true`（失败原文仅管理员可访问） |
| `POST /admin/moderation/queue/:id/approve` | mod/admin | 放行（窗口内定案 / 追认 AI 放行 / 推翻 AI 封禁）。`{ note? }`。**封禁条目仅管理员**（否则 403） |
| `POST /admin/moderation/queue/:id/reject` | mod/admin | 封禁（窗口内驳回 / 推翻 AI 放行）。`{ note? }`。**封禁条目仅管理员** |
| `POST /admin/moderation/finalize` | **仅 admin** | 手动催一轮逾期复审（运维/排障），返回本轮落定条数；单条失败会跳过而非整批失败 |
| `GET /admin/moderation/retained` | **仅 admin** | 审核失败内容的留存库（含正文全文与复审记录）；这些原文对版主与作者都不可见 |

队列按 `score DESC, id DESC` 分页，`nextCursor` 为 `"score:id"` 字符串；部署过渡期仍接受旧的数字 ID 游标，无效游标返回 400。列表与待办计数只包含当前版本，`counts.blocked` 统计全部失败留存版本。

编辑后重新送审会生成独立记录，旧版本仅保留证据、不能再操作当前内容；对旧版本执行 approve/reject 返回 400。留存库继续返回所有被封禁版本的送审快照。

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

## 文件服务 `/api/files`（面向新生的资料库）

读接口按可见性在 SQL 层过滤；**不可见与不存在一律 404**，不泄漏资源是否存在。
写接口要求 `active` 用户；分类管理要求 `admin`。授权走 `authz.can()` 的 `FILE_*` 能力，不另开角色。

| 端点 | 权限 |
|------|------|
| `GET /files/config` | 公开。一次性下发分类、标签云、页头统计、扩展名白名单、体积上限、kind/visibility/sort 枚举、标签上限 |
| `GET /files/categories` | 公开。分类列表（含各类可见资料数） |
| `POST /files/categories` | **仅 admin** `{ slug, name, description?, kind?, sortOrder? }` |
| `PATCH /files/categories/:id` | **仅 admin**（名称/说明/kind/排序） |
| `DELETE /files/categories/:id` | **仅 admin**。内建分类（`is_system=1`）与**非空分类**均拒绝（409） |
| `GET /files/resources` | 按可见性过滤。参数 `category`(slug) `kind` `tag` `q` `sort` `status` `featured` `uploaderId` `page` `pageSize`；`sort` ∈ `latest`/`downloads`/`favorites`/`rating`/`name`，`pageSize` 超上限**夹紧**到 50 而非报错 |
| `GET /files/resources/:id` | 按可见性过滤，返回 `{ ..., descriptionMarkdown, can:{update,delete}, sha256? }` |
| `POST /files/resources/presign` | **active**。`{ filename, mimeType, sizeBytes }` → `{ objectKey, uploadUrl, expires, sig, expiresAt, contentType }`。**不建数据库行**（元数据尚未收集） |
| `PUT /files/upload/:uploaderId/:objectKey` | 签名即凭证。签名串为 `…/{objectKey}@size={size}`，**绑定上传者 + 对象键 + 声明体积**；会话身份必须与签名内上传者一致，且该用户仍为 active。**对象一旦写完即不可变**：完成步骤用 `os.link` 原子认领目标路径，已存在则返回 409，因此上传票据虽在 15 分钟内有效却**不可重放覆盖**——否则作者可在发布后用同一地址把内容换成等长的另一份，而标题、版本号、下载量与已存 SHA-256 全部仍描述旧内容。替换内容必须走新对象 |
| `POST /files/resources` | **active**。`{ objectKey, expires, sig, sizeBytes, categoryId, title, descriptionMarkdown?, tags?, visibility?, originalFilename?, mimeType?, sha256? }`。创建前三重复核：① 上传签名（防篡改体积、防冒用他人对象键）；② 对象**确实已落盘**；③ 落盘体积与声明一致。②③ 是必需的——只验签名时，用户可以只 presign、根本不 PUT 字节就建条目，列表里会出现永远下载不到的资料 |
| `PATCH /files/resources/:id` | 上传者本人或 admin（标题/说明/标签/分类/可见性/status） |
| `DELETE /files/resources/:id` | 上传者本人或 admin（软删除；磁盘对象留待运维脚本回收） |
| `GET /files/resources/:id/download` | 按可见性。返回 `{ downloadUrl, originalFilename, ... }`；**`?preview=true` 时取地址但不计数**，避免在线预览污染下载量 |
| `GET /files/serve/:objectKey` | 签名 + **可见性复核**（两道独立校验：签名只证明 URL 未过期，资源可能已被改成 private 或软删除） |
| `PUT` / `DELETE /files/resources/:id/favorite` | **active**，幂等 |
| `PUT /files/resources/:id/rating` | **active** `{ score: 1..5 }`，一人一票可覆盖 |
| `DELETE /files/resources/:id/rating` | **active**，撤销评分（未评分时为无副作用空操作）。**与设置评分使用同一套可见性校验**：不可见资源一律 404，否则从未获授权的账户只要 DELETE 一下就能读到私有资料的 `ratingAvg`/`ratingCount`，把这条路由变成探测存在性与口碑的接口 |
| `GET /files/favorites` | **active**，我收藏的资料。**同样套用可见性谓词**：收藏是"当时可见"的快照，但授权每次请求重新判定，资料后来改成 private/转待审后必须立刻从收藏列表消失 |
| `GET /files/mine` | **active**，我上传的资料（含待审与归档） |
| 对象键与 URL 编码 | 对象键形如 `{uuid}/{文件名}`；上传/下载 URL 对路径段做百分号编码，而签名始终针对**未编码**的规范形式。实测确认（`backend/.pytmp-verify/probe_decoding.py`）**真实 uvicorn 与 httpx 的 `ASGITransport` 都只对路径解码一次**，因此：未编码的 `#` 必须编码（否则它后面的查询串会被当成 URL fragment），字面 `%20` 编码一次即可原样还原，两者都不会破坏签名校验。**唯一会二次解码的是 Starlette 的 `TestClient`**（它执行 `unquote(url.path)`，而该 path 已被 httpx 解码过一次）——属测试工具假象，**不要据此收紧产品约束**；涉及保留字符文件名的用例请改用 `httpx.ASGITransport` 驱动 |
| 启动时自动执行 | 孤儿对象回收：presign 刻意不建行，因此"申请了上传地址、传了字节、却从未创建资料"的文件没有任何表引用。启动时按「数据库引用差集 + 24 小时保留窗口 + 严格命名规范」三重条件回收，附件对象因同在 `attachments` 表被引用而绝不受影响。测试/运维可调 `app.state.reap_file_orphans(older_than_ms)` |

> 下载计数规则：`download_count` 只在 (resource, user) 首次下载时 +1；匿名下载按 (resource, ip, 24 小时) 去重；
> `file_downloads` 明细始终全量记录（含重复），供审计与防刷分析。

## 分页游标示例

```http
GET /api/discussions?feed=latest&limit=10
→ { "items": [...], "nextCursor": "1788022289371_11" }

GET /api/discussions?feed=latest&limit=10&cursor=1788022289371_11
→ { "items": [...], "nextCursor": null }
```

通知游标为通知 id（`nextCursor: 4`）。
