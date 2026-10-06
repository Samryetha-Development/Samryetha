# Samryetha 数据库 Schema

数据库：SQLite（WAL 模式）。DDL 由 SQLAlchemy Core metadata 管理，全部 schema 定义在 `src/samryetha/schema.py`。

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
| `users` | `id`, `username`(唯一 NOCASE), `email`(唯一), `display_name`, `bio`, `profile_moderation_status`, `pending_display_name`, `pending_bio`, `password_hash`(argon2id), `role`(`student`/`moderator`/`admin`), `status`(`pending`/`active`/`banned`/`deactivated`), `email_domain`, `email_verified_at`, `avatar_object_key`, `settings`(JSON), `last_seen_at` | 核心身份实体。资料文本改动先落 `pending_*`，`display_name`/`bio` 始终保持"上一次通过"的值；失败原文不在主字段上，只有管理员能从留存库看到 |
| `sessions` | `token_hash`(PK=sha256), `user_id`, `expires_at`, `ip`, `user_agent`, `last_seen_at` | 服务端会话 |
| `oidc_identities` | `user_id`, `issuer`, `subject`, `email_at_link`, `last_login_at`；`(issuer, subject)` 唯一 | 外部 OIDC 身份到论坛用户的稳定映射；email 不作为身份主键 |
| `oidc_login_transactions` | `state_hash`(PK), `nonce`, `code_verifier`, `return_to`, `expires_at` | 10 分钟、一次性的服务端 OIDC/PKCE 登录事务 |
| `email_verification_tokens` | `user_id`(唯一), `token_hash`(=sha256 验证码), `expires_at` | 6 位验证码，15 分钟 |
| `password_reset_tokens` | `user_id`(唯一), `token_hash`, `expires_at` | 1 小时有效 |

### 内容

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `boards` | `slug`(唯一), `name`, `description`, `visibility`(`public`/`members`/`private`), `posting_policy`(`everyone`/`members`/`moderators`), `created_by_user_id`, 软删列 | 动态板块实体 |
| `board_members` | `board_id`+`user_id`(复合 PK), `role`(`member`/`moderator`) | 板块成员/板块版主 |
| `discussions` | `board_id`, `author_id`, `title`, `body_md`, `body_html`, `reply_count`, `save_count`, `is_pinned`, `is_locked`, `last_reply_at`, `created_at`, `updated_at`, 软删列 | 帖子（反规范化计数） |
| `discussion_drafts` | `id`, `author_id`(FK users, CASCADE), `board_slug`(可空), `title`, `body_md`, `body_format`, `created_at`, `updated_at` | 作者私有的未发布草稿；与讨论及其事件完全分离；保留原始文字，板块变动不删除草稿 |
| `draft_attachments` | `attachment_id`(PK/FK attachments, CASCADE), `draft_id`(FK discussion_drafts, CASCADE) | 一个附件最多关联一篇草稿；关联期间免于 uploaded 孤儿回收，发布/删除草稿或删除附件时解除关联 |
| `replies` | `discussion_id`, `author_id`, `parent_reply_id`(自引用 FK), `body_md`, `body_html`, 软删列 | 回复（支持线程嵌套） |
| `attachments` | `uploader_id`, `object_key`, `original_filename`, `mime_type`, `size_bytes`, `state`(`pending`/`uploaded`/`attached`/`orphaned`) | 附件元数据 |

### 互动

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `user_follows` | `follower_id`+`followee_id`(复合 PK), CHECK 防自关注 | 用户关注 |
| `discussion_follows` | `user_id`+`discussion_id`(复合 PK) | 关注帖子 |
| `discussion_saves` | `user_id`+`discussion_id`(复合 PK) | 收藏帖子 |
| `notifications` | `user_id`, `actor_user_id`, `type`(`reply`/`follow`/`mention`/`ban`...), `discussion_id`, `reply_id`, `body`, `source_event_id`, `is_read`, `created_at` | 站内通知；`source_event_id` 关联 outbox 事件，按事件去重且允许重复的独立关注或封禁行为 |

### 治理

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `reports` | `reporter_user_id`, `reportable_type`(`discussion`/`reply`/`user`), `reportable_id`, `reason`, `status`(`open`/`in_progress`/`resolved`/`dismissed`) | 举报 |
| `moderation_actions` | `actor_user_id`, `action`, `target_type`, `target_id`, `reason`, `created_at` | 治理审计日志（人工处置；AI 先行处置记在 `moderation_queue` 的 `recheck`/`resolution` 里） |
| `moderation_queue` | `content_type`(`discussion`/`reply`/`profile`/`message`/`attachment`), `content_id`, `author_id`, `excerpt`, `decision`(机器判定 `allow`/`review`/`block`), `score`, `signals`(JSON), `review_state`(人的决定 `pending`/`approved`/`rejected`), `reviewer_id`, `review_note`, `hold_until`(**已停用**，发布即审核后不再写入), `resolution`(`blocked_by_machine`/`blocked`/`published_by_human`/`published_by_ai`), `resolved_at`, `recheck`(旧复审快照，已停用), `overturned`, `submitted_text`(送审全文快照), `superseded_at`(被新版本替代的毫秒时间，可空) | 审核队列。**发布即审核**：规则命中或模型判 `block` 直接封禁（`resolution=blocked_by_machine`），其余进队列由管理员随时维持/推翻。每个送审版本独立一行，旧版本不再参与待办或人工回写。所有「不予公开」的处置（`blocked` + `blocked_by_machine`）原文快照留存且**仅管理员可访问** |
| `bans` | `user_id`, `banned_by_user_id`, `reason`, `banned_until`, `is_active`, `created_at` | 封禁记录（可期满） |

