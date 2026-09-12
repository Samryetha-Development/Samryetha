"""Add TOTP, recovery-code, and step-up state.

Revision ID: 0002_mfa
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_mfa"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credentials", sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("assurance_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "authentication_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_authentication_challenges_token_hash", "authentication_challenges", ["token_hash"])
    op.create_index("ix_authentication_challenges_user_id", "authentication_challenges", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_authentication_challenges_user_id", table_name="authentication_challenges")
    op.drop_index("ix_authentication_challenges_token_hash", table_name="authentication_challenges")
    op.drop_table("authentication_challenges")
    op.drop_column("sessions", "assurance_verified_at")
    op.drop_column("credentials", "enabled_at")
