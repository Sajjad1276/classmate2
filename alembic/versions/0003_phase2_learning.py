"""Ensure phase-two learning tables exist on legacy schema installations."""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _phase_two_tables():
    from classmate.db.schema_v0001 import (
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
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    # These tables may have existed before 0003 because of create_all bootstrap.
    # A blind downgrade could delete learner work, grades and attendance.
    pass
