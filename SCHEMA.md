# Samryetha Schema Inventory

> 当前仓库中所有主要数据形状的现状地图。本文描述的是代码当前实际使用的 schema，
> 不是目标架构，也不表示这些形状都合理。
>
> 快照日期：2026-10-06  
> 快照分支：`codex/split-services`  
> 当前验证状态：后端 `340 passed`；前端 `pnpm typecheck` / `pnpm build` 通过。

## 1. Schema 分布总览la

仓库里没有单一 schema 真源。目前至少存在六套互相独立的数据描述：

| 层 | 当前真源 | 是否运行时校验 | 当前问题 |
|---|---|---:|---|
| SQLite 物理表 | `backend/src/samryetha/schema.py` | 是 | SQLAlchemy Core Row 在领域 repository 中立即转为 typed record |
| API 请求 | `backend/src/samryetha/routers/*.py` 与领域 `models.py` 的 Pydantic model | 是 | router 映射 typed command/result，不向 service 传裸 `dict` |
| API 响应 | service 返回的手写字典 | 否 | 140 个路由没有一个 `response_model` |
| 前端响应类型 | `frontend/src/lib/api.ts` | 否 | `apiFetch<T>` 只做 `data as T`，与后端没有机械关联 |
| 事务事件/SSE | `emit_event(..., payload={...})` | 否 | payload 是无约束 JSON dict，消费者靠字符串 key |
| 权限 resource | `authz.can(..., resource: Any)` | 否 | dict 被转成 `SimpleNamespace`，字段要求隐藏在分支里 |
| 运行时依赖 | `app.state.*` | 否 | 没有统一容器类型，路由和 worker 动态取属性 |

标记约定：

- `*`：必填。
- `?`：可空或可选；二者在部分现有接口中没有严格区分。
- `ms`：Unix epoch 毫秒整数。
- `JSON text`：数据库中存储为 `TEXT`，应用层自行 `json.loads`。
- `[frontend-only]`：只有前端 TypeScript 声明，后端没有对应 response model。
- `[drift]`：代码中的不同 schema 已经互相矛盾。

## 2. 通用标量、枚举和包络

### 2.1 通用标量

| 名称 | 实际表示 | 备注 |
|---|---|---|
| `Id` | `integer >= 1` | 数据库通常为 SQLite autoincrement integer |
| `TimestampMs` | `integer` | 所有业务时间戳均为 epoch 毫秒；不要当 SQL `DateTime` |
| `BoolInt` | `0 | 1` | SQLite 表中的布尔值 |
| `Cursor` | `string | integer | null` | 不同 feed 使用不同格式，没有统一 cursor 类型 |
| `JsonObject` | `dict / object` | 当前通常没有字段约束 |

### 2.2 当前枚举集合

| 概念 | 当前值 |
|---|---|
| 用户角色 | `student | moderator | admin`；但管理 API 和前端只声明 `student | admin` |
| 用户状态 | `pending | active | banned | deactivated` |
| 板块可见性 | `public | members | private` |
| 发帖策略 | `everyone | members | moderators` |
| 板块成员角色 | `member | moderator` |
| 正文格式 | `markdown | text` |
| 内容审核状态 | `approved | pending | rejected` |
| 举报状态 | `open | in_progress | resolved | dismissed` |
| 机器审核决定 | `allow | review | block` |
| 人工审核状态 | `pending | approved | rejected` |
| 审核 resolution | `blocked_by_machine | blocked | published_by_human | published_by_ai` |
| 附件状态 | `pending | uploaded | attached | orphaned` |
| 通知类型 | `reply | mention | follow | system | moderation | ban` |
| 反馈类型 | `bug | suggestion` |
| 反馈紧急度 | `urgent | normal` |
| 反馈状态 | `open | done | expired` |
| API key 角色 | `read | write` |
| 任务优先级 | `urgent | normal` |
| 任务状态 | `open | done` |
| outbox 状态 | `pending | processing | held | done | failed` |

### 2.3 API 错误包络

后端统一错误外形：

```ts
type ApiErrorEnvelope = {
  error: {
    code: string;
    message: string;
    requestId: string;
    details?: unknown;
  };
};
```

已声明的 `code`：

```text
BAD_REQUEST
AUTH_REQUIRED
SESSION_EXPIRED
INVALID_CREDENTIALS
EMAIL_NOT_VERIFIED
BANNED
FORBIDDEN
NOT_FOUND
METHOD_NOT_ALLOWED
CONFLICT
GONE
PAYLOAD_TOO_LARGE
UNSUPPORTED_MEDIA_TYPE
VALIDATION_ERROR
RATE_LIMITED
TOKEN_INVALID_OR_EXPIRED
EMAIL_ALREADY_VERIFIED
INTERNAL_ERROR
SERVICE_UNAVAILABLE
```

## 3. SQLite 物理 Schema

权威来源：`backend/src/samryetha/schema.py`。当前共有 **36 张表**。

### 3.1 身份与用户

#### `users`

