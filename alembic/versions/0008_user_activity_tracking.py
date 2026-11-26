"""Track first private start and recent private bot activity.

Revision ID: 0008_user_activity_tracking
Revises: 0007_class_member_invite_codes
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008_user_activity_tracking"
down_revision: Union[str, None] = "0007_class_member_invite_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("first_private_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_last_seen_at", "users", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_users_last_seen_at", table_name="users")
    op.drop_column("users", "last_seen_at")
    op.drop_column("users", "first_private_started_at")
