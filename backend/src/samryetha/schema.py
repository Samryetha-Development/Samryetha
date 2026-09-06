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
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

# ---------------------------------------------------------------- helpers


def _ms(name: str) -> Column:
    return Column(name, BigInteger)


def _soft_delete() -> list[Column]:
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
    Column("board_id", ForeignKey("boards.id"), primary_key=True, nullable=False),
    Column("user_id", ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("role", Text, nullable=False, server_default="member"),  # member|moderator
    _ms("joined_at"),
    Index("board_members_user_idx", "user_id"),
)

# ---------------------------------------------------------------- discussions

discussions = Table(
    "discussions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("board_id", ForeignKey("boards.id"), nullable=False),
    Column("author_id", ForeignKey("users.id"), nullable=False),
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

replies = Table(
    "replies",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("discussion_id", ForeignKey("discussions.id"), nullable=False),
    Column("author_id", ForeignKey("users.id"), nullable=False),
    Column("parent_reply_id", ForeignKey("replies.id")),  # 自引用，表级声明
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
    Column("user_id", ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("discussion_id", ForeignKey("discussions.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    Index("discussion_saves_discussion_idx", "discussion_id"),
)

discussion_follows = Table(
    "discussion_follows",
    metadata,
    Column("user_id", ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("discussion_id", ForeignKey("discussions.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    Index("discussion_follows_discussion_idx", "discussion_id"),
)

user_follows = Table(
    "user_follows",
    metadata,
    Column("follower_id", ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("followee_id", ForeignKey("users.id"), primary_key=True, nullable=False),
    _ms("created_at"),
    CheckConstraint("follower_id <> followee_id", name="user_follows_no_self"),
    Index("user_follows_followee_idx", "followee_id"),
)

# ---------------------------------------------------------------- notifications

notifications = Table(
    "notifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("actor_user_id", ForeignKey("users.id")),
    Column("type", Text, nullable=False),  # reply|mention|follow|system|moderation|ban
    Column("discussion_id", ForeignKey("discussions.id")),
    Column("reply_id", ForeignKey("replies.id")),
    Column("body", Text),
    Column("is_read", Integer, nullable=False, server_default="0"),
    _ms("read_at"),
    _ms("created_at"),
    Index("notifications_user_read_created_idx", "user_id", "is_read", "created_at"),
    Index("notifications_user_created_idx", "user_id", "created_at"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- attachments

attachments = Table(
    "attachments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("uploader_id", ForeignKey("users.id"), nullable=False),
    Column("discussion_id", ForeignKey("discussions.id")),
    Column("object_key", Text, nullable=False, unique=True),
    Column("original_filename", Text, nullable=False),
    Column("mime_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", Text),
    Column("state", Text, nullable=False, server_default="pending"),  # pending|attached|orphaned
    _ms("created_at"),
    Index("attachments_uploader_created_idx", "uploader_id", "created_at"),
    Index("attachments_discussion_idx", "discussion_id"),
    Index("attachments_state_idx", "state"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- moderation

reports = Table(
    "reports",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reporter_user_id", ForeignKey("users.id"), nullable=False),
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
    Column("actor_user_id", ForeignKey("users.id"), nullable=False),
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
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("banned_by_user_id", ForeignKey("users.id"), nullable=False),
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
    Column("user_id", ForeignKey("users.id"), nullable=False),
    _ms("expires_at"),
    Column("ip", Text),
    Column("user_agent", Text),
    _ms("created_at"),
    _ms("last_seen_at"),
    Index("sessions_user_idx", "user_id"),
    Index("sessions_expires_idx", "expires_at"),
)

# ---------------------------------------------------------------- tokens

email_verification_tokens = Table(
    "email_verification_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", ForeignKey("users.id"), unique=True, nullable=False),
    Column("token_hash", Text, unique=True, nullable=False),
    _ms("expires_at"),
    _ms("created_at"),
    sqlite_autoincrement=True,
)

password_reset_tokens = Table(
    "password_reset_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
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
    Column("status", Text, nullable=False, server_default="pending"),  # pending|processing|done|failed
    Column("attempts", Integer, nullable=False, server_default="0"),
    _ms("available_at"),
    _ms("created_at"),
    _ms("processed_at"),
    Index("outbox_status_available_idx", "status", "available_at", "id"),
    sqlite_autoincrement=True,
)

# ---------------------------------------------------------------- feedback

feedback_projects = Table(
    "feedback_projects",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("created_by_user_id", ForeignKey("users.id")),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("feedback_projects_created_idx", "created_at"),
    sqlite_autoincrement=True,
)

feedback_project_members = Table(
    "feedback_project_members",
    metadata,
    Column("project_id", ForeignKey("feedback_projects.id"), primary_key=True, nullable=False),
    Column("user_id", ForeignKey("users.id"), primary_key=True, nullable=False),
    Column("is_programmer", Integer, nullable=False, server_default="0"),
    _ms("joined_at"),
    Index("feedback_project_members_user_idx", "user_id"),
)

feedback_items = Table(
    "feedback_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", ForeignKey("feedback_projects.id"), nullable=False),
    Column("author_id", ForeignKey("users.id"), nullable=False),
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
    Column("item_id", ForeignKey("feedback_items.id"), nullable=False),
    Column("author_id", ForeignKey("users.id"), nullable=False),
    Column("parent_comment_id", ForeignKey("feedback_comments.id")),  # 自引用，嵌套评论
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

# 开发任务追踪（独立于 feedback）：公开可读、登录可写，分组(category)+优先级(priority)。
tasks = Table(
    "tasks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("author_id", ForeignKey("users.id"), nullable=False),
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
    Column("user_a_id", ForeignKey("users.id"), nullable=False),
    Column("user_b_id", ForeignKey("users.id"), nullable=False),
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
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("sender_id", ForeignKey("users.id"), nullable=False),
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
    "reports",
    "moderation_actions",
    "bans",
    "sessions",
    "email_verification_tokens",
    "password_reset_tokens",
    "outbox_events",
    "feedback_projects",
    "feedback_project_members",
    "feedback_items",
    "feedback_comments",
    "feedback_api_keys",
    "tasks",
    "app_settings",
]