```text
id*                       INTEGER PK
username*                 TEXT UNIQUE
email*                    TEXT UNIQUE
recovery_email?           TEXT
display_name*             TEXT
bio*                      TEXT DEFAULT ''
profile_moderation_status TEXT DEFAULT 'approved'
pending_display_name?     TEXT
pending_bio?              TEXT
password_hash*            TEXT
role*                     TEXT DEFAULT 'student'
status*                   TEXT DEFAULT 'pending'
discriminator?            INTEGER UNIQUE
email_domain?             TEXT
email_verified_at?        TimestampMs
avatar_object_key?        TEXT
last_seen_at?             TimestampMs
settings*                 JSON text DEFAULT '{}'
created_at?               TimestampMs
updated_at?               TimestampMs
deleted_at?               TimestampMs
```

索引：`status`、`deleted_at`。

#### `schools`

```text
id*            INTEGER PK
name*          TEXT
email_domain*  TEXT UNIQUE
is_active*     BoolInt DEFAULT 1
created_at?    TimestampMs
```

#### `sessions`

```text
token_hash*    TEXT PK
user_id*       FK users.id
expires_at?    TimestampMs
ip?            TEXT
user_agent?    TEXT
created_at?    TimestampMs
last_seen_at?  TimestampMs
```

#### `oidc_identities`

```text
id*             INTEGER PK
user_id*        FK users.id
issuer*         TEXT
subject*        TEXT
email_at_link?  TEXT
created_at?     TimestampMs
last_login_at?  TimestampMs
UNIQUE (issuer, subject)
```

#### `oidc_login_transactions`

```text
state_hash*     TEXT PK
nonce*          TEXT
code_verifier*  TEXT
return_to*      TEXT DEFAULT '/'
expires_at?     TimestampMs
created_at?     TimestampMs
```

#### `oidc_claim_tickets`

```text
id*            INTEGER PK
ticket_hash*   TEXT UNIQUE
issuer*        TEXT
subject*       TEXT
email?         TEXT
display_name?  TEXT
attempts*      INTEGER DEFAULT 0
expires_at?    TimestampMs
created_at?    TimestampMs
```

#### `qr_login_tickets`

```text
id*             INTEGER PK
ticket_id_hash* TEXT UNIQUE
secret_hash*    TEXT
status*         TEXT DEFAULT 'pending'
approved_by?    FK users.id
ip?             TEXT
user_agent?     TEXT
expires_at?     TimestampMs
created_at?     TimestampMs
decided_at?     TimestampMs
```

#### `qr_login_confirmation_codes`

```text
id*             INTEGER PK
ticket_id_hash* TEXT
user_id*        FK users.id
code_hash*      TEXT
attempts*       INTEGER DEFAULT 0
expires_at?     TimestampMs
created_at?     TimestampMs
consumed_at?    TimestampMs
```

#### `email_verification_tokens`

```text
id*          INTEGER PK
user_id*     FK users.id UNIQUE
token_hash*  TEXT UNIQUE
expires_at?  TimestampMs
created_at?  TimestampMs
```

#### `password_reset_tokens`

```text
id*          INTEGER PK
user_id*     FK users.id
token_hash*  TEXT UNIQUE
expires_at?  TimestampMs
used_at?     TimestampMs
created_at?  TimestampMs
```

注意：`password_reset_tokens.user_id` 当前只有普通索引，不是唯一约束；旧文档曾把它描述为唯一。

### 3.2 板块、讨论和草稿

#### `boards`

```text
id*                  INTEGER PK
slug*                TEXT UNIQUE
name*                TEXT
description*         TEXT DEFAULT ''
visibility*          public | members | private DEFAULT public
posting_policy*      everyone | members | moderators DEFAULT members
created_by_user_id?  INTEGER
deleted_at?          TimestampMs
deleted_by?          INTEGER
deletion_reason?     TEXT
created_at?          TimestampMs
updated_at?          TimestampMs
```

`created_by_user_id`、`deleted_by` 当前没有声明 FK。

#### `board_members`

```text
board_id*  FK boards.id   PK(part)
user_id*   FK users.id    PK(part)
role*      member | moderator DEFAULT member
joined_at? TimestampMs
```

#### `discussions`

```text
id*                 INTEGER PK
board_id*           FK boards.id
author_id*          FK users.id
title*              TEXT
body_md*            TEXT
body_html?          TEXT
body_format*        markdown | text DEFAULT markdown
reply_count*        INTEGER DEFAULT 0
save_count*         INTEGER DEFAULT 0
is_pinned*          BoolInt DEFAULT 0
is_locked*          BoolInt DEFAULT 0
status*             TEXT DEFAULT open
moderation_status*  approved | pending | rejected DEFAULT approved
last_reply_at?      TimestampMs
deleted_at?         TimestampMs
deleted_by?         INTEGER
deletion_reason?    TEXT
created_at?         TimestampMs
updated_at?         TimestampMs
```

#### `replies`

```text
id*                 INTEGER PK
discussion_id*      FK discussions.id
author_id*          FK users.id
parent_reply_id?    FK replies.id
body_md*            TEXT
body_html?          TEXT
body_format*        markdown | text DEFAULT markdown
moderation_status*  approved | pending | rejected DEFAULT approved
deleted_at?         TimestampMs
deleted_by?         INTEGER
deletion_reason?    TEXT
created_at?         TimestampMs
updated_at?         TimestampMs
```

#### `discussion_drafts`

