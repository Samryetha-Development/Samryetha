# Samryetha 数据库 Schema

数据库：SQLite（WAL 模式）。DDL 由 Drizzle 管理（`pnpm db:generate` / `db:migrate`）。全部 schema 定义在 `src/infrastructure/db/schema.ts`。

## 通用约定

- **时间戳**：`integer(name, { mode: "timestamp_ms" })` —— 存毫秒数，映射为 `Date`。
- **布尔**：0/1 整数。
- **软删列**（`discussions` / `replies` / `boards`）：`deleted_at`、`deleted_by`、`deletion_reason`。
- 外键开启：`PRAGMA foreign_keys = ON`；`busy_timeout = 5000`。
- 表名 snake_case；Drizzle 列名 snake_case。

## 表清单

### 身份与用户

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `schools` | `id`, `name`, `email_domain`(唯一) | 学校 + 邮箱域名 allowlist 来源 |
| `users` | `id`, `username`(唯一 NOCASE), `email`(唯一), `display_name`, `bio`, `password_hash`(argon2id), `role`(`student`/`moderator`/`admin`), `status`(`pending`/`active`/`banned`/`deactivated`), `email_domain`, `email_verified_at`, `avatar_object_key`, `settings`(JSON), `last_seen_at` | 核心身份实体 |
| `sessions` | `token_hash`(PK=sha256), `user_id`, `expires_at`, `ip`, `user_agent`, `last_seen_at` | 服务端会话 |
| `email_verification_tokens` | `user_id`(唯一), `token_hash`(=sha256 验证码), `expires_at` | 6 位验证码，15 分钟 |
| `password_reset_tokens` | `user_id`(唯一), `token_hash`, `expires_at` | 1 小时有效 |

### 内容

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `boards` | `slug`(唯一), `name`, `description`, `visibility`(`public`/`members`/`private`), `posting_policy`(`everyone`/`members`/`moderators`), `created_by_user_id`, 软删列 | 动态板块实体 |
| `board_members` | `board_id`+`user_id`(复合 PK), `role`(`member`/`moderator`) | 板块成员/板块版主 |
| `discussions` | `board_id`, `author_id`, `title`, `body_md`, `body_html`, `reply_count`, `save_count`, `is_pinned`, `is_locked`, `last_reply_at`, `created_at`, `updated_at`, 软删列 | 帖子（反规范化计数） |
| `replies` | `discussion_id`, `author_id`, `parent_reply_id`(自引用 FK), `body_md`, `body_html`, 软删列 | 回复（支持线程嵌套） |
| `attachments` | `uploader_id`, `object_key`, `original_filename`, `mime_type`, `size_bytes`, `state`(`pending`/`uploaded`/`attached`/`orphaned`) | 附件元数据 |

### 互动

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `user_follows` | `follower_id`+`followee_id`(复合 PK), CHECK 防自关注 | 用户关注 |
| `discussion_follows` | `user_id`+`discussion_id`(复合 PK) | 关注帖子 |
| `discussion_saves` | `user_id`+`discussion_id`(复合 PK) | 收藏帖子 |
| `notifications` | `user_id`, `actor_user_id`, `type`(`reply`/`follow`/`mention`/`ban`...), `discussion_id`, `reply_id`, `body`, `is_read`, `created_at` | 站内通知 |

### 治理

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `reports` | `reporter_user_id`, `reportable_type`(`discussion`/`reply`/`user`), `reportable_id`, `reason`, `status`(`open`/`in_progress`/`resolved`/`dismissed`) | 举报 |
| `moderation_actions` | `actor_user_id`, `action`, `target_type`, `target_id`, `reason`, `created_at` | 治理审计日志 |
| `bans` | `user_id`, `banned_by_user_id`, `reason`, `banned_until`, `is_active`, `created_at` | 封禁记录（可期满） |

### 基建

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `outbox_events` | `id`, `event_type`, `aggregate_type`, `aggregate_id`, `payload`(JSON), `status`(`pending`/`processing`/`processed`/`failed`), `attempts`, `available_at`(退避), `processed_at` | transactional outbox |

### 反馈（feedback 模块，与板块/版主完全独立）

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `feedback_projects` | `id`, `name`, `description`, `created_by_user_id`, 软删列 | 反馈项目（不同于论坛板块） |
| `feedback_project_members` | `project_id`+`user_id`(复合 PK), `is_programmer`(0/1) | 项目成员；`is_programmer=1` 为程序员（可标完成/过期/管理他人条目） |
| `feedback_items` | `id`, `project_id`, `author_id`, `seq`(项目内递增), `title`, `detail`, `type`(`bug`/`suggestion`), `urgency`(`urgent`/`normal`), `status`(`open`/`done`/`expired`), `closed_at`, `edited_at`, 软删列；唯一索引 `(project_id, seq)` | 反馈条目（seq 按项目自动编号，唯一索引兜底并发） |
| `feedback_api_keys` | `id`, `name`, `key_hash`(sha256), `key_prefix`, `role`(`read`/`write`), `project_ids`(JSON 数组，空=全部), `enabled`, `last_used_at` | Agent API 密钥（完整 key 仅创建时展示一次） |
| `app_settings` | `key`(PK), `value`(JSON) | 通用键值设置（反馈备份 cron/keep、待恢复标记等） |

> 备份：快照用 `VACUUM INTO` 生成完整库文件存 `data/backups/`；恢复 = 写待恢复标记，下次启动换库文件后生效。

## 迁移与未来切 PG

- SQLite `autoincrement` → PG `identity`。
- `timestamp_ms` → `timestamptz`。
- `username` NOCASE → PG 用 `lower()` 表达式唯一索引。
- FTS（搜索）在 SQLite 用 FTS5 trigram、PG 用 `to_tsvector` + GIN，隔离在 search 模块内。
