"""Email-delivered one-time codes (sign-in / second factor / step-up / address verification).

These codes are stored on the existing ``authentication_challenges`` table, so
this revision adds no table: it only widens ``purpose`` for the two new values
(``EMAIL_OTP``, ``EMAIL_VERIFY``) and keeps the read path fast for the lookup
that every redemption performs (user + purpose + unused, newest first).

Revision ID: 0007_email_codes
Revises: 0006_webauthn_credentials
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_email_codes"
down_revision = "0006_webauthn_credentials"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_auth_challenge_user_purpose"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "authentication_challenges" not in inspector.get_table_names():
        return
    # Widen purpose for the new code flows. SQLite ignores VARCHAR lengths, but
    # PostgreSQL enforces them and would reject the longer value on insert.
    purpose = next(
        (column for column in inspector.get_columns("authentication_challenges") if column["name"] == "purpose"),
        None,
    )
    if purpose is not None and getattr(purpose["type"], "length", None) not in (None, 32):
        with op.batch_alter_table("authentication_challenges") as batch:
            batch.alter_column("purpose", type_=sa.String(length=32), existing_nullable=False)
    existing = {index["name"] for index in inspector.get_indexes("authentication_challenges")}
    if INDEX_NAME not in existing:
        op.create_index(
            INDEX_NAME, "authentication_challenges", ["user_id", "purpose", "used_at"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "authentication_challenges" not in inspector.get_table_names():
        return
    existing = {index["name"] for index in inspector.get_indexes("authentication_challenges")}
    if INDEX_NAME in existing:
        op.drop_index(INDEX_NAME, table_name="authentication_challenges")