```text
id*          INTEGER PK
author_id*   FK users.id ON DELETE CASCADE
board_slug?  TEXT
title*       TEXT DEFAULT ''
body_md*     TEXT DEFAULT ''
body_format* markdown | text DEFAULT text
created_at?  TimestampMs
updated_at?  TimestampMs
```

#### `attachments`

```text
id*                 INTEGER PK
uploader_id*        FK users.id
discussion_id?      FK discussions.id
object_key*         TEXT UNIQUE
original_filename*  TEXT
mime_type*          TEXT
size_bytes*         INTEGER
sha256?             TEXT
state*              pending | uploaded | attached | orphaned DEFAULT pending
created_at?         TimestampMs
```

#### `draft_attachments`

```text
attachment_id*  FK attachments.id PK
draft_id*       FK discussion_drafts.id
```

### 3.3 关注、收藏和通知

#### `discussion_saves`

```text
user_id*        FK users.id       PK(part)
discussion_id*  FK discussions.id PK(part)
created_at?     TimestampMs
```

#### `discussion_follows`

```text
user_id*        FK users.id       PK(part)
discussion_id*  FK discussions.id PK(part)
created_at?     TimestampMs
```

#### `user_follows`

```text
follower_id*  FK users.id PK(part)
followee_id*  FK users.id PK(part)
created_at?   TimestampMs
CHECK follower_id <> followee_id
```

#### `notifications`

```text
id*             INTEGER PK
user_id*        FK users.id
actor_user_id?  FK users.id
type*           reply | mention | follow | system | moderation | ban
discussion_id?  FK discussions.id
reply_id?       FK replies.id
body?           TEXT
source_event_id? INTEGER
is_read*        BoolInt DEFAULT 0
read_at?        TimestampMs
created_at?     TimestampMs
UNIQUE (user_id, source_event_id)
```

`source_event_id` 当前没有数据库 FK，只用于 outbox 幂等。

### 3.4 私信

#### `conversations`

```text
id*              INTEGER PK
user_a_id*       FK users.id
user_b_id*       FK users.id
last_message_at? TimestampMs
created_at?      TimestampMs
UNIQUE (user_a_id, user_b_id)
```

用户对的规范顺序由业务代码保证，数据库只保证给定顺序唯一。

#### `direct_messages`

```text
id*                 INTEGER PK
conversation_id*    FK conversations.id
sender_id*          FK users.id
body*               TEXT
source*             TEXT DEFAULT user
moderation_status*  approved | pending | rejected DEFAULT approved
read_at?             TimestampMs
created_at?          TimestampMs
```

### 3.5 举报、封禁和内容审核

#### `reports`

```text
id*                INTEGER PK
reporter_user_id*  FK users.id
reportable_type*   discussion | reply | user
reportable_id*     INTEGER
reason?            TEXT
status*            open | in_progress | resolved | dismissed DEFAULT open
created_at?        TimestampMs
```

`reportable_id` 是多态 ID，没有数据库 FK。

#### `moderation_actions`

```text
id*             INTEGER PK
actor_user_id*  FK users.id
action*         TEXT
target_type*    TEXT
target_id*      INTEGER
reason?         TEXT
created_at?     TimestampMs
```

`target_type + target_id` 是多态引用，没有数据库 FK。

#### `moderation_queue`

```text
id*             INTEGER PK
content_type*   discussion | reply | profile | message | attachment
content_id*     INTEGER
author_id*      FK users.id
excerpt*        TEXT DEFAULT ''
decision*       allow | review | block DEFAULT review
score*          INTEGER DEFAULT 0
signals*        JSON text DEFAULT '{}'
review_state*   pending | approved | rejected DEFAULT pending
reviewer_id?    FK users.id
review_note?    TEXT
reviewed_at?    TimestampMs
created_at?     TimestampMs
hold_until?     TimestampMs
resolution?     blocked_by_machine | blocked | published_by_human | published_by_ai
resolved_at?    TimestampMs
recheck*        JSON text DEFAULT ''
submitted_text* TEXT DEFAULT ''
superseded_at?  TimestampMs
overturned*     BoolInt DEFAULT 0
```

`content_type + content_id` 是多态引用，没有数据库 FK。

#### `bans`

```text
id*                INTEGER PK
user_id*           FK users.id
banned_by_user_id* FK users.id
reason?            TEXT
banned_until?      TimestampMs
is_active*         BoolInt DEFAULT 1
created_at?        TimestampMs
```

### 3.6 Transactional outbox

#### `outbox_events`

```text
id*             INTEGER PK
event_type*     TEXT
aggregate_type? TEXT
aggregate_id?   TEXT
payload*        JSON text
status*         pending | processing | held | done | failed DEFAULT pending
attempts*       INTEGER DEFAULT 0
available_at?   TimestampMs
created_at?     TimestampMs
processed_at?   TimestampMs
processing_at?  TimestampMs
```

关键索引：

- `(status, available_at, id)`：worker 轮询。
- `(aggregate_type, aggregate_id, event_type)`：按聚合定位和幂等补发。

### 3.7 Feedback

#### `feedback_projects`

```text
id*                  INTEGER PK
name*                TEXT
description*         TEXT DEFAULT ''
created_by_user_id?  FK users.id
deleted_at?          TimestampMs
deleted_by?          INTEGER
deletion_reason?     TEXT
created_at?          TimestampMs
updated_at?          TimestampMs
```

