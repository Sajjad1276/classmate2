"""add performance indexes for navigation and notifications

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_class_memberships_user_active_class",
        "class_memberships",
        ["user_id", "is_active", "class_id"],
    )
    op.create_index(
        "ix_school_memberships_user_active_school",
        "school_memberships",
        ["user_id", "is_active", "school_id"],
    )
    op.create_index(
        "ix_announcement_class_active_published",
        "announcements",
        ["class_id", "is_active", "published_at"],
    )
    op.create_index(
        "ix_attendance_student_date",
        "attendance",
        ["student_id", "attendance_date"],
    )
    op.create_index(
        "ix_poll_response_user_poll",
        "poll_responses",
        ["user_id", "poll_id"],
    )
    op.create_index(
        "ix_reminder_target_status_scheduled",
        "reminder_jobs",
        ["target_id", "status", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reminder_target_status_scheduled", table_name="reminder_jobs")
    op.drop_index("ix_poll_response_user_poll", table_name="poll_responses")
    op.drop_index("ix_attendance_student_date", table_name="attendance")
    op.drop_index("ix_announcement_class_active_published", table_name="announcements")
    op.drop_index("ix_school_memberships_user_active_school", table_name="school_memberships")
    op.drop_index("ix_class_memberships_user_active_class", table_name="class_memberships")
