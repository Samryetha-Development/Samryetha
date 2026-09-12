"""Bind OIDC authorization codes to authentication assurance.

Revision ID: 0004_oidc_assurance
Revises: 0003_mfa_attempt_limit
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_oidc_assurance"
down_revision = "0003_mfa_attempt_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("authorization_codes", sa.Column("assurance_level", sa.String(length=8), nullable=False, server_default="AAL1"))
    op.add_column("authorization_codes", sa.Column("authentication_method", sa.String(length=64), nullable=False, server_default="PASSWORD"))
    op.add_column("authorization_codes", sa.Column("auth_time", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE authorization_codes SET auth_time = created_at WHERE auth_time IS NULL")


def downgrade() -> None:
    op.drop_column("authorization_codes", "auth_time")
    op.drop_column("authorization_codes", "authentication_method")
    op.drop_column("authorization_codes", "assurance_level")