#### `feedback_project_members`

```text
project_id*    FK feedback_projects.id PK(part)
user_id*       FK users.id              PK(part)
is_programmer* BoolInt DEFAULT 0
joined_at?     TimestampMs
```

#### `feedback_items`

```text
id*          INTEGER PK
project_id*  FK feedback_projects.id
author_id*   FK users.id
seq*         INTEGER
title*       TEXT
detail*      TEXT DEFAULT ''
type*        bug | suggestion
urgency*     urgent | normal DEFAULT normal
status*      open | done | expired DEFAULT open
closed_at?   TimestampMs
edited_at?   TimestampMs
deleted_at?  TimestampMs
deleted_by?  INTEGER
deletion_reason? TEXT
created_at?  TimestampMs
updated_at?  TimestampMs
UNIQUE (project_id, seq)
```

#### `feedback_comments`

```text
id*                INTEGER PK
item_id*           FK feedback_items.id
author_id*         FK users.id
parent_comment_id? FK feedback_comments.id
body*              TEXT
deleted_at?        TimestampMs
deleted_by?        INTEGER
deletion_reason?   TEXT
created_at?        TimestampMs
updated_at?        TimestampMs
```

#### `feedback_api_keys`

```text
id*           INTEGER PK
name*         TEXT
key_hash*     TEXT UNIQUE
key_prefix*   TEXT
role*         read | write DEFAULT read
project_ids*  JSON text DEFAULT '[]'
enabled*      BoolInt DEFAULT 1
last_used_at? TimestampMs
created_at?   TimestampMs
```

### 3.8 Tasks

#### `tasks`

```text
id*         INTEGER PK
author_id*  FK users.id
category*   TEXT DEFAULT General
title*      TEXT
notes*      TEXT DEFAULT ''
priority*   urgent | normal DEFAULT normal
status*     open | done DEFAULT open
done_at?    TimestampMs
created_at? TimestampMs
updated_at? TimestampMs
```

#### `task_comments`

```text
id*                INTEGER PK
task_id*           FK tasks.id
author_id*         FK users.id
parent_comment_id? FK task_comments.id
body*              TEXT
deleted_at?        TimestampMs
deleted_by?        INTEGER
deletion_reason?   TEXT
created_at?        TimestampMs
updated_at?        TimestampMs
```

### 3.9 应用设置

#### `app_settings`

```text
key*    TEXT PK
value*  JSON text
```

已知 key 包括反馈备份配置和待恢复标记，但数据库不约束 key 或 value 形状。

## 4. API 请求 Schema

权威来源：各 `routers/*.py` 中的 Pydantic `BaseModel`。这里保留 wire 使用的 camelCase。

### 4.1 Auth / OIDC / QR

```ts
type RegisterBody = { username: string; password: string };
type LoginBody = { username: string; password: string };
type ChangePasswordBody = { currentPassword: string; newPassword: string };
type ForgotPasswordBody = { username: string; recoveryEmail: string };
type ResetPasswordBody = { token: string; newPassword: string };

type OidcStartRequest = { returnTo?: string | null };
type OidcCompleteRequest = {
  code?: string | null;
  state?: string | null;
  error?: string | null;
};

type ClaimBody = { ticket: string; username: string; password: string };
type ClaimNewBody = { ticket: string };
type EmergencyLoginBody = { username: string; token: string };

type QrDecideBody = { ticket_id: string; code?: string | null };
type QrExchangeBody = { ticket_id: string; secret: string };
```

注意：QR 请求使用 snake_case，而大部分论坛请求使用 camelCase。

### 4.2 用户与板块

```ts
type ProfileBody = {
  displayName?: string | null;
  username?: string | null;
  recoveryEmail?: string | null;
  bio?: string | null;
  avatarObjectKey?: string | null;
  settings?: Record<string, unknown> | null;
};

type BoardBody = {
  name: string;
  slug: string;
  description?: string | null;
  visibility?: "public" | "members" | "private" | null;
  postingPolicy?: "everyone" | "members" | "moderators" | null;
};

type BoardPatch = Partial<Omit<BoardBody, "slug">>;
type DeleteBoardBody = { reason?: string | null };
type MemberRoleBody = { role: "member" | "moderator" };
```

### 4.3 Discussion / Reply / Draft / Attachment

```ts
type CreateDiscussionBody = {
  boardSlug: string;
  title?: string | null;
  bodyMarkdown: string;
  bodyFormat?: "markdown" | "text";
  attachmentIds?: number[] | null;
  draftId?: number | null;
};

type UpdateDiscussionBody = {
  title?: string | null;
  bodyMarkdown?: string | null;
  bodyFormat?: string | null;
};

type DeleteDiscussionBody = { reason?: string | null };
type PreviewBody = { bodyMarkdown: string; bodyFormat?: "markdown" | "text" };

type CreateReplyBody = {
  bodyMarkdown: string;
  bodyFormat?: "markdown" | "text";
  parentReplyId?: number | null;
};

type UpdateReplyBody = {
  bodyMarkdown: string;
  bodyFormat?: string | null;
};

type SaveDraftBody = {
  boardSlug?: string | null;
  title?: string;
  bodyMarkdown?: string;
  bodyFormat?: "markdown" | "text";
  attachmentIds?: number[];
};

type PresignBody = {
  filename: string;
  mimeType: string;
  sizeBytes: number;
};
```

