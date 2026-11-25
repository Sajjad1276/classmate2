"""Create one-time invitation codes bound to a student and a class.

Revision ID: 0007_class_member_invite_codes
Revises: 0006
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007_class_member_invite_codes"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "class_member_invite_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("class_id", "user_id", name="uq_class_member_invite_code_user_class"),
    )
    op.create_index("ix_class_member_invite_codes_class_id", "class_member_invite_codes", ["class_id"])
    op.create_index("ix_class_member_invite_codes_user_id", "class_member_invite_codes", ["user_id"])
    op.create_index("ix_class_member_invite_codes_expiry", "class_member_invite_codes", ["expires_at", "used_at"])


def downgrade() -> None:
    op.drop_index("ix_class_member_invite_codes_expiry", table_name="class_member_invite_codes")
    op.drop_index("ix_class_member_invite_codes_user_id", table_name="class_member_invite_codes")
    op.drop_index("ix_class_member_invite_codes_class_id", table_name="class_member_invite_codes")
    op.drop_table("class_member_invite_codes")
