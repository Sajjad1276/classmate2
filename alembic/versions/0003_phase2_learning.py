"""add learning workflow tables for phase two

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _phase_two_tables():
    from classmate.db.models import (
        Attendance,
        Grade,
        HomeworkSubmission,
        Poll,
        PollOption,
        PollResponse,
    )

    return (
        HomeworkSubmission.__table__,
        Grade.__table__,
        Attendance.__table__,
        Poll.__table__,
        PollOption.__table__,
        PollResponse.__table__,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for table in _phase_two_tables():
        # 0001 currently bootstraps the full ORM metadata on new installs.
        # Older databases may have only the original core schema, so create
        # each missing table independently and idempotently.
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in reversed(_phase_two_tables()):
        if inspector.has_table(table.name):
            table.drop(bind=bind, checkfirst=True)
            inspector = inspect(bind)