### 4.4 私信、举报和管理

```ts
type SendBody = { username: string; body: string };

type CreateReportBody = {
  reportableType: "discussion" | "reply" | "user";
  reportableId: number;
  reason: string;
};

type ResolveReportBody = {
  status: "open" | "in_progress" | "resolved" | "dismissed";
  action?: string | null;
  reason?: string | null;
};

type BanUserBody = {
  username: string;
  reason?: string | null;
  durationHours?: number | null;
};

type UnbanUserBody = { reason?: string | null };
type RestoreBody = {
  targetType: "discussion" | "reply";
  targetId: number;
  reason?: string | null;
};

type ChangeRoleBody = {
  role: "student" | "admin";
  reason?: string | null;
};

type ChangeStatusBody = {
  status: "active" | "deactivated";
  reason?: string | null;
};

type DecideBody = { note?: string | null };
```

### 4.5 Feedback

```ts
type FeedbackBody = {
  projectId: number;
  title: string;
  detail?: string | null;
  type: "bug" | "suggestion";
  urgency?: "urgent" | "normal" | null;
};

type FeedbackPatch = {
  title?: string | null;
  detail?: string | null;
  type?: "bug" | "suggestion" | null;
  urgency?: "urgent" | "normal" | null;
};

type FeedbackStatusBody = { status: "open" | "done" | "expired" };
type FeedbackCommentBody = { body: string; parentCommentId?: number | null };
type FeedbackCommentPatch = { body: string };

type ProjectBody = { name: string; description?: string | null };
type ProjectPatch = { name?: string | null; description?: string | null };
type MemberRow = { userId: number; isProgrammer: boolean };
type MembersBody = { members: MemberRow[] };

type KeyBody = {
  name: string;
  role: "read" | "write";
  projectIds?: number[];
};
type KeyEnabledBody = { enabled: boolean };

type RestoreBackupBody = { name: string };
type BackupSettingsBody = { backupCron: string; backupKeep: number };
type AgentStatusBody = { status: "done" | "open" };
```

### 4.6 Tasks

```ts
type TaskCreate = {
  category?: string | null;
  title: string;
  notes?: string | null;
  priority?: "urgent" | "normal" | null;
  status?: "open" | "done" | null;
};

type TaskPatch = {
  category?: string | null;
  title?: string | null;
  notes?: string | null;
  priority?: "urgent" | "normal" | null;
};

type TaskStatusBody = { status: "open" | "done" };
type TaskCommentBody = { body: string; parentCommentId?: number | null };
type TaskCommentPatch = { body: string };
```

## 5. API 响应 Schema

以下类型来自 `frontend/src/lib/api.ts`。它们是当前 UI 期待的形状，**后端不会用
Pydantic response model 验证这些形状**。

### 5.1 公共引用类型

```ts
type AuthorRef = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
};

type BoardRef = { id: number; slug: string; name: string };
type FeedPage<T> = { items: T[]; nextCursor: string | null };
type Presence = { onlineCount: number };
```

### 5.2 用户

```ts
type UserDTO = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
  email: string;
  recoveryEmail: string | null;
  role: "student" | "admin";
  status: "pending" | "active" | "banned" | "deactivated";
  bio: string;
  profilePending: boolean;
  emailVerified: boolean;
  avatarObjectKey: string | null;
  settings: Record<string, unknown>;
  createdAt: TimestampMs;
  lastSeenAt: TimestampMs | null;
};

type PublicProfile = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
  bio: string;
  avatarObjectKey: string | null;
  joinedAt: TimestampMs;
  lastSeenAt: TimestampMs | null;
  stats: {
    discussions: number;
    replies: number;
    followers: number;
    following: number;
  };
  isFollowing: boolean;
};
```

### 5.3 Board / Discussion / Reply / Attachment / Draft

```ts
type BoardSummary = {
  id: number;
  slug: string;
  name: string;
  description: string;
  visibility: "public" | "members" | "private";
  postingPolicy: "everyone" | "members" | "moderators";
  memberCount: number;
  todayActivity: number;
  currentUserRole: "member" | "moderator" | null;
};

type BoardMember = AuthorRef & { role: "member" | "moderator" };

type ThreadSummary = {
  id: number;
  title: string;
  preview: string;
  board: BoardRef;
  author: AuthorRef;
  replyCount: number;
  isPinned: boolean;
  isLocked: boolean;
  moderationStatus: "approved" | "pending" | "rejected";
  createdAt: TimestampMs;
  lastActivityAt: TimestampMs;
};

type DiscussionDetail = ThreadSummary & {
  bodyMarkdown: string;
  bodyHtml: string | null;
  bodyFormat: "markdown" | "text";
  saveCount: number;
  isSaved: boolean;
  isFollowing: boolean;
  can: { update: boolean; delete: boolean };
  attachments?: AttachmentRef[] | null;
};

type ReplyDTO = {
  id: number;
  discussionId: number;
  parentReplyId: number | null;
  author: AuthorRef;
  bodyMarkdown: string;
  bodyHtml: string | null;
  bodyFormat: "markdown" | "text";
  isDeleted: boolean;
  moderationStatus: "approved" | "pending" | "rejected";
  createdAt: TimestampMs;
  updatedAt: TimestampMs;
};

type AttachmentRef = {
  id: number;
  originalFilename: string;
  mimeType: string;
  sizeBytes: number;
  isImage: boolean;
  downloadUrl: string;
};

type DraftSummary = {
  id: number;
  title: string;
  preview: string;
  boardSlug: string | null;
  bodyFormat: "markdown" | "text";
  attachmentCount: number;
  createdAt: TimestampMs;
  updatedAt: TimestampMs;
};

type DraftDetail = Omit<DraftSummary, "preview" | "attachmentCount"> & {
  bodyMarkdown: string;
  attachments: AttachmentRef[];
};
```

