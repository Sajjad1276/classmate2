"""Persist reusable class invite links and recurring group reminders."""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009_group_invite_reminders"
down_revision: Union[str, None] = "0008_user_activity_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "classes",
        sa.Column("member_join_code_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "classes",
        sa.Column("member_invite_reminder_next_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "classes",
        sa.Column("member_invite_reminder_last_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_classes_member_invite_reminder_next_at",
        "classes",
        ["member_invite_reminder_next_at"],
    )
    op.add_column(
        "reminder_jobs",
        sa.Column("button_text", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "reminder_jobs",
        sa.Column("button_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reminder_jobs", "button_url")
    op.drop_column("reminder_jobs", "button_text")
    op.drop_index("ix_classes_member_invite_reminder_next_at", table_name="classes")
    op.drop_column("classes", "member_invite_reminder_last_sent_at")
    op.drop_column("classes", "member_invite_reminder_next_at")
    op.drop_column("classes", "member_join_code_token")
