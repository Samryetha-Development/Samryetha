"""数据库显式 schema — 逐表镜像 backend/src/infrastructure/db/schema.ts 的最终 DDL。

约定（与 TS/Drizzle 层一致）：
- 所有时间戳都是 epoch **毫秒**整数，直接读写 int（存量数据即如此，勿用 SQLAlchemy DateTime）。
- JSON 列（users.settings / feedback_api_keys.project_ids / app_settings.value）存 JSON TEXT。
- 布尔/位标记是整数 0/1。
- 本模块是 Python 端唯一 schema 真源；运行时直接打开既有 SQLite（不跑 DDL），测试里用 create_all。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

# ---------------------------------------------------------------- helpers


def _ms(name: str) -> Column[int]:
    return Column(name, BigInteger)


def _soft_delete() -> list[Column[int] | Column[str]]:
    return [
        _ms("deleted_at"),
        Column("deleted_by", Integer),
        Column("deletion_reason", Text),
    ]


# ---------------------------------------------------------------- users

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", Text, nullable=False),
    Column("email", Text, nullable=False),
    Column("recovery_email", Text),
    Column("display_name", Text, nullable=False),
    Column("bio", Text, nullable=False, server_default=""),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False, server_default="student"),  # student|moderator|admin
    Column("status", Text, nullable=False, server_default="pending"),  # pending|active|banned|deactivated
    Column("discriminator", Integer),  # 随机 4 位身份号
    Column("email_domain", Text),
    _ms("email_verified_at"),
    Column("avatar_object_key", Text),
    _ms("last_seen_at"),
    Column("settings", Text, nullable=False, server_default="{}"),  # JSON text
    _ms("created_at"),
    _ms("updated_at"),
    _ms("deleted_at"),
    UniqueConstraint("email", name="users_email_unique"),
    UniqueConstraint("username", name="users_username_unique"),
    UniqueConstraint("discriminator", name="users_discriminator_unique"),
    Index("users_status_idx", "status"),
    Index("users_deleted_at_idx", "deleted_at"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- schools

schools = Table(
    "schools",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("email_domain", Text, nullable=False, unique=True),
    Column("is_active", Integer, nullable=False, server_default="1"),
    _ms("created_at"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- boards

boards = Table(
    "boards",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("visibility", Text, nullable=False, server_default="public"),  # public|members|private
    Column("posting_policy", Text, nullable=False, server_default="members"),  # everyone|members|moderators
    Column("created_by_user_id", Integer),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    UniqueConstraint("slug", name="boards_slug_unique"),
    sqlite_autoincrement=True,
)

board_members = Table(
    "board_members",
    metadata,
    Column("board_id", Integer, ForeignKey("boards.id"), primary_key=True, nullable=False),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("role", Text, nullable=False, server_default="member"),  # member|moderator
    _ms("joined_at"),
    Index("board_members_user_idx", "user_id"),
)

# ---------------------------------------------------------------- discussions

discussions = Table(
    "discussions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("board_id", Integer, ForeignKey("boards.id"), nullable=False),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("body_md", Text, nullable=False),
    Column("body_html", Text),
    Column("body_format", Text, nullable=False, server_default="markdown"),  # markdown|text
    Column("reply_count", Integer, nullable=False, server_default="0"),
    Column("save_count", Integer, nullable=False, server_default="0"),
    Column("is_pinned", Integer, nullable=False, server_default="0"),
    Column("is_locked", Integer, nullable=False, server_default="0"),
    Column("status", Text, nullable=False, server_default="open"),  # open|locked
    _ms("last_reply_at"),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("discussions_board_activity_idx", "board_id", "last_reply_at"),
    Index("discussions_board_created_idx", "board_id", "created_at"),
    Index("discussions_author_created_idx", "author_id", "created_at"),
    Index("discussions_pinned_activity_idx", "is_pinned", "last_reply_at"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- replies

# Private compositions are separate from published discussions and their events.
discussion_drafts = Table(
    "discussion_drafts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("author_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("board_slug", Text),
    Column("title", Text, nullable=False, server_default=""),
    Column("body_md", Text, nullable=False, server_default=""),
    Column("body_format", Text, nullable=False, server_default="text"),
    _ms("created_at"),
    _ms("updated_at"),
    Index("discussion_drafts_author_updated_idx", "author_id", "updated_at", "id"),
    sqlite_autoincrement=True,
)

replies = Table(
    "replies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("discussion_id", Integer, ForeignKey("discussions.id"), nullable=False),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("parent_reply_id", Integer, ForeignKey("replies.id")),  # 自引用，表级声明
    Column("body_md", Text, nullable=False),
    Column("body_html", Text),
    Column("body_format", Text, nullable=False, server_default="markdown"),  # markdown|text
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("replies_discussion_created_idx", "discussion_id", "created_at"),
    Index("replies_author_idx", "author_id"),
    Index("replies_parent_idx", "parent_reply_id"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- saves / follows

discussion_saves = Table(
    "discussion_saves",
    metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("discussion_id", Integer, ForeignKey("discussions.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    Index("discussion_saves_discussion_idx", "discussion_id"),
)

discussion_follows = Table(
    "discussion_follows",
    metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("discussion_id", Integer, ForeignKey("discussions.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    Index("discussion_follows_discussion_idx", "discussion_id"),
)

user_follows = Table(
    "user_follows",
    metadata,
    Column("follower_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("followee_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    CheckConstraint("follower_id <> followee_id", name="user_follows_no_self"),
    Index("user_follows_followee_idx", "followee_id"),
)

# ---------------------------------------------------------------- notifications

notifications = Table(
    "notifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("actor_user_id", Integer, ForeignKey("users.id")),
    Column("type", Text, nullable=False),  # reply|mention|follow|system|moderation|ban
    Column("discussion_id", Integer, ForeignKey("discussions.id")),
    Column("reply_id", Integer, ForeignKey("replies.id")),
    Column("body", Text),
    Column("source_event_id", Integer),
    Column("is_read", Integer, nullable=False, server_default="0"),
    _ms("read_at"),
    _ms("created_at"),
    Index("notifications_user_read_created_idx", "user_id", "is_read", "created_at"),
    Index("notifications_user_created_idx", "user_id", "created_at"),
    Index("notifications_user_source_event_uq", "user_id", "source_event_id", unique=True),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- attachments

attachments = Table(
    "attachments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("uploader_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("discussion_id", Integer, ForeignKey("discussions.id")),
    Column("object_key", Text, nullable=False, unique=True),
    Column("original_filename", Text, nullable=False),
    Column("mime_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", Text),
    Column("state", Text, nullable=False, server_default="pending"),  # pending|uploaded|attached|orphaned
    _ms("created_at"),
    Index("attachments_uploader_created_idx", "uploader_id", "created_at"),
    Index("attachments_discussion_idx", "discussion_id"),
    Index("attachments_state_idx", "state"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- moderation

draft_attachments = Table(
    "draft_attachments",
    metadata,
    Column("attachment_id", Integer, ForeignKey("attachments.id", ondelete="CASCADE"), primary_key=True),
    Column("draft_id", Integer, ForeignKey("discussion_drafts.id", ondelete="CASCADE"), nullable=False),
    Index("draft_attachments_draft_idx", "draft_id"),
)

reports = Table(
    "reports",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reporter_user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("reportable_type", Text, nullable=False),  # discussion|reply|user
    Column("reportable_id", Integer, nullable=False),
    Column("reason", Text),
    Column("status", Text, nullable=False, server_default="open"),  # open|in_progress|resolved|dismissed
    _ms("created_at"),
    Index("reports_status_created_idx", "status", "created_at"),
    Index("reports_reportable_idx", "reportable_type", "reportable_id"),
    sqlite_autoincrement=True,
)

moderation_actions = Table(
    "moderation_actions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("action", Text, nullable=False),
    Column("target_type", Text, nullable=False),
    Column("target_id", Integer, nullable=False),
    Column("reason", Text),
    _ms("created_at"),
    Index("moderation_actions_target_idx", "target_type", "target_id"),
    Index("moderation_actions_actor_created_idx", "actor_user_id", "created_at"),
    sqlite_autoincrement=True,
)

bans = Table(
    "bans",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("banned_by_user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("reason", Text),
    _ms("banned_until"),
    Column("is_active", Integer, nullable=False, server_default="1"),
    _ms("created_at"),
    Index("bans_user_active_idx", "user_id", "is_active"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- sessions

sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    _ms("expires_at"),
    Column("ip", Text),
    Column("user_agent", Text),
    _ms("created_at"),
    _ms("last_seen_at"),
    Index("sessions_user_idx", "user_id"),
    Index("sessions_expires_idx", "expires_at"),
)

oidc_identities = Table(
    "oidc_identities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("email_at_link", Text),
    _ms("created_at"),
    _ms("last_login_at"),
    UniqueConstraint("issuer", "subject", name="oidc_identities_issuer_subject_unique"),
    Index("oidc_identities_user_idx", "user_id"),
    sqlite_autoincrement=True,
)

oidc_login_transactions = Table(
    "oidc_login_transactions",
    metadata,
    Column("state_hash", Text, primary_key=True),
    Column("nonce", Text, nullable=False),
    Column("code_verifier", Text, nullable=False),
    Column("return_to", Text, nullable=False, server_default="/"),
    _ms("expires_at"),
    _ms("created_at"),
    Index("oidc_login_transactions_expires_idx", "expires_at"),
)

# OIDC 首次登录无映射时的认领票据：用户凭老用户名+密码把 (issuer, subject)
# 绑定到已有账号。一次性（消费即删），密码连续错 5 次即作废。
oidc_claim_tickets = Table(
    "oidc_claim_tickets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ticket_hash", Text, nullable=False, unique=True),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("email", Text),
    Column("display_name", Text),
    Column("attempts", Integer, nullable=False, server_default="0"),
    _ms("expires_at"),
    _ms("created_at"),
    Index("oidc_claim_tickets_hash_idx", "ticket_hash"),
    Index("oidc_claim_tickets_expires_idx", "expires_at"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- tokens

# 扫码登录票据：PC 展示二维码，手机确认后 PC 凭 secret 换会话。
# ticket_id 公开（二维码/推送通道用），secret 只存哈希；单次有效，2 分钟 TTL。
qr_login_tickets = Table(
    "qr_login_tickets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ticket_id_hash", Text, nullable=False, unique=True),
    Column("secret_hash", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),  # pending|approved|denied
    Column("approved_by", Integer, ForeignKey("users.id")),
    Column("ip", Text),
    Column("user_agent", Text),
    _ms("expires_at"),
    _ms("created_at"),
    _ms("decided_at"),
    Index("qr_login_tickets_hash_idx", "ticket_id_hash"),
    Index("qr_login_tickets_expires_idx", "expires_at"),
    sqlite_autoincrement=True,
)

# 扫码登录邮箱确认码：仅当账号有可投递且经 IdP 校验的邮箱时才要求二次确认。
# 只存哈希（ticket_id_hash / code_hash），code_hash 绑 user_id，避免跨用户撞码；
# attempts 限次，consumed_at 保证单次使用。
qr_login_confirmation_codes = Table(
    "qr_login_confirmation_codes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ticket_id_hash", Text, nullable=False),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("code_hash", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    _ms("expires_at"),
    _ms("created_at"),
    _ms("consumed_at"),
    Index("qr_login_confirmation_codes_ticket_idx", "ticket_id_hash"),
    Index("qr_login_confirmation_codes_user_idx", "user_id"),
    sqlite_autoincrement=True,
)

email_verification_tokens = Table(
    "email_verification_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), unique=True, nullable=False),
    Column("token_hash", Text, unique=True, nullable=False),
    _ms("expires_at"),
    _ms("created_at"),
    sqlite_autoincrement=True,
)

password_reset_tokens = Table(
    "password_reset_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("token_hash", Text, unique=True, nullable=False),
    _ms("expires_at"),
    _ms("used_at"),
    _ms("created_at"),
    Index("password_reset_tokens_user_idx", "user_id"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- outbox

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_type", Text, nullable=False),
    Column("aggregate_type", Text),
    Column("aggregate_id", Text),
    Column("payload", Text, nullable=False),  # JSON 字符串
    Column("status", Text, nullable=False, server_default="pending"),  # pending|processing|held|done|failed
    Column("attempts", Integer, nullable=False, server_default="0"),
    _ms("available_at"),
    _ms("created_at"),
    _ms("processed_at"),
    # 租约制回收：claim(pending→processing)时写入，worker崩溃/超时后可扫回 pending。
    # Lease for crash recovery: set on claim; stale processing rows are swept back to pending.
    _ms("processing_at"),
    Index("outbox_status_available_idx", "status", "available_at", "id"),
    Index("outbox_aggregate_event_idx", "aggregate_type", "aggregate_id", "event_type"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- feedback

feedback_projects = Table(
    "feedback_projects",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("created_by_user_id", Integer, ForeignKey("users.id")),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("feedback_projects_created_idx", "created_at"),
    sqlite_autoincrement=True,
)

feedback_project_members = Table(
    "feedback_project_members",
    metadata,
    Column("project_id", Integer, ForeignKey("feedback_projects.id"), primary_key=True, nullable=False),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("is_programmer", Integer, nullable=False, server_default="0"),
    _ms("joined_at"),
    Index("feedback_project_members_user_idx", "user_id"),
)

feedback_items = Table(
    "feedback_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", Integer, ForeignKey("feedback_projects.id"), nullable=False),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("detail", Text, nullable=False, server_default=""),
    Column("type", Text, nullable=False),  # bug|suggestion
    Column("urgency", Text, nullable=False, server_default="normal"),  # urgent|normal
    Column("status", Text, nullable=False, server_default="open"),  # open|done|expired
    _ms("closed_at"),
    _ms("edited_at"),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    UniqueConstraint("project_id", "seq", name="feedback_items_project_seq_unique"),
    Index("feedback_items_project_status_idx", "project_id", "status"),
    Index("feedback_items_author_idx", "author_id"),
    sqlite_autoincrement=True,
)

feedback_comments = Table(
    "feedback_comments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("item_id", Integer, ForeignKey("feedback_items.id"), nullable=False),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("parent_comment_id", Integer, ForeignKey("feedback_comments.id")),  # 自引用，嵌套评论
    Column("body", Text, nullable=False),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("feedback_comments_item_created_idx", "item_id", "created_at"),
    Index("feedback_comments_parent_idx", "parent_comment_id"),
    sqlite_autoincrement=True,
)

feedback_api_keys = Table(
    "feedback_api_keys",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("key_hash", Text, unique=True, nullable=False),
    Column("key_prefix", Text, nullable=False),
    Column("role", Text, nullable=False, server_default="read"),  # read|write
    Column("project_ids", Text, nullable=False, server_default="[]"),  # JSON array of ints
    Column("enabled", Integer, nullable=False, server_default="1"),
    _ms("last_used_at"),
    _ms("created_at"),
    Index("feedback_api_keys_created_idx", "created_at"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- tasks

# 开发任务追踪（独立于 feedback）：仅管理员可见，分组(category)+优先级(priority)。
tasks = Table(
    "tasks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("category", Text, nullable=False, server_default="General"),
    Column("title", Text, nullable=False),
    Column("notes", Text, nullable=False, server_default=""),
    Column("priority", Text, nullable=False, server_default="normal"),  # urgent|normal
    Column("status", Text, nullable=False, server_default="open"),  # open|done
    _ms("done_at"),
    _ms("created_at"),
    _ms("updated_at"),
    Index("tasks_status_created_idx", "status", "created_at"),
    Index("tasks_author_idx", "author_id"),
    sqlite_autoincrement=True,
)

task_comments = Table(
    "task_comments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", Integer, ForeignKey("tasks.id"), nullable=False),
    Column("author_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("parent_comment_id", Integer, ForeignKey("task_comments.id")),  # 自引用，嵌套评论
    Column("body", Text, nullable=False),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("task_comments_task_created_idx", "task_id", "created_at"),
    Index("task_comments_parent_idx", "parent_comment_id"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- file service

# 文件服务（面向新生的资料库：新生攻略 / 各科复习提纲 / 学习纲要 / 历年题）。
# File service (a resource library for newcomers: freshman guides / course outlines /
# study syllabi / past exam papers).
#
# 与 attachments 的分工：附件依附于讨论帖，可见性由父帖推断，且会被 reap_orphans 回收；
# 资料是独立的一等内容，可见性由自身字段决定，且属于长期资产。
# Division of labour with attachments: attachments belong to a discussion, derive their
# visibility from the parent post, and are recycled by reap_orphans; a resource is a
# first-class standalone item whose visibility comes from its own columns and is a
# long-lived asset.
file_categories = Table(
    "file_categories",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    # guide(新生攻略)|outline(复习提纲)|syllabus(学习纲要)|exam(历年题)|other
    Column("kind", Text, nullable=False, server_default="other"),
    Column("sort_order", Integer, nullable=False, server_default="0"),
    # 内建分类不允许删除（仅可改名与排序），避免运营一次误删清空整个导航骨架。
    # Built-in categories cannot be deleted (only renamed and reordered), so a single
    # mistake cannot wipe the whole navigation skeleton.
    Column("is_system", Integer, nullable=False, server_default="0"),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    UniqueConstraint("slug", name="file_categories_slug_unique"),
    Index("file_categories_kind_idx", "kind"),
    Index("file_categories_sort_idx", "sort_order", "id"),
    sqlite_autoincrement=True,
)

file_resources = Table(
    "file_resources",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category_id", Integer, ForeignKey("file_categories.id"), nullable=False),
    Column("uploader_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("description_md", Text, nullable=False, server_default=""),
    # JSON 数组：归一化小写、去重、最多 8 个、单个不超过 24 字符（见 files.service.normalize_tags）。
    # JSON array: lower-cased, de-duplicated, at most 8 entries of at most 24 chars each.
    Column("tags", Text, nullable=False, server_default="[]"),
    Column("object_key", Text, nullable=False, unique=True),
    Column("original_filename", Text, nullable=False),
    # 入库声明，仅用于展示；回源 Content-Type 一律按 objectKey 扩展名推导（防存储型 XSS）。
    # Stored declaration for display only; the served Content-Type always comes from the
    # objectKey extension (prevents stored XSS).
    Column("mime_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", Text),
    # public(所有人)|members(登录用户)|private(仅上传者与管理员)
    Column("visibility", Text, nullable=False, server_default="members"),
    # published|archived：归档后仍在库中可检索，但不再出现在默认列表。
    # published|archived: an archived resource stays searchable but leaves the default list.
    Column("status", Text, nullable=False, server_default="published"),
    # 版本号，为后续"同一份资料的新版本"预留；本期恒为 1。
    # Version number reserved for future revisions of the same resource; always 1 for now.
    Column("version", Integer, nullable=False, server_default="1"),
    Column("is_featured", Integer, nullable=False, server_default="0"),
    # 以下四列是冗余计数，避免列表页对明细表做 COUNT/SUM 聚合。
    # 写入路径集中在 FileRepository 内，与明细表同事务更新。
    # The four counters below are denormalised to keep the list query free of COUNT/SUM
    # aggregation; every write goes through FileRepository and updates them in the same
    # transaction as the detail row.
    Column("download_count", Integer, nullable=False, server_default="0"),
    Column("favorite_count", Integer, nullable=False, server_default="0"),
    Column("rating_sum", Integer, nullable=False, server_default="0"),
    Column("rating_count", Integer, nullable=False, server_default="0"),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("file_resources_category_created_idx", "category_id", "created_at"),
    Index("file_resources_category_download_idx", "category_id", "download_count"),
    Index("file_resources_uploader_created_idx", "uploader_id", "created_at"),
    Index("file_resources_featured_idx", "is_featured", "created_at"),
    Index("file_resources_status_idx", "status"),
    sqlite_autoincrement=True,
)

file_favorites = Table(
    "file_favorites",
    metadata,
    Column("resource_id", Integer, ForeignKey("file_resources.id"), primary_key=True, nullable=False),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    Index("file_favorites_user_created_idx", "user_id", "created_at"),
)

file_ratings = Table(
    "file_ratings",
    metadata,
    Column("resource_id", Integer, ForeignKey("file_resources.id"), primary_key=True, nullable=False),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True, nullable=False),
    # 1-5 星，一人一票，改分即原地 UPDATE（不追加流水）。
    # 1-5 stars, one vote per user; changing a score updates the row in place.
    Column("score", Integer, nullable=False),
    _ms("created_at"),
    _ms("updated_at"),
    Index("file_ratings_user_idx", "user_id"),
)

# 下载明细：计数是脸面、明细是证据。count 会按"同一用户同一资源"去重后自增，
# 明细表则保留全部记录（含重复下载），供后续审计与防刷分析。
# Download log: the counter is the public face, the log is the evidence. The counter is
# incremented once per (user, resource) pair while the log keeps every hit, repeated ones
# included, for later auditing and anti-abuse analysis.
file_downloads = Table(
    "file_downloads",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("resource_id", Integer, ForeignKey("file_resources.id"), nullable=False),
    # 未登录的公开资料下载没有用户，故允许 NULL。
    # A public download by a signed-out visitor has no user, hence nullable.
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("client_ip", Text),
    _ms("created_at"),
    Index("file_downloads_resource_created_idx", "resource_id", "created_at"),
    Index("file_downloads_resource_user_idx", "resource_id", "user_id"),
    Index("file_downloads_user_created_idx", "user_id", "created_at"),
    sqlite_autoincrement=True,
)


# ---------------------------------------------------------------- app settings

app_settings = Table(
    "app_settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),  # JSON text
)


# ---------------------------------------------------------------- direct messages

conversations = Table(
    "conversations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_a_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("user_b_id", Integer, ForeignKey("users.id"), nullable=False),
    _ms("last_message_at"),
    _ms("created_at"),
    UniqueConstraint("user_a_id", "user_b_id", name="conversations_pair_unique"),
    Index("conversations_user_a_idx", "user_a_id"),
    Index("conversations_user_b_idx", "user_b_id"),
    sqlite_autoincrement=True,
)

direct_messages = Table(
    "direct_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", Integer, ForeignKey("conversations.id"), nullable=False),
    Column("sender_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("source", Text, nullable=False, server_default="user"),  # 预留：其他平台接入
    _ms("read_at"),
    _ms("created_at"),
    Index("direct_messages_conversation_idx", "conversation_id", "created_at"),
    sqlite_autoincrement=True,
)


__all__ = [
    "metadata",
    "users",
    "schools",
    "boards",
    "board_members",
    "discussions",
    "replies",
    "discussion_saves",
    "discussion_follows",
    "user_follows",
    "notifications",
    "conversations",
    "direct_messages",
    "attachments",
    "file_categories",
    "file_resources",
    "file_favorites",
    "file_ratings",
    "file_downloads",
    "reports",
    "moderation_actions",
    "bans",
    "sessions",
    "oidc_identities",
    "oidc_login_transactions",
    "oidc_claim_tickets",
    "email_verification_tokens",
    "password_reset_tokens",
    "outbox_events",
    "feedback_projects",
    "feedback_project_members",
    "feedback_items",
    "feedback_comments",
    "feedback_api_keys",
    "tasks",
    "task_comments",
    "qr_login_tickets",
    "qr_login_confirmation_codes",
    "app_settings",
]