### 基建

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `outbox_events` | `id`, `event_type`, `aggregate_type`, `aggregate_id`, `payload`(JSON), `status`(`pending`/`processing`/`held`/`done`/`failed`), `attempts`, `available_at`(退避), `processed_at` | transactional outbox；`held` 为审核期间暂停投递，放行后恢复；`outbox_aggregate_event_idx` 加速定位创建事件及幂等补发 |

### 反馈（feedback 模块，与板块/版主完全独立）

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `feedback_projects` | `id`, `name`, `description`, `created_by_user_id`, 软删列 | 反馈项目（不同于论坛板块） |
| `feedback_project_members` | `project_id`+`user_id`(复合 PK), `is_programmer`(0/1) | 项目成员；`is_programmer=1` 为程序员（可标完成/过期/管理他人条目） |
| `feedback_items` | `id`, `project_id`, `author_id`, `seq`(项目内递增), `title`, `detail`, `type`(`bug`/`suggestion`), `urgency`(`urgent`/`normal`), `status`(`open`/`done`/`expired`), `closed_at`, `edited_at`, 软删列；唯一索引 `(project_id, seq)` | 反馈条目（seq 按项目自动编号，唯一索引兜底并发） |
| `feedback_api_keys` | `id`, `name`, `key_hash`(sha256), `key_prefix`, `role`(`read`/`write`), `project_ids`(JSON 数组，空=全部), `enabled`, `last_used_at` | Agent API 密钥（完整 key 仅创建时展示一次） |
| `app_settings` | `key`(PK), `value`(JSON) | 通用键值设置（反馈备份 cron/keep、待恢复标记等） |

> 备份：快照用 `VACUUM INTO` 生成完整库文件存 `data/backups/`；恢复 = 写待恢复标记，下次启动换库文件后生效。

### 任务（独立于 feedback，仅管理员）

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `tasks` | `id`, `author_id`, `category`(默认 `General`), `title`, `notes`, `priority`(`urgent`/`normal`), `status`(`open`/`done`), `done_at`, `created_at`, `updated_at` | 开发任务看板；**仅管理员可读写** |
| `task_comments` | `id`, `task_id`, `author_id`, `parent_comment_id`(自引用), `body`, 软删列, `created_at`, `updated_at` | 任务嵌套评论（删除任务时一并清除） |

### 文件服务（面向新生的资料库）

文件服务刻意**不复用 `attachments`**：附件的可见性由父帖推断、有存亡周期（`reap_orphans` 回收），
而资料是独立的一等内容、可见性由自身字段决定、属于长期资产。两者只共享存储层
（同一 `Storage` 实例与同一套 HMAC presign 算法，`object_key` 形状一致）。

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `file_categories` | `id`, `slug`(唯一), `name`, `description`, `kind`(`guide`/`outline`/`syllabus`/`exam`/`other`), `sort_order`, `is_system`, 软删列 | 资料分类。`is_system=1` 为内建分类，只可改名排序、不可删除；非空分类拒绝删除（否则会留下无法按分类检索的悬空资料） |
| `file_resources` | `id`, `category_id`, `uploader_id`, `title`, `description_md`, `tags`(JSON 数组), `object_key`(唯一), `original_filename`, `mime_type`, `size_bytes`, `sha256`, `visibility`(`public`/`members`/`private`), `moderation_status`, `status`(`published`/`archived`), `version`, `is_featured`, `download_count`, `favorite_count`, `rating_sum`, `rating_count`, 软删列 | 资料主表。`mime_type` 仅供展示，回源 Content-Type 一律按 `object_key` 扩展名推导（防存储型 XSS）。末四列为冗余计数，只在 `files_service` 内与明细表同事务更新 |
| `file_favorites` | `resource_id`+`user_id`(复合 PK), `created_at` | 收藏（幂等） |
| `file_ratings` | `resource_id`+`user_id`(复合 PK), `score`(1–5), `created_at`, `updated_at` | 评分，一人一票，改分原地 UPDATE |
| `file_downloads` | `id`, `resource_id`, `user_id`(可空), `client_ip`, `created_at` | 下载明细。**计数按 (resource,user) 终身去重、匿名按 (resource,ip,24 小时) 去重，明细始终全量落库**——计数是脸面、明细是证据 |

> 建表方式与既有表一致：`create_schema()` 的 `create_all` 幂等建表，无需迁移框架；
> 内建分类由启动时的 `ensure_seed_categories()` 幂等写入（已存在则新增 0 行）。

## 迁移与未来切 PG

草稿功能仅新增 `discussion_drafts` 和 `draft_attachments` 两张表，不修改已有表或存量帖子。标准 `python -m samryetha.main` 启动流程中的 `create_schema()` 幂等创建缺失表；其他启动方式也应在首次启用前调用该方法。无需新增环境变量。

- SQLite `autoincrement` → PG `identity`。
- `timestamp_ms` → `timestamptz`。
- `username` NOCASE → PG 用 `lower()` 表达式唯一索引。
- FTS（搜索）在 SQLite 用 FTS5 trigram、PG 用 `to_tsvector` + GIN，隔离在 search 模块内。
