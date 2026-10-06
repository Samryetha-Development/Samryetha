"""Email verification / password-reset / invite tokens.

Revision ID: 0005_email_tokens
Revises: 0004_oidc_assurance
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_email_tokens"
down_revision = "0004_oidc_assurance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "email_tokens" not in inspector.get_table_names():
        op.create_table(
            "email_tokens",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("identity_id", sa.Uuid(), nullable=True),
            sa.Column("purpose", sa.String(length=16), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["identity_id"], ["identities.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index("ix_email_tokens_token_hash", "email_tokens", ["token_hash"])
        op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "email_tokens" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("email_tokens")}
    if "ix_email_tokens_user_id" in indexes:
        op.drop_index("ix_email_tokens_user_id", table_name="email_tokens")
    if "ix_email_tokens_token_hash" in indexes:
        op.drop_index("ix_email_tokens_token_hash", table_name="email_tokens")
    op.drop_table("email_tokens")
