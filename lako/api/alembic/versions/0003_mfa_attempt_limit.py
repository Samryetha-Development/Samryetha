"""Limit MFA challenge attempts.

Revision ID: 0003_mfa_attempt_limit
Revises: 0002_mfa
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_mfa_attempt_limit"
down_revision = "0002_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "authentication_challenges",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("authentication_challenges", "attempt_count")
