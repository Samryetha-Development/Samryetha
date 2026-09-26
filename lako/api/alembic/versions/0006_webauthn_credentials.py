"""Passkey / WebAuthn credentials.

Revision ID: 0006_webauthn_credentials
Revises: 0005_email_tokens
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_webauthn_credentials"
down_revision = "0005_email_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "webauthn_credentials" not in inspector.get_table_names():
        op.create_table(
            "webauthn_credentials",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("credential_id", sa.LargeBinary(), nullable=False),
            sa.Column("public_key", sa.LargeBinary(), nullable=False),
            sa.Column("sign_count", sa.Integer(), nullable=False),
            sa.Column("transports", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("aaguid", sa.String(length=64), nullable=True),
            sa.Column("backed_up", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("credential_id"),
        )
        op.create_index(
            "ix_webauthn_credentials_credential_id", "webauthn_credentials", ["credential_id"]
        )
        op.create_index("ix_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "webauthn_credentials" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("webauthn_credentials")}
    if "ix_webauthn_credentials_user_id" in indexes:
        op.drop_index("ix_webauthn_credentials_user_id", table_name="webauthn_credentials")
    if "ix_webauthn_credentials_credential_id" in indexes:
        op.drop_index("ix_webauthn_credentials_credential_id", table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