### 5.4 通知和私信

```ts
type NotificationDTO = {
  id: number;
  type: string;
  actor: AuthorRef | null;
  body: string | null;
  discussionId: number | null;
  replyId: number | null;
  isRead: boolean;
  createdAt: TimestampMs;
};

type ConversationSummary = {
  id: number;
  otherUser: AuthorRef;
  lastMessage: {
    body: string;
    senderId: number;
    createdAt: TimestampMs;
  } | null;
  unreadCount: number;
  lastMessageAt: TimestampMs;
};

type DirectMessage = {
  id: number;
  senderId: number;
  body: string;
  source: string;
  isRead: boolean;
  createdAt: TimestampMs;
};
```

### 5.5 管理和治理

```ts
type AdminUser = {
  id: number;
  username: string;
  handle: string;
  displayName: string;
  email: string;
  role: "student" | "admin";
  status: "pending" | "active" | "banned" | "deactivated";
  emailVerified: boolean;
  createdAt: TimestampMs;
  lastSeenAt: TimestampMs | null;
  banActive: boolean;
  reportCount: number;
};

type AdminStats = {
  users: {
    total: number;
    pending: number;
    active: number;
    banned: number;
    deactivated: number;
  };
  content: { discussions: number; replies: number; boards: number };
  moderation: { openReports: number; activeBans: number };
  activity: {
    activeToday: number;
    newUsersToday: number;
    newDiscussionsToday: number;
    newRepliesToday: number;
    onlineNow: number;
  };
};

type ReportTarget = {
  type: "discussion" | "reply" | "user";
  id: number;
  title?: string;
  boardSlug?: string;
  username?: string;
  handle?: string;
  displayName?: string;
  discussionId?: number;
};

type ReportDTO = {
  id: number;
  reporter: AuthorRef;
  reportableType: string;
  reportableId: number;
  target?: ReportTarget;
  reason: string | null;
  status: "open" | "in_progress" | "resolved" | "dismissed";
  createdAt: TimestampMs;
};
```

其余治理响应包括：

- `ModerationAction`：审计 actor、action、target、reason、createdAt。
- `DeletedDiscussion`：删除帖子摘要、板块、删除人、删除时间和原因。
- `DeletedReply`：删除回复摘要、父讨论、删除人、删除时间和原因。
- review queue item：目前只存在于后端手写 dict 和页面局部类型中，没有共享模型。

### 5.6 Feedback 响应

```ts
type FeedbackItem = {
  id: number;
  seq: number;
  projectId: number;
  author: AuthorRef;
  title: string;
  detail: string;
  type: "bug" | "suggestion";
  urgency: "urgent" | "normal";
  status: "open" | "done" | "expired";
  closedAt: TimestampMs | null;
  editedAt: TimestampMs | null;
  createdAt: TimestampMs;
  updatedAt: TimestampMs;
};

type FeedbackComment = {
  id: number;
  itemId: number;
  parentCommentId: number | null;
  author: AuthorRef;
  body: string;
  isDeleted: boolean;
  createdAt: TimestampMs;
  updatedAt: TimestampMs;
};

type FeedbackProjectSummary = {
  id: number;
  name: string;
  description: string;
  memberCount: number;
  isProgrammer: boolean;
  createdAt: TimestampMs;
};

type FeedbackProjectMember = {
  userId: number;
  username: string;
  handle: string;
  displayName: string;
  isProgrammer: boolean;
  joinedAt: TimestampMs;
};

type FeedbackProjectAdmin = {
  id: number;
  name: string;
  description: string;
  members: FeedbackProjectMember[];
  createdAt: TimestampMs;
};

type FeedbackApiKey = {
  id: number;
  name: string;
  prefix: string;
  role: "read" | "write";
  projectIds: number[];
  enabled: boolean;
  lastUsedAt: TimestampMs | null;
  createdAt: TimestampMs;
};

type FeedbackBackupInfo = {
  name: string;
  size: number;
  createdAt: TimestampMs;
};

type FeedbackBackupSettings = {
  backupCron: string;
  backupKeep: number;
};
```

### 5.7 Tasks 响应

```ts
type TaskItem = {
  id: number;
  author: AuthorRef;
  category: string;
  title: string;
  notes: string;
  priority: "urgent" | "normal";
  status: "open" | "done";
  doneAt: TimestampMs | null;
  createdAt: TimestampMs;
  updatedAt: TimestampMs;
};

type TaskCategoryCount = {
  category: string;
  open: number;
  done: number;
};

type TaskComment = {
  id: number;
  taskId: number;
  parentCommentId: number | null;
  author: AuthorRef;
  body: string;
  isDeleted: boolean;
  createdAt: TimestampMs;
  updatedAt: TimestampMs;
};
```

