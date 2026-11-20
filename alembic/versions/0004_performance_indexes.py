"""add performance indexes for navigation and notifications

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None]

INDEXES = (
    ("ix_class_memberships_user_active_class", "class_memberships", ("user_id", "is_active", "class_id")),
    ("ix_school_memberships_user_active_school", "school_memberships", ("user_id", "is_active", "school_id")),
    ("ix_announcement_class_active_published", "announcements", ("class_id", "is_active", "published_at")),
    ("ix_attendance_student_date", "attendance", ("student_id", "attendance_date")),
    ("ix_poll_response_user_poll", "poll_responses", ("user_id", "poll_id")),
    ("ix_reminder_target_status_scheduled", "reminder_jobs", ("target_id", "status", "scheduled_at")),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for name, table, columns in INDEXES_:
        if not inspector.has_table(table):
            continue
        existing = {item["name"] for item in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, list(columns))
            inspector = inspect(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for name, table, _columns in reversed(INDEXES):
        if not inspector.has_table(table):
            continue
        existing = {item["name"] for item in inspector.get_indexes(table)}
        if name in existing:
            op.drop_index(name, table_name=table)
            inspector = inspect(bind)
