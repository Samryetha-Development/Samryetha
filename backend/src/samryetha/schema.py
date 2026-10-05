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
    # 资料文本（display_name/bio）的审核状态；pending 时对外仍展示旧资料。
    Column("profile_moderation_status", Text, nullable=False, server_default="approved"),
    # 待审的新资料。机器只标记，所以资料改动先落这里，display_name/bio 始终保持"上一次通过"
    # 的值（规则 §31：待审期间对外展示旧资料）。判定放行才提升为主字段；驳回则留在
    # pending_* 里——失败原文因此既不对外可见，也没有被删除，只有管理员能从留存库调取。
    Column("pending_display_name", Text),
    Column("pending_bio", Text),
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
    # 审核状态：pending=待审（仅作者与版主可见）|approved|rejected。默认 approved，
    # 存量行与未启用审核的部署因此不受影响。
    Column("moderation_status", Text, nullable=False, server_default="approved"),
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
    Column("moderation_status", Text, nullable=False, server_default="approved"),
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
    Column("uploader_id", ForeignKey("users.id"), nullable=False),
    Column("discussion_id", ForeignKey("discussions.id")),
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

# 审核队列：一切"先审后发"的内容都先落在这里，人（或后续的自动策略）再放行/驳回。
#
# 设计要点：
#   - **不存正文副本**，只存 (content_type, content_id) 指针 + 判定快照。正文已经写在
#     各自的表里（discussions/replies/direct_messages/...），复制一份必然会漂移。
#   - decision 记录**自动判定**（allow|review|block），review_state 记录**人的决定**
#     （pending|approved|rejected）。两者分开，才能事后统计"模型判错了多少"。
#   - signals 存 JSON：命中哪条规则、模型返回什么、耗时多久——申诉与调参的唯一依据。
#   - **机器只标记，不定案**：review/block 都只把内容压成 pending 并设 hold_until。
#     版主在窗口内定案即为最终结果；逾期未定案由 AI 复审先行处置，写进 resolution，
#     review_state 保持 pending，人工随时可以推翻（overturned=1）。
moderation_queue = Table(
    "moderation_queue",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("content_type", Text, nullable=False),  # discussion|reply|profile|message|attachment
    Column("content_id", Integer, nullable=False),
    Column("author_id", ForeignKey("users.id"), nullable=False),
    # 提交审核时的内容摘要（标题+正文前若干字），列表页不必回表拼正文。
    Column("excerpt", Text, nullable=False, server_default=""),
    # 自动判定：allow（直接放行）|review（转人工）|block（自动驳回）
    Column("decision", Text, nullable=False, server_default="review"),
    # 置信度 0-100：规则命中给固定分，模型给 0-100。列表按它倒序，先看最可疑的。
    Column("score", Integer, nullable=False, server_default="0"),
    Column("signals", Text, nullable=False, server_default="{}"),  # JSON
    # 人工决定：pending|approved|rejected。机器只标记，所以始终先落 pending；
    # 只有人（或 AI 复审的先行处置）才会把它推向 approved/rejected。
    Column("review_state", Text, nullable=False, server_default="pending"),
    Column("reviewer_id", ForeignKey("users.id")),
    Column("review_note", Text),
    _ms("reviewed_at"),
    _ms("created_at"),
    # ---------------------------------------------------------------- 确认窗口 + AI 复审
    # 人工确认窗口的截止时间（毫秒）。机器判定为 review/block 时内容先压住（pending），
    # 版主可在此之前定案；逾期由 AI 复审并先行处置（见 automod.finalize_pending）。
    _ms("hold_until"),
    # 已执行的最终/先行动作：
    #   NULL                = 还在确认窗口内，等人定案
    #   published_by_ai     = 1 分钟超时，AI 复审放行 → 先行公开（人工可推翻为封禁）
    #   published_by_human  = 人工放行（窗口内定案，或事后推翻 AI 封禁、重新放行）
    #   blocked             = AI 复审未放行而先行封禁，或人工驳回/推翻 —— 仅管理员可见
    # 谁处置的看 reviewer_id：为空 = 机器（AI 复审）先行处置，非空 = 人工定案。
    Column("resolution", Text),
    _ms("resolved_at"),
    # AI 复审快照（JSON）：第二次判定、理由、命中规则与时间，以及是否据此放行。
    # 复审结论是决定性的（"必需初审和复审都放行才放行"，实际由复审定夺），但仍是
    # **先行**结论——人工可以维持或推翻。申诉调取的就是这一列。
    Column("recheck", Text, nullable=False, server_default=""),
    # 提交时送审文本的**快照**。留存库必须读它，不能读内容表当前值：帖子/回复删掉就
    # 读不到了，个人资料更是原地更新——用户再改一次简介，被拒原文就永远找不回来，
    # "全部留存"会名不副实（见 PR #70 审查意见 #9）。
    Column("submitted_text", Text, nullable=False, server_default=""),
    # 历史版本只供留存查询，不能再把旧结论写回当前内容。
    _ms("superseded_at"),
    # 人工是否推翻了 AI 的先行处置（1=推翻，0=维持）。没有 AI 先行处置时为 0。
    Column("overturned", Integer, nullable=False, server_default="0"),
    Index("moderation_queue_state_created_idx", "review_state", "created_at"),
    Index("moderation_queue_content_idx", "content_type", "content_id"),
    Index("moderation_queue_author_idx", "author_id", "created_at"),
    # 复审 worker 的扫描条件就是 (review_state='pending', hold_until<=now)。
    Index("moderation_queue_hold_idx", "review_state", "hold_until"),
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

oidc_identities = Table(
    "oidc_identities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
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
    Column("approved_by", ForeignKey("users.id")),
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
    Column("user_id", ForeignKey("users.id"), nullable=False),
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
    # 租约制回收：claim(pending→processing)时写入，worker崩溃/超时后可扫回 pending。
    # Lease for crash recovery: set on claim; stale processing rows are swept back to pending.
    _ms("processing_at"),
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

# 开发任务追踪（独立于 feedback）：仅管理员可见，分组(category)+优先级(priority)。
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

task_comments = Table(
    "task_comments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", ForeignKey("tasks.id"), nullable=False),
    Column("author_id", ForeignKey("users.id"), nullable=False),
    Column("parent_comment_id", ForeignKey("task_comments.id")),  # 自引用，嵌套评论
    Column("body", Text, nullable=False),
    *_soft_delete(),
    _ms("created_at"),
    _ms("updated_at"),
    Index("task_comments_task_created_idx", "task_id", "created_at"),
    Index("task_comments_parent_idx", "parent_comment_id"),
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
    Column("moderation_status", Text, nullable=False, server_default="approved"),
    _ms("read_at"),
    _ms("created_at"),
    Index("direct_messages_conversation_idx", "conversation_id", "created_at"),
    sqlite_autoincrement=True,
)


# ---------------------------------------------------------------- i18n

i18n_catalog = Table(
    "i18n_catalog",
    metadata,
    Column("key", Text, primary_key=True),
    Column("source_lang", Text, nullable=False, server_default="en"),
    Column("value", Text, nullable=False),
    Column("context", Text),
    _ms("created_at"),
    _ms("updated_at"),
)

i18n_submissions = Table(
    "i18n_submissions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", Text, ForeignKey("i18n_catalog.key"), nullable=False),
    Column("lang", Text, nullable=False),
    Column("value", Text, nullable=False),
    Column("note", Text),
    Column("status", Text, nullable=False, server_default="pending"),  # pending|approved|rejected
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("reviewer_id", ForeignKey("users.id")),
    Column("reject_reason", Text),
    _ms("submitted_at"),
    _ms("reviewed_at"),
    Index("i18n_submissions_key_lang_idx", "key", "lang"),
    Index("i18n_submissions_user_idx", "user_id"),
    Index("i18n_submissions_status_idx", "status"),
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
    "moderation_queue",
    "app_settings",
    "i18n_catalog",
    "i18n_submissions",
]