## 6. 事务事件和 SSE Schema

### 6.1 Outbox envelope

```ts
type OutboxEvent = {
  id: number;
  eventType: string;
  aggregateType: string | null;
  aggregateId: string | null;
  payload: Record<string, unknown>;
  status: "pending" | "processing" | "held" | "done" | "failed";
  attempts: number;
  availableAt: TimestampMs | null;
  createdAt: TimestampMs | null;
  processedAt: TimestampMs | null;
  processingAt: TimestampMs | null;
};
```

### 6.2 已发现的业务事件

```ts
type MentionCreated = {
  type: "mention.created";
  aggregateType: "discussion";
  aggregateId: string;
  payload: {
    discussionId: number;
    replyId: number | null;
    authorId: number;
    mentionedUserId: number;
    mentionedUsername: string;
    title: string;
  };
};

type DiscussionCreated = {
  type: "discussion.created";
  payload: {
    discussionId: number;
    authorId: number;
    title: string;
    boardId: number;
  };
};

type ReplyCreated = {
  type: "reply.created";
  payload: {
    discussionId: number;
    replyId: number;
    parentReplyId: number | null;
    authorId: number;
    title: string;
  };
};

type DiscussionSaved = {
  type: "discussion.saved";
  payload: { discussionId: number; userId: number };
};

type DiscussionFollowed = {
  type: "discussion.followed";
  payload: { discussionId: number; userId: number };
};

type UserFollowed = {
  type: "user.followed";
  payload: { followerId: number; followeeId: number };
};

type MessageCreated = {
  type: "message.created";
  payload: {
    conversationId: number;
    senderId: number;
    recipientId: number;
  };
};

type UserBanned = {
  type: "user.banned";
  payload: {
    userId: number;
    bannedByUserId: number;
    reason: string | null;
    bannedUntil: string | null; // ISO string，不是 TimestampMs
  };
};
```

`discussion.saved` 和 `discussion.followed` 当前会写 outbox，但没有注册 worker handler。

### 6.3 进程内事件与 SSE

```ts
type MemoryEvent = {
  type: string;
  data?: Record<string, unknown>;
};

type NotificationCreatedEvent = {
  type: "notification.created";
  data: { userId: number };
};

type SseConnected = {
  event: "connected";
  data: { userId: number; at: TimestampMs };
};

type SseNotificationCreated = {
  event: "notification.created";
  data: { userId: number; seq: number };
};

type SseGap = {
  event: "gap";
  data: { seq: number };
};
```

## 7. 权限 Resource Schema

`authz.can` 当前签名是：

```py
can(actor: Actor | None, ability: str, resource: Any, conn: Connection) -> bool
```

传入 dict 时会通过 `SimpleNamespace(**resource)` 动态变成对象。根据各分支实际访问，
resource 至少存在以下隐式类型：

```ts
type BoardResource = {
  type: "board";
  id: number;
  visibility: "public" | "members" | "private";
  postingPolicy?: "everyone" | "members" | "moderators";
};

type DiscussionResource = {
  type: "discussion";
  id: number;
  authorId: number;
  boardId: number;
  isLocked?: boolean | 0 | 1;
  deletedAt?: TimestampMs | null;
};

type ReplyResource = {
  type: "reply";
  id: number;
  authorId: number;
  discussionId: number;
  boardId?: number;
  deletedAt?: TimestampMs | null;
};

type UserResource = {
  type: "user";
  id: number;
};

type AttachmentResource = {
  type: "attachment";
  id: number;
  uploaderId: number;
};

type FeedbackProjectResource = {
  type: "feedback_project";
  id: number;
};

type FeedbackItemResource = {
  type: "feedback_item";
  id: number;
  projectId: number;
  authorId: number;
};

type FeedbackCommentResource = {
  type: "feedback_comment";
  id: number;
  projectId: number;
  authorId: number;
};
```

这些类型目前没有真正声明；字段缺失只会在运行时表现为 `AttributeError`。

## 8. Python 内部对象和隐式返回协议

### 8.1 已声明对象

```ts
type CurrentUser = {
  id: number;
  username: string;
  display_name: string;
  email: string;
  role: string;
  status: string;
};

type Actor = {
  id: number;
  role: string;
  status: string;
};

type AutomodSignal = {
  rule: string;
  weight: number;
  detail?: string;
};

type AutomodVerdict = {
  decision: "allow" | "review" | "block";
  score: number;
  source: string;
  signals: AutomodSignal[];
};
```

### 8.2 未声明但频繁传递的协议

```ts
type ContentSnapshot = {
  exists: boolean;
  text: string;
  title: string | null;
  is_public_board: boolean;
};

type PreparedFinalization = [
  content: ContentSnapshot,
  verdict: AutomodVerdict | null,
  note: string,
];

type ServicePage<T> = {
  items: T[];
  nextCursor: string | number | null;
};
```

`PreparedFinalization` 当前是无返回注解 tuple；producer 和 consumer 只能靠位置约定。

## 9. `app.state` 运行时 Schema

`create_app` 当前动态挂载：

