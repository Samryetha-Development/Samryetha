"""i18n 服务数据库 schema（SQLite，SQLAlchemy Core）。

两张主表：
  catalog_entries  — 管理员维护的"标准翻译条目"（locale + key + value）。
  submissions      — 用户提交的翻译建议，带审核状态。

时间戳：epoch 毫秒整数（与主站保持一致）。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


def _ms(name: str) -> Column:
    return Column(name, BigInteger)


# ---------------------------------------------------------------- catalog_entries

catalog_entries = Table(
    "catalog_entries",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("locale", Text, nullable=False),        # e.g. "zh-CN"
    Column("key", Text, nullable=False),           # 点分命名空间 e.g. "nav.home"
    Column("value", Text, nullable=False),         # 翻译文本
    Column("description", Text),                   # 可选说明
    _ms("created_at"),
    _ms("updated_at"),
    UniqueConstraint("locale", "key", name="uq_catalog_locale_key"),
    Index("ix_catalog_locale", "locale"),
)

# ---------------------------------------------------------------- submissions

submissions = Table(
    "submissions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("locale", Text, nullable=False),
    Column("key", Text, nullable=False),
    Column("value", Text, nullable=False),         # 提交的翻译文本
    Column("note", Text),                          # 提交者附注
    Column("submitter_id", Integer, nullable=False),  # users.id（外部引用，不建外键避免跨库问题）
    Column("submitter_name", Text, nullable=False),   # 冗余存储，展示用
    # status: pending | approved | rejected
    Column("status", Text, nullable=False, server_default="pending"),
    Column("reviewer_id", Integer),                   # 审核者 users.id
    Column("reviewer_name", Text),
    Column("review_note", Text),
    _ms("reviewed_at"),
    _ms("created_at"),
    _ms("updated_at"),
    CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_submission_status"),
    Index("ix_submissions_status", "status"),
    Index("ix_submissions_submitter", "submitter_id"),
    Index("ix_submissions_locale_key", "locale", "key"),
)