```ts
type AppState = {
  settings: Settings;
  db: Database;
  mailer: ConsoleMailer | SmtpMailer;
  oidc: OidcClient | null;
  auth_limiter: SlidingWindowLimiter;
  storage: Storage;
  events: EventBus;
  presence: MemoryPresenceStore;
  dispatcher: OutboxDispatcher;

  reap_attachment_orphans: (olderThanMs?: number) => number;
  finalize_moderation: (now?: number | null) => Record<string, unknown>[];
  flush_outbox: () => number;

  // 只在 main() 生产入口启动后保证存在
  backup_scheduler?: BackupScheduler;
};
```

测试和部分路由还会直接读取 `db.engine`，因此 `Database` 也是事实上的公开运行时协议。

## 10. 已确认的 Schema 冲突和空洞

### 10.1 后端响应没有真源

- 路由全部返回 `dict`、`list[dict]` 或无注解结果。
- OpenAPI 中大多数响应只是 `object` + `additionalProperties: true`。
- 前端 DTO 是手写副本。
- `apiFetch<T>` 不验证 JSON，只执行类型断言。

### 10.2 角色模型漂移

- 数据库注释、`require_moderator` 和多处可见性代码仍接受 `moderator`。
- `UserDTO`、`AdminUser`、`ChangeRoleBody` 只接受 `student | admin`。
- `is_global_mod` 又声明只有 `admin` 是全局管理角色。

目前无法从 schema 层判断 `moderator` 是合法存量值、迁移过渡值还是应删除值。

### 10.3 DraftDTO 是未接线的第三份 schema

`drafts.py` 中的 Python `DraftDTO`：

- 没有被 `_dto()` 返回；
- `createAt` 与 wire 的 `createdAt` 不一致；
- 时间字段为 `Any`；
- `boardSlug`、`bodyFormat` 的取值范围未声明。

### 10.4 审核流程文档和运行代码冲突

旧 `backend/docs/schema.md` 声称 `hold_until` 和 `recheck` 已停用，但当前：

- worker 仍按 `hold_until` 查询；
- `_prepare_finalization` 仍执行 AI recheck；
- `_finalize_one` 仍写入 `recheck` 和 `published_by_ai`；
- `moderation_queue_hold_idx` 仍存在。

因此本文保留当前代码实际使用的字段，不采纳“已停用”结论。

### 10.5 时间类型不统一

- 数据库和绝大多数 API 使用 epoch 毫秒。
- `user.banned.payload.bannedUntil` 使用 ISO 字符串或 null。
- 备份信息 `createdAt` 由文件时间生成，但前端仍当普通 number。

### 10.6 Optional、nullable、partial 被混在一起

Pydantic patch model 大多使用 `field: T | None = None`，随后又调用
`model_dump(exclude_none=True)`。这使下面两种意图无法区分：

1. 请求未提供字段；
2. 请求明确要求把字段清空为 `null`。

### 10.7 删除/写操作返回不一致

部分接口返回 HTTP 204；更多接口返回 HTTP 200 + `{ "ok": true }`。前端却把许多
后者声明成 `Promise<void>`，差异被 `data as T` 隐藏。

### 10.8 JSON text 没有内部 schema

这些列都是无约束 JSON：

- `users.settings`
- `moderation_queue.signals`
- `moderation_queue.recheck`
- `outbox_events.payload`
- `feedback_api_keys.project_ids`
- `app_settings.value`

其中 `outbox_events.payload` 实际承载多个不同事件 union，但数据库、Pydantic 和类型检查器
都不知道其 discriminator 与字段要求。

## 11. 建议未来收敛后的 Schema 所有权

本节只是给阅读本文时的归类参考，不代表已经实施。

```text
per-feature contract models
  ├─ request models       FastAPI/Pydantic 校验输入
  ├─ response models      FastAPI/Pydantic 校验输出并生成 OpenAPI
  ├─ domain models        业务逻辑使用，不携带 HTTP 命名细节
  ├─ repository rows      SQL Row -> 明确 mapper -> domain
  └─ event models         带 type discriminator 的事件 union

OpenAPI
  └─ generated frontend types/client
```

建议继续保留 dict 的地方：

- SQLAlchemy `.values(...)` 的短生命周期更新参数；
- 最终 JSON 序列化前的局部字面量；
- 真正开放的用户设置扩展字段。

应优先消灭 dict 的边界：

- repository 到 service；
- service 到 router；
- outbox producer 到 consumer；
- `authz.can` 的 resource；
- 后端响应到前端 client；
- `app.state` 依赖容器。

## 12. 对应源码

- 物理表：`backend/src/samryetha/schema.py`
- 数据库生命周期：`backend/src/samryetha/db.py`
- API 请求：`backend/src/samryetha/routers/`
- 后端响应组装：`backend/src/samryetha/*/service.py` 与各域 `models.py`
- 前端响应 DTO：`frontend/src/lib/api.ts`
- 错误包络：`backend/src/samryetha/errors.py`
- 权限 resource：`backend/src/samryetha/authz.py`
- outbox envelope：`backend/src/samryetha/outbox.py`
- outbox consumers：`backend/src/samryetha/outbox_worker.py`
- SSE：`backend/src/samryetha/routers/realtime.py`
- 运行时容器：`backend/src/samryetha/main.py`
